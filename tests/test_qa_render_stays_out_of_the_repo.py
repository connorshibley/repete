"""scripts/qa_render.py must not be able to resolve into a live tree.

Sibling to tests/test_artifacts_stay_out_of_the_repo.py, and for the same
reason: the rendered artifacts are GITIGNORED, so a bad write leaves
`git status --porcelain` clean and src/deploycheck.py structurally cannot see
it. On 2026-07-28 that blindness let test fixtures sit in the repo root as
blog.html and journal.html until the next scheduled cycle nearly published
them over an eleven-day public archive.

qa_render is a bigger hazard than the renderers it calls, because its whole
job is to render from a config that points somewhere unusual — one wrong
--out and it writes the published dashboard from synthetic data.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(REPO, "scripts"))

import qa_fixture  # noqa: E402
import qa_render  # noqa: E402


def _roots():
    return qa_fixture._repo_roots()


@pytest.mark.parametrize("sub", ["", "memory", ".site", "publisher_data"])
def test_it_refuses_every_live_directory(sub):
    """Assert on the guard function, never by attempting the write: a guard
    test that only fails destructively is a trap (see the 2026-08-11 note in
    tests/test_qa_fixture_shapes.py)."""
    for root in _roots():
        dest = os.path.join(root, sub) if sub else root
        with pytest.raises(SystemExit) as e:
            qa_render._guard_out_dir(dest)
        assert "REFUSING" in str(e.value)


def test_the_main_checkout_is_covered_not_just_this_one():
    """From a worktree, `__file__/..` is the worktree — so a guard written
    that way protects a directory nobody publishes from while the real
    published mirror stays open. That exact bug shipped nine fixture files
    into the live .site/ on 2026-08-11."""
    roots = [os.path.realpath(r) for r in _roots()]
    assert REPO in roots
    if os.path.isfile(os.path.join(REPO, ".git")):
        assert len(roots) == 2, "main checkout not discovered from the worktree"


def test_a_scratch_directory_is_allowed(tmp_path):
    """Negative control. If the refusal were unconditional every assertion
    above would pass for the wrong reason."""
    qa_render._guard_out_dir(str(tmp_path / "site"))


def test_the_config_it_builds_never_points_at_the_live_stores(tmp_path):
    fixture, out = str(tmp_path / "fx"), str(tmp_path / "out")
    os.makedirs(fixture)
    cfg = qa_render.build_cfg(fixture, out)
    assert cfg["memory"]["ledger_path"].startswith(fixture)
    assert cfg["memory"]["learnings_path"].startswith(fixture)
    assert cfg["learning"]["lessons_path"].startswith(fixture)
    assert cfg["learning"]["judgments_path"].startswith(fixture)
    assert cfg["x_posting"]["posts_log_path"].startswith(fixture)
    assert cfg["x_posting"]["journal_path"].startswith(fixture)
    assert cfg["publish"]["out_dir"] == out


def test_the_owners_handle_is_stripped_from_the_journal_url():
    """config.yaml's journal_url_base carries a personal GitHub handle into
    every rendered permalink, and QA output gets attached to bug reports."""
    cfg = qa_render.build_cfg("/tmp/nope", "/tmp/nope-out")
    assert "connorshibley" not in cfg["x_posting"]["journal_url_base"]
    assert "github.io" not in cfg["x_posting"]["journal_url_base"]


def test_the_storage_backend_is_pinned_to_the_fixtures_format():
    """dashboard.render() never calls store.configure(), so the process-wide
    JSONL default is what reads the fixture. Pinned so a config change to
    storage.backend cannot silently point the renderers at an empty SQLite
    database and render a blank dashboard that looks like a product state."""
    cfg = qa_render.build_cfg("/tmp/nope", "/tmp/nope-out")
    assert cfg["storage"]["backend"] == "jsonl"


def test_it_renders_the_published_layout_by_default(tmp_path):
    """publish_dashboard.sh renames dashboard.html -> index.html, and blog.py
    and journal.py both link to index.html. Rendering dashboard.html and
    calling it done tests a layout that never ships."""
    fx, out = tmp_path / "fx", tmp_path / "site"
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "qa_fixture.py"),
                    "--out", str(fx), "--days", "6", "--anchor", "2026-08-11"],
                   capture_output=True, text=True, check=True)
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "qa_render.py"),
                        "--fixture", str(fx), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (out / "index.html").exists()
    assert not (out / "dashboard.html").exists()
    # The sidecar must land beside its HTML — dashboard.py derives its path
    # from dirname(out_path), so an out_dir mismatch silently splits them.
    assert (out / "dashboard_data.json").exists()


def test_backdating_moves_the_stamp_in_both_places(tmp_path):
    """The badge reads data-gen from the HTML; the poll compares generated_at
    in the sidecar. Ageing one and not the other produces a state the real
    page can never be in."""
    import json
    import re
    fx, out = tmp_path / "fx", tmp_path / "site"
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "qa_fixture.py"),
                    "--out", str(fx), "--days", "6", "--anchor", "2026-08-11"],
                   capture_output=True, text=True, check=True)
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "qa_render.py"),
                    "--fixture", str(fx), "--out", str(out), "--age-hours", "25"],
                   capture_output=True, text=True, check=True)
    stamp = re.search(r'data-gen="([^"]+)"', (out / "index.html").read_text())
    assert stamp
    assert json.loads((out / "dashboard_data.json").read_text())[
        "generated_at"] == stamp.group(1)
