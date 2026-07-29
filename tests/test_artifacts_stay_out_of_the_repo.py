"""No test may write a public artifact into the repo root.

On 2026-07-28 the repo-root `blog.html` and `journal.html` were silently
replaced by test fixtures — 2,456 bytes where the published copy held 16,570,
and 2,533 where it held 62,354. `scripts/publish_dashboard.sh` pushes on any
difference from the `.site/` twin, so the next scheduled cycle would have
published four duplicate fake posts over an eleven-day public archive.

Nothing in the system could have caught it. The artifacts are gitignored, so
`git status --porcelain` stayed clean and `src/deploycheck.py` — which reads
exactly that — is structurally blind to them.

The cause was never one bad test. All nine production render call sites omitted
`out_path`, so every one of them resolved against the process working
directory; running pytest from the repo root was sufficient. The fix routes
every renderer through `sitepaths.resolve()`, and this file is the guard that
keeps it routed.
"""
import os

import blog
import dashboard
import journal
import sitepaths

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTIFACTS = ("dashboard.html", "dashboard_data.json", "blog.html",
             "journal.html")


def test_the_cfg_fixture_never_resolves_into_the_repo_root(cfg):
    """The fixture's promise, enforced rather than asserted in a docstring."""
    for name in ARTIFACTS:
        resolved = os.path.abspath(sitepaths.resolve(cfg, name))
        assert os.path.dirname(resolved) != REPO_ROOT, (
            f"{name} resolves to the repo root under the test fixture")


def test_every_renderer_honours_out_dir(cfg):
    """A render with no explicit out_path lands in the configured directory.

    This is the specific failure: `blog.render(cfg)` with no second argument
    is what `backfill_posts.run()` called, nine times over across the codebase.
    """
    for mod in (blog, journal, dashboard):
        written = mod.render(cfg)
        assert os.path.exists(written)
        assert os.path.abspath(written).startswith(
            os.path.abspath(sitepaths.out_dir(cfg)))
        assert os.path.dirname(os.path.abspath(written)) != REPO_ROOT


def test_dashboard_data_json_follows_its_html(cfg):
    """dashboard_data.json is what the published page POLLS. If it were left
    behind in the CWD while the HTML moved, the live badge on the deployed site
    would drift amber then red beside perfectly current markup."""
    written = dashboard.render(cfg)
    beside = os.path.join(os.path.dirname(written), dashboard.DATA_PATH)
    assert os.path.exists(beside), "dashboard_data.json did not follow the HTML"


def test_an_explicit_out_path_still_wins(cfg, tmp_path):
    """Callers that pass a path keep exact control — the config is a default,
    not an override. Every existing test relies on this."""
    target = str(tmp_path / "explicit" / "blog.html")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    assert blog.render(cfg, out_path=target) == target
    assert os.path.exists(target)


def test_out_dir_defaults_to_cwd_for_production():
    """Production must keep writing to the repo root: run_cycle.sh cd's there
    before rendering, and publish_dashboard.sh reads from there. A config
    without the key must behave exactly as the code did before it existed."""
    assert sitepaths.out_dir(None) == "."
    assert sitepaths.out_dir({}) == "."
    assert sitepaths.out_dir({"publish": {}}) == "."
    assert sitepaths.out_dir({"publish": {"out_dir": None}}) == "."
    assert sitepaths.resolve({}, "blog.html") == os.path.join(".", "blog.html")


def test_a_renderer_that_ignored_out_dir_would_fail_this_file(cfg):
    """Meta-assertion: the checks above are not vacuous.

    If `sitepaths.resolve` were stubbed to return the bare filename — exactly
    the pre-fix behaviour — the assertions in this file must break. Proven
    directly rather than trusted, because the whole incident was a guard that
    looked present and was not.
    """
    naive = os.path.abspath("blog.html")          # what the old default gave
    routed = os.path.abspath(sitepaths.resolve(cfg, "blog.html"))
    assert naive != routed, (
        "resolve() is returning the CWD-relative path — the fix is inert")
