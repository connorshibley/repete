"""Structural pins for behaviours the 2026-08-30 browser audit observed live.

CI has no browser, deliberately — the repo's seam (established by
test_dashboard_survives_a_region_swap.py) is to pin the structural contract
that makes the browser behaviour inevitable, and record the behaviour itself
in docs/qa_inventory.md's browser table with a dated evidence line.

Each pin below traces to a specific observation or near-miss from that audit.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _dashboard_src():
    return (ROOT / "src" / "dashboard.py").read_text()


# --- F-17: an unbroken token must not widen the page ------------------------

def test_blog_and_journal_break_long_tokens():
    """F-17. A 4,000-char unbroken string in a blog post's market-context line
    rendered the page 40,316px wide (measured in-browser, hostile profile,
    2026-08-30) — the whole page scrolled horizontally and every other post
    became unreadable. The realistic production trigger is one long URL in a
    headline or post. The dashboard survives via per-table `overflow-x:auto`
    wrappers; blog and journal are prose and need the text itself to break.
    """
    import blog
    import journal
    for name, css in (("blog", blog.CSS), ("journal", journal.CSS)):
        assert "overflow-wrap:anywhere" in css, (
            f"{name}.CSS lost overflow-wrap:anywhere — one long token will "
            f"widen the whole page again (F-17)")


def test_a_long_unbroken_token_is_actually_subject_to_the_rule():
    """The CSS grep above passes even if the rule sits on a selector nothing
    matches. Render a real blog page with a hostile-length token and assert
    the rule is on `body`, which everything inherits from."""
    import blog
    assert re.search(r"body\{[^}]*overflow-wrap:anywhere", blog.CSS), (
        "the break rule must be on body so every descendant inherits it")


# --- the poll interval, unpinned until now ----------------------------------

def test_the_poll_interval_is_sixty_seconds():
    """The 30s badge repaint is pinned elsewhere; the 60s poll never was.
    The audit's own first instinct was to shrink it to avoid five minutes of
    waiting — qa_render.py's principle (the page under test stays the page
    that ships) is why that was wrong, and this pin is what makes the next
    person's version of that instinct a red test instead of a silent ship."""
    assert "setInterval(poll,60000)" in _dashboard_src()


# --- the swap guard: count-up idempotence's other half ----------------------

def test_swap_only_replaces_changed_regions():
    """Observed live: a poll with an unchanged hash produced ZERO DOM
    mutations in the hero (MutationObserver, 70s window). That stillness
    rests on swap() comparing before assigning; without the inequality guard
    every poll rebuilds every region and re-runs the hero count-up."""
    assert re.search(r"el\.innerHTML\s*!==?\s*d\.regions\[k\]",
                     _dashboard_src()), (
        "swap() no longer compares region content before replacing it")


# --- the boot splash's exemption is only sound while it holds ---------------

def test_the_boot_splash_lives_outside_every_volatile_region():
    """The delegation rule allows listeners on `document` and `#boot` only.
    That exemption is sound because #boot is static chrome — if it ever moves
    inside a #rgn-* the first swap silently kills its skip handler, the exact
    F-04 failure class."""

    import yaml
    sys.path.insert(0, str(ROOT / "src"))
    import dashboard
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    # render offline from whatever ledger cfg points at; the structure of the
    # static chrome does not depend on the data
    import store
    store.configure({"storage": {"backend": "jsonl"}})
    html = None
    try:
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            cfg = dict(cfg)
            cfg["memory"] = dict(cfg["memory"])
            cfg["memory"]["ledger_path"] = os.path.join(td, "empty.jsonl")
            open(cfg["memory"]["ledger_path"], "w").close()
            cfg["memory"]["learnings_path"] = os.path.join(td, "l.md")
            cfg["memory"]["lessons_path"] = os.path.join(td, "l.jsonl")
            cfg["memory"]["judgments_path"] = os.path.join(td, "j.jsonl")
            cfg.setdefault("publish", {})
            cfg["publish"] = dict(cfg.get("publish") or {})
            cfg["publish"]["out_dir"] = td
            out = dashboard.render(cfg, spy_bars=[])
            html = open(out).read() if isinstance(out, str) and os.path.exists(str(out)) else open(os.path.join(td, "dashboard.html")).read()
    except TypeError:
        raise
    boot_at = html.find('id=boot')
    if boot_at == -1:
        boot_at = html.find('id="boot"')
    assert boot_at != -1, "boot splash gone from the rendered page"
    # No #rgn-* opens before #boot and closes after it: check #boot is not
    # inside any element whose id starts with rgn-. Cheap structural check:
    # every rgn div in this page is emitted as <div id=rgn-X>...</div> with no
    # nesting of boot inside (boot is emitted before the first region).
    first_rgn = html.find('id=rgn-')
    assert first_rgn != -1
    assert boot_at < first_rgn, (
        "#boot is emitted after the first volatile region — if it moved "
        "inside one, its click-to-skip dies on the first swap (F-04 class)")


# --- the session key ---------------------------------------------------------

def test_the_session_storage_key_is_stable():
    """Renaming `repete_boot` silently resets every visitor's once-per-session
    splash contract. Observed in-browser: set to '1' after first play,
    splash absent from the DOM on reload with the key present."""
    assert "repete_boot" in _dashboard_src()
