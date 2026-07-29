"""No public page may advertise a channel the bot does not use.

The defect
----------
X was switched off by owner decision on 2026-07-28 (`x_posting.enabled: false`;
the four `X_*` values in `.env` had already been empty since ~2026-07-24). Three
renderers kept linking to it anyway — six occurrences across `dashboard.py`,
`blog.py` and `journal.py`.

One of them was not a link but an assertion. `src/dashboard.py` rendered, on a
page anyone can load:

    The bot narrates its trades and reasoning at x.com/Repete2026.

That was false for five days. The account is not being written to; the
narration lives on the bot's own blog and journal, which is exactly where
`src/x_poster.py` archives every composed post (status `x_disabled`) before it
ever attempts delivery.

Why a test rather than just a fix
--------------------------------
The same defect already happened once in a different shape — `blog.render`
filtering on `status == "posted"` froze the public blog for three days while the
bot traded (see `test_blog_is_independent_of_x.py`). Both times the code was
correct about trading and wrong about what it told the reader, and both times
nothing failed: a stale claim on a web page does not raise.

So the assertion is structural and cheap: while X is off, no rendered page may
contain a link to it. If X is ever switched back on, this file is where the
decision gets re-examined — `test_the_guard_is_conditional_on_x_being_off`
exists so nobody has to delete a test to re-enable a feature.
"""
import os

import pytest

import blog
import dashboard
import journal
import sitepaths

# Everything that would mean "we still point at X". `@Repete2026` is checked
# separately from the domain because the handle appeared as bare text in the
# dashboard heading, with the URL on the following line.
X_MARKERS = ("x.com", "twitter.com", "@Repete2026", "on X ↗")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _x_off(cfg):
    cfg.setdefault("x_posting", {})["enabled"] = False
    return cfg


def _render_all(cfg, tmp_path):
    """Render all three pages into the fixture's out_dir and return the HTML.

    Uses `sitepaths.resolve` rather than a literal path — W0's finding was that
    nine call sites resolved against the PROCESS WORKING DIRECTORY and a pytest
    run from the repo root was enough to overwrite the published archive.
    """
    out = {}
    dashboard.render(cfg)
    blog.render(cfg)
    journal.render(cfg)
    for mod, name in ((dashboard, dashboard.OUT_PATH), (blog, blog.OUT_PATH),
                      (journal, journal.OUT_PATH)):
        path = sitepaths.resolve(cfg, name)
        with open(path) as f:
            out[name] = f.read()
    return out


# ---- the rendered pages ----

@pytest.mark.parametrize("marker", X_MARKERS)
def test_no_rendered_page_links_to_x(cfg, tmp_path, marker):
    for name, html in _render_all(_x_off(cfg), tmp_path).items():
        assert marker not in html, (
            f"{name} still advertises X ({marker!r}) while "
            f"x_posting.enabled is false")


def test_the_pages_still_link_to_each_other(cfg, tmp_path):
    """The permissive half. A renderer that emitted no links at all would
    satisfy every assertion above — and would be a worse page than the one
    being fixed."""
    pages = _render_all(_x_off(cfg), tmp_path)
    assert "journal.html" in pages[dashboard.OUT_PATH]
    assert "blog.html" in pages[dashboard.OUT_PATH]
    assert "journal.html" in pages[blog.OUT_PATH]
    assert "blog.html" in pages[journal.OUT_PATH]


def test_the_dashboard_names_where_the_narration_actually_is(cfg, tmp_path):
    """The sentence that was false. Its replacement has to be TRUE, not just
    X-free — deleting the claim and saying nothing would leave a reader unable
    to find the reasoning the bot does publish."""
    html = _render_all(_x_off(cfg), tmp_path)[dashboard.OUT_PATH]
    assert "narrates every trade" in html
    assert "blog.html" in html and "journal.html" in html


# ---- the source, so a future edit cannot re-add one ----

@pytest.mark.parametrize("mod", ["dashboard.py", "blog.py", "journal.py"])
def test_no_renderer_source_contains_an_x_url(mod):
    """Belt and braces on top of the render assertions. A link inside a branch
    the fixture does not exercise would pass the tests above and still ship."""
    with open(os.path.join(REPO, "src", mod)) as f:
        src = f.read()
    for marker in ("x.com/", "twitter.com/"):
        assert marker not in src, f"src/{mod} still hard-codes {marker}"


def test_the_guard_is_conditional_on_x_being_off():
    """Re-enabling X must not require deleting a test.

    This file's premise is `x_posting.enabled: false`. If that ever flips, the
    right move is to restore the links deliberately and narrow these
    assertions — NOT to quietly drop the file. Recorded here so the intent
    survives whoever finds it next.
    """
    import yaml
    with open(os.path.join(REPO, "config.yaml")) as f:
        shipped = yaml.safe_load(f)
    assert shipped["x_posting"]["enabled"] is False, (
        "config.yaml has re-enabled X. This file asserts the pages carry no "
        "X links, which was correct while it was off. Decide deliberately: "
        "restore the links and narrow this test, or leave X off.")
    assert shipped["x_posting"]["dry_run"] is True, (
        "W5-2 set dry_run: true so that BOTH flags must be turned over to "
        "post publicly. One of them has been flipped back.")
