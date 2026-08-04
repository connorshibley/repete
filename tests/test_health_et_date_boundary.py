"""`health.status()` must answer every date question in ONE timezone.

Why this file exists (2026-08-03, found while §50's gates were running)
----------------------------------------------------------------------
`python src/health.py` at 20:51 ET on a Monday reported:

    DEGRADED | mode=paper | ... | heartbeat=5.05h
      - cycle ran today but never completed

The day's cycle had completed at 15:47 ET and the record was sitting in the
ledger. Nothing had failed.

The cause is a timezone seam, not a logic error. Two halves of the same
question were answered in different zones, and each was internally consistent:

  * `cycle_was_due(now)` converts to America/New_York, correctly, because the
    cycle fires at 15:45 ET;
  * `cycle_completed_today` compared `now.strftime()` — a **UTC** date —
    against `record["ts"][:10]`, also UTC.

Between 20:00 ET and midnight ET the UTC date has already rolled forward while
ET is still the same trading day. So for four hours every weekday evening the
scheduler half says "a cycle was due today" while the ledger half goes looking
for records stamped with *tomorrow's* UTC date, finds none, and concludes the
cycle died.

`main()` exits non-zero on degraded, so anything gating on health failed every
weekday night.

This is the SAME CLASS as the Monday false alarm fixed in PR #77 — a staleness
question answered in one timezone and a scheduling question in another. That is
why the fix makes every date in the module Eastern rather than special-casing
the evening: a second seam would just move the four hours somewhere else.

Every test here is paired. A fix that silences the evening by never reporting
an incomplete cycle would be worse than the bug.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import health

ET = ZoneInfo("America/New_York")


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(timezone.utc)


# Mon 3 Aug 2026. The cycle fires 15:45 ET.
CYCLE = _at(2026, 8, 3, 15, 47)          # today's completed cycle
EVENING = _at(2026, 8, 3, 20, 51)        # THE BUG — UTC is already Aug 4
LATE = _at(2026, 8, 3, 23, 30)           # still Monday in ET
AFTERNOON = _at(2026, 8, 3, 16, 15)      # UTC and ET agree here
NEXT_DAY = _at(2026, 8, 4, 16, 15)       # Tuesday, after the cycle was due


def _env(tmp_path, monkeypatch, records=()):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "heartbeat").write_text(CYCLE.isoformat() + "\n")
    with open(tmp_path / "memory" / "ledger.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return {"memory": {"ledger_path": "memory/ledger.jsonl"}, "mode": "paper",
            "risk": {"max_drawdown_pct": 10.0}}


def _events(when, *names):
    return [{"type": "event", "event": n, "ts": when.isoformat()} for n in names]


def _incomplete(problems):
    return [p for p in problems if "never completed" in p]


# ------------------------------------------------------------- the predicate

def test_et_date_of_an_evening_timestamp_is_still_that_day():
    """20:51 ET on Aug 3 is 00:51 UTC on Aug 4. The ET date is what counts."""
    assert health._et_date(EVENING.isoformat()) == "2026-08-03"
    assert EVENING.strftime("%Y-%m-%d") == "2026-08-04"   # the trap, pinned


def test_et_date_of_an_afternoon_timestamp_is_unchanged():
    assert health._et_date(CYCLE.isoformat()) == "2026-08-03"


def test_et_date_of_junk_is_empty_not_a_guess():
    """An unparseable timestamp must not silently become today and count as a
    completed cycle."""
    assert health._et_date("not a date") == ""
    assert health._et_date("") == ""
    assert health._et_date(None) == ""


def test_a_naive_timestamp_is_read_as_UTC():
    """Older records were written without an offset. Guessing local time here
    would shift them by hours and re-open the same seam."""
    assert health._et_date("2026-08-04T00:51:00") == "2026-08-03"


# ------------------------------------------------- THE bug, in both directions

def test_a_completed_cycle_still_counts_in_the_ET_EVENING(tmp_path, monkeypatch):
    """The bug exactly as observed: 20:51 ET, cycle completed at 15:47 ET, and
    health called it dead because UTC had rolled over to the 4th."""
    cfg = _env(tmp_path, monkeypatch,
               _events(CYCLE, "cycle_complete", "market_context"))
    st = health.status(cfg=cfg, now=EVENING, read_only=True)
    assert st["cycle_completed_today"] is True
    assert _incomplete(st["problems"]) == [], st["problems"]
    assert st["healthy"] is True


def test_still_counts_at_2330_ET(tmp_path, monkeypatch):
    """The far edge of the same window."""
    cfg = _env(tmp_path, monkeypatch,
               _events(CYCLE, "cycle_complete", "market_context"))
    st = health.status(cfg=cfg, now=LATE, read_only=True)
    assert st["cycle_completed_today"] is True


def test_a_GENUINELY_missing_cycle_IS_still_flagged_in_the_evening(
        tmp_path, monkeypatch):
    """The half that must keep working. Same evening clock, same fresh
    heartbeat, but no cycle_complete anywhere — that is a real failure and
    must still fail. Without this the fix would just be a mute."""
    cfg = _env(tmp_path, monkeypatch, records=[])
    st = health.status(cfg=cfg, now=EVENING, read_only=True)
    assert _incomplete(st["problems"]), st["problems"]
    assert st["healthy"] is False


def test_a_cycle_that_COMPLETES_in_the_ET_evening_counts(tmp_path, monkeypatch):
    """The case the other tests do not reach, found by a surviving mutation.

    Every test above stamps `cycle_complete` at 15:47 ET, where the UTC and ET
    dates agree — so reverting the RECORD side of the comparison to UTC slicing
    changed nothing and the mutation lived. A cycle can legitimately finish in
    the ET evening (a manual re-run, or the catch-up job on a machine that woke
    late), and that record carries tomorrow's UTC date. Without converting it
    the cycle would not count on the day it actually ran.
    """
    cfg = _env(tmp_path, monkeypatch,
               _events(EVENING, "cycle_complete", "market_context"))
    st = health.status(cfg=cfg, now=EVENING, read_only=True)
    assert st["cycle_completed_today"] is True
    assert _incomplete(st["problems"]) == [], st["problems"]


def test_YESTERDAYS_cycle_does_not_satisfy_today(tmp_path, monkeypatch):
    """The other way the seam could be papered over. Monday's completed cycle
    must not make Tuesday evening look healthy."""
    cfg = _env(tmp_path, monkeypatch,
               _events(CYCLE, "cycle_complete", "market_context"))
    st = health.status(cfg=cfg, now=NEXT_DAY, read_only=True)
    assert st["cycle_completed_today"] is False
    assert _incomplete(st["problems"]), st["problems"]


def test_the_afternoon_case_is_unchanged(tmp_path, monkeypatch):
    """16:15 ET is 20:15 UTC — same date either way. This is the window the old
    code got right, and it must stay right."""
    cfg = _env(tmp_path, monkeypatch,
               _events(CYCLE, "cycle_complete", "market_context"))
    st = health.status(cfg=cfg, now=AFTERNOON, read_only=True)
    assert st["cycle_completed_today"] is True
    assert st["problems"] == [], st["problems"]


# --------------------------------------- the other today-keyed counters agree

def test_degradations_are_counted_on_the_ET_day_too(tmp_path, monkeypatch):
    """Same seam, quieter symptom: an evening degradation was being counted
    against tomorrow, so the SLO window silently reset at 20:00 ET."""
    cfg = _env(tmp_path, monkeypatch,
               _events(CYCLE, "cycle_complete", "market_context")
               + _events(EVENING, "degradation", "degradation"))
    st = health.status(cfg=cfg, now=EVENING, read_only=True)
    assert st["degradations_today"] == 2


def test_a_crash_recorded_in_the_evening_is_seen(tmp_path, monkeypatch):
    cfg = _env(tmp_path, monkeypatch, _events(EVENING, "cycle_crashed"))
    st = health.status(cfg=cfg, now=EVENING, read_only=True)
    assert st["cycle_crashed_today"] is True
