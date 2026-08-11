"""The page must keep working after the poll replaces a region.

The dashboard's premise is that it stays current without a reload: LIVE_JS
polls its own sidecar every 60s and, on a changed hash, replaces the innerHTML
of ten volatile regions. Every interactive element lives inside one of them —
the filter chips in `decisions`, all the [data-tip] hover targets in the three
chart regions, the [data-count] figure in `hero`.

Handlers used to be attached per element at load, so the first poll destroyed
the elements they were bound to and filtering and every chart tooltip went
dead. Nothing surfaced it: no console error, and the chips still changed
colour on hover because that is CSS. Measured in a browser against a served
fixture — click a chip at t=0 and 7 rows filter to 1; click the same chip
after one poll and nothing happens, not even the .on class.

WHAT THESE TESTS ARE AND ARE NOT. They assert the page is BUILT so a swap
cannot orphan a handler. They do not execute JavaScript and therefore do not
prove the handlers fire — that is a browser criterion (SITE-BROWSER-01..03 in
docs/qa_inventory.md), verified by driving a real browser against a served
copy. An `assert "addEventListener" in JS` presented as behavioural proof is
the anti-pattern scripts/qa_sweep.py:391 already calls out; the structural
guard is worth having, but only labelled honestly.
"""
import re

import dashboard
from ledger import Ledger

# `boot` is the one legitimate non-document receiver: the splash is a
# full-page overlay rendered OUTSIDE every rgn-* mount point, so swap() cannot
# reach it, and it removes itself on click or after 3.4s.
ALLOWED_RECEIVERS = {"document", "boot"}


def _cfg_paths(cfg, tmp_path):
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "judgments.jsonl")
    return cfg


def _rendered(cfg, tmp_path):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("NVDA", "buy", "dip in uptrend", {"rsi2": 7},
                     {"verdict": "downsize", "scale": 0.7,
                      "reasoning": "extended", "bull_case": "trend",
                      "bear_case": "stretched"},
                     executed=True, entry_price=100.0, qty=5)
    led.log_decision("AAPL", "buy", "signal", {"rsi2": 9},
                     {"verdict": "veto", "scale": 0.0, "reasoning": "no"},
                     executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "dash.html"))
    return open(out).read()


def test_no_listener_is_bound_to_an_element_inside_a_volatile_region():
    """The regression guard for F-04 and F-05.

    Every listener must be attached to `document`, which swap() never touches.
    Counting RECEIVERS is the check: an earlier version grepped for
    querySelectorAll('.chip').forEach and flagged the loop that merely clears
    the .on class, which binds nothing.
    """
    receivers = re.findall(r"(\w+)\.addEventListener\(", dashboard.JS)
    assert receivers, "no listeners at all — the page lost its interactivity"
    stray = sorted({r for r in receivers if r not in ALLOWED_RECEIVERS})
    assert not stray, (
        f"listeners bound to {stray}; swap() replaces the innerHTML of the "
        f"volatile regions, so anything bound to an element inside one is "
        f"destroyed by the first poll that sees a new hash")


def test_the_interactive_selectors_are_still_delegated_by_name():
    """Delegation only helps if it covers the events the page actually uses.
    mouseover/mouseout rather than mouseenter/mouseleave, because only the
    former bubble to document."""
    for event in ("click", "mouseover", "mouseout"):
        assert f"document.addEventListener('{event}'" in dashboard.JS, (
            f"{event} is not delegated; the handler that used it is orphaned "
            f"by a swap")
    assert "mouseenter" not in dashboard.JS, (
        "mouseenter does not bubble, so it cannot be delegated to document")


def test_swap_reanimates_the_replaced_figure(cfg, tmp_path):
    """F-06. The value is server-rendered and correct either way, so this is
    polish — pinned so the hook is not deleted as unused."""
    html = _rendered(cfg, tmp_path)
    assert "window.__repete_after_swap" in html
    assert "if(window.__repete_after_swap)window.__repete_after_swap();" in html


def test_the_count_up_never_reanimates_the_same_element_twice(cfg, tmp_path):
    """Without the guard, every poll would restart the animation on a figure
    that did not change — a number that flickers on a timer reads as data
    churning when nothing happened."""
    html = _rendered(cfg, tmp_path)
    assert "if(el.dataset.counted)return;" in html


def test_an_empty_filter_explains_itself(cfg, tmp_path):
    """F-08. The table renders only the most recent N_DECISIONS and executed
    trades are a few percent of decisions, so a chip matching nothing is an
    ordinary day. It used to leave a header above an empty void."""
    html = _rendered(cfg, tmp_path)
    assert "id=nomatch" in html
    assert "No decisions match this filter" in html
    row = re.search(r"<tr id=nomatch[^>]*>", html)
    assert row, "the no-match row is not rendered"
    assert "display:none" in row.group(0), (
        "the no-match row must start hidden or it shows as a result")
    assert not re.search(r"<tr id=nomatch[^>]*class=", row.group(0)), (
        "the no-match row must carry no r-* class, or a filter could match it")
    # Both halves. Asserting only the toggle let a mutation replacing the
    # lookup with `var nm=null;` SURVIVE — the toggle line was still present,
    # the test still passed, and the row would never have been shown again.
    # Reported as a weakness of this test rather than quietly patched.
    assert "var nm=document.getElementById('nomatch');" in html, (
        "the handler no longer looks the no-match row up")
    assert "if(nm)nm.style.display=any?'none':'';" in html, (
        "the handler no longer toggles the no-match row")


def test_the_failure_note_survives_a_repaint(cfg, tmp_path):
    """F-07. paint() used to be called by the 30s repaint with an empty note,
    erasing 'update check failed' thirty seconds after every failed poll. A
    page whose update path had been broken for hours looked healthy for about
    half of every minute. Measured against a 404 sidecar before the fix:

        4s..19s  live · 16m old · update check failed
        57s      live · 17m old            <- the repaint wiped it
    """
    html = _rendered(cfg, tmp_path)
    assert "if(n!==undefined)note=n;" in html, (
        "paint() no longer keeps the last note")
    assert "setInterval(function(){paint();},30000)" in html, (
        "the repaint passes an argument again, which clears the note")
    # The staleness COLOUR was always right and must stay computed every tick.
    assert "badge.className='fresh '+cls;" in html


def test_the_local_file_note_is_still_shown(cfg, tmp_path):
    """The sticky-note change must not lose the one note that is set before
    any poll happens."""
    html = _rendered(cfg, tmp_path)
    assert "auto-update unavailable — opened as a local file" in html
    assert "var note=canPoll?''" in html
