"""The handoff panel — the phone view — and the two ways it could mislead.

`src/handoff.py` writes one self-describing row per cycle. This renders it.
The panel reads the RECORD and never recomputes: the ledger is the source of
truth and the page is a view, so a second computation here could silently
disagree with the row the cycle actually wrote.

Two failure modes, both of which would undo the record's whole purpose:

  1. **Absent rendering as zero.** A grid of "0 entries, 0 exits, $0.00"
     with no handoff row claims the bot had a quiet cycle when in fact
     nothing reported. That is precisely the confusion the handoff record was
     added to end (2026-07-24's crashed cycle; 2026-08-21..28's silent
     outage), reintroduced at the display layer.
  2. **Leaking operator state.** The record's `unresolved` list holds
     infrastructure weaknesses — currently "no verified off-host mirror —
     every backup is on the same disk as the thing it protects". This page is
     public. Equity, positions and every decision are already published, so
     nothing else in the handoff is newly exposed; a backup posture is a
     different category and stays with the operator.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import dashboard  # noqa: E402

MIRROR = "no verified off-host mirror — every backup is on the same disk"


def _event(payload):
    return [{"type": "event", "event": "handoff", "ts": "2026-09-08T19:48:00Z",
             "detail": json.dumps(payload)}]


def _full(**over):
    p = {"date_et": "2026-09-08",
         "changed": {"entries": 2, "exits": 1, "refused": 8},
         "outcomes": {"closed_today": 1, "realized_today": -45.20},
         "remaining_risk": {"open_positions": 12, "equity": 99989.48,
                            "drawdown_pct": 0.64, "headroom_pp": 9.36,
                            "entries_blocked": False},
         "unresolved": [MIRROR],
         "next_job": {"name": "watchdog", "at_et": "2026-09-08 16:15",
                      "in_minutes": 27}}
    p.update(over)
    return p


# --- it renders the record ---------------------------------------------------

def test_the_panel_shows_the_recorded_numbers():
    html = dashboard._handoff_panel(_event(_full()))
    for expect in ("entries today", "refused today", "watchdog", "in 27m"):
        assert expect in html, f"panel missing {expect!r}"
    assert ">2<" in html and ">8<" in html
    assert "-$45.20" in html


def test_the_panel_reads_the_latest_handoff_not_the_first():
    old = _event(_full(changed={"entries": 99, "exits": 0, "refused": 0}))
    new = _event(_full(changed={"entries": 7, "exits": 0, "refused": 0}))
    html = dashboard._handoff_panel(old + new)
    assert ">7<" in html and ">99<" not in html


# --- 1. absent renders as absent --------------------------------------------

def test_no_handoff_record_says_so_instead_of_showing_zeros():
    html = dashboard._handoff_panel([])
    assert "No handoff recorded yet" in html
    assert "entries today" not in html, (
        "an empty panel rendered the card grid — zeros with no record claim a "
        "quiet cycle when nothing has reported")


def test_an_unparseable_handoff_is_treated_as_absent():
    bad = [{"type": "event", "event": "handoff", "detail": "{not json"}]
    assert "No handoff recorded yet" in dashboard._handoff_panel(bad)


def test_a_null_realized_pnl_is_a_dash_not_a_dollar_zero():
    """A quiet day and a day that netted exactly $0.00 are different facts —
    the standing absent-vs-zero rule, at the display layer."""
    html = dashboard._handoff_panel(_event(_full(
        outcomes={"closed_today": 0, "realized_today": None})))
    assert "realized today" in html
    assert "$0.00" not in html


def test_a_missing_drawdown_is_a_dash_not_a_zero_percent():
    html = dashboard._handoff_panel(_event(_full(
        remaining_risk={"open_positions": 3, "equity": 1.0,
                        "drawdown_pct": None, "headroom_pp": None,
                        "entries_blocked": False})))
    assert "0.00%" not in html


# --- 2. operator state stays with the operator -------------------------------

def test_the_unresolved_list_never_reaches_the_public_panel():
    html = dashboard._handoff_panel(_event(_full()))
    assert MIRROR not in html
    assert "off-host" not in html and "backup" not in html


def test_the_unresolved_list_never_reaches_the_whole_rendered_page(tmp_path,
                                                                   cfg,
                                                                   monkeypatch):
    """Panel-level absence is not enough — assert it against the FULL page, so
    a future author who surfaces `unresolved` somewhere else still trips this."""
    import store
    from ledger import Ledger
    store.configure(cfg)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("cycle_complete", '{"equity": 100000.0}')
    led.log_event("handoff", json.dumps(_full()))
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "d.html"
    dashboard.render(cfg, out_path=str(out), spy_bars=[])
    page = out.read_text()
    assert "This cycle" in page, "the handoff region did not render at all"
    assert MIRROR not in page
    assert "off-host" not in page


# --- the blocked state is loud ----------------------------------------------

def test_entries_blocked_renders_prominently():
    """The state that produced the 2026-08-21..28 outage: 53 refused signals
    over seven days while every other surface read "live". It gets a card, not
    a footnote."""
    html = dashboard._handoff_panel(_event(_full(
        remaining_risk={"open_positions": 0, "equity": 1.0,
                        "drawdown_pct": 11.0, "headroom_pp": -1.0,
                        "entries_blocked": True})))
    assert "ENTRIES BLOCKED" in html
    assert "loss" in html


# --- it is a real region -----------------------------------------------------

def test_handoff_is_a_volatile_region_with_a_mount_point(tmp_path, cfg,
                                                         monkeypatch):
    """Being in `regions` is what makes the 60s poll refresh it on the phone
    and what puts it under the sidecar hash."""
    import store
    from ledger import Ledger
    store.configure(cfg)
    Ledger(cfg["memory"]["ledger_path"]).log_event(
        "cycle_complete", '{"equity": 100000.0}')
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "d.html"
    dashboard.render(cfg, out_path=str(out), spy_bars=[])
    assert 'id=rgn-handoff' in out.read_text()
    sidecar = json.loads((tmp_path / dashboard.DATA_PATH).read_text())
    assert "handoff" in sidecar["regions"]
