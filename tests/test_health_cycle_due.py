"""`health.status()` must not report a missed cycle before the cycle was due.

Why this file exists (2026-08-03)
---------------------------------
Asked to confirm the bot was live on a Monday afternoon, `python src/health.py`
answered:

    DEGRADED | mode=paper | ... | heartbeat=70.49h
      - heartbeat is 70.5h old — a weekday cycle was missed

Nothing had been missed. The heartbeat is written by the trading cycle, which
fires at 15:45 ET, so on a Monday morning the newest one is Friday's — 66-70h
old — and the day's cycle was still hours away.

The guard was `now.weekday() < 5`, under a comment claiming "weekends are
excluded from the staleness verdict". They were not: that expression only skips
the check when TODAY is a weekend and does nothing about the weekend GAP.

Two separate false alarms came out of it, and both are pinned below:

  * the STALE branch fired every Monday before 15:45
  * the "ran but never completed" branch fired every WEEKDAY morning before
    15:45, because a fresh heartbeat plus no `cycle_complete` yet is the normal
    state of any weekday morning

`main()` exits non-zero on degraded, so anything gating on this failed on those
mornings too.

The fix must not buy quiet by going blind: after the cycle IS due, a stale
heartbeat is still a genuine missed cycle and must still fail. Every test here
is paired for that reason.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import health

ET = ZoneInfo("America/New_York")


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET).astimezone(timezone.utc)


# Aug 2026: Fri 31 Jul, Sat 1, Sun 2, Mon 3, Tue 4.
FRI_CYCLE = _at(2026, 7, 31, 15, 45)     # last heartbeat before the weekend
MON_MORNING = _at(2026, 8, 3, 10, 0)
MON_AFTER = _at(2026, 8, 3, 16, 15)      # when the watchdog actually runs
MON_CYCLE = _at(2026, 8, 3, 15, 45)
TUE_MORNING = _at(2026, 8, 4, 10, 0)
TUE_AFTER = _at(2026, 8, 4, 16, 15)
SAT = _at(2026, 8, 1, 12, 0)


def _env(tmp_path, monkeypatch, heartbeat=None, records=()):
    """Isolated cwd with an optional heartbeat and ledger."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    if heartbeat is not None:
        (tmp_path / "memory" / "heartbeat").write_text(heartbeat.isoformat() + "\n")
    cfg = {"memory": {"ledger_path": "memory/ledger.jsonl"}, "mode": "paper"}
    import json
    with open(tmp_path / "memory" / "ledger.jsonl", "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return cfg


def _cycle_complete(when):
    return [{"type": "event", "event": ev, "ts": when.isoformat()}
            for ev in ("cycle_complete", "market_context")]


def _missed(problems):
    return [p for p in problems if "cycle was missed" in p]


def _incomplete(problems):
    return [p for p in problems if "never completed" in p]


# --------------------------------------------- cycle_was_due, the predicate

def test_cycle_is_not_due_before_1545_on_a_weekday():
    assert health.cycle_was_due(MON_MORNING) is False


def test_cycle_is_due_at_1545_and_after():
    assert health.cycle_was_due(MON_CYCLE) is True
    assert health.cycle_was_due(MON_AFTER) is True


def test_cycle_is_never_due_at_the_weekend():
    assert health.cycle_was_due(SAT) is False


# --------------------------------------------- THE false alarm, both directions

def test_monday_morning_with_fridays_heartbeat_is_NOT_a_missed_cycle(
        tmp_path, monkeypatch):
    """The bug, exactly as observed. 66h stale is the normal state of a Monday
    morning, because nothing writes a heartbeat over a weekend."""
    cfg = _env(tmp_path, monkeypatch, heartbeat=FRI_CYCLE)
    st = health.status(cfg=cfg, now=MON_MORNING, read_only=True)
    assert st["heartbeat_age_hours"] > health.MAX_HEARTBEAT_AGE_HOURS
    assert _missed(st["problems"]) == [], st["problems"]


def test_monday_AFTER_1545_with_fridays_heartbeat_IS_a_missed_cycle(
        tmp_path, monkeypatch):
    """The half that must keep working. Same heartbeat, same day, later clock:
    the cycle was due and did not write one, which is a real failure. Without
    this the fix would just be a mute."""
    cfg = _env(tmp_path, monkeypatch, heartbeat=FRI_CYCLE)
    st = health.status(cfg=cfg, now=MON_AFTER, read_only=True)
    assert _missed(st["problems"]), st["problems"]
    assert not st["healthy"]


def test_weekday_morning_is_not_reported_as_ran_but_never_completed(
        tmp_path, monkeypatch):
    """The second false alarm, which fired every weekday morning.

    Tuesday 10:00 with Monday's heartbeat: 18h old, so it passes the staleness
    branch, and today's cycle has of course not completed at 10am. The old
    guard called that 'cycle ran today but never completed'.
    """
    cfg = _env(tmp_path, monkeypatch, heartbeat=MON_CYCLE,
               records=_cycle_complete(MON_CYCLE))
    st = health.status(cfg=cfg, now=TUE_MORNING, read_only=True)
    assert st["heartbeat_age_hours"] < health.MAX_HEARTBEAT_AGE_HOURS
    assert _incomplete(st["problems"]) == [], st["problems"]


def test_weekday_AFTER_1545_with_no_completion_IS_flagged(tmp_path, monkeypatch):
    """Its paired half: a fresh heartbeat means the process started, so no
    `cycle_complete` once the cycle was due means it started and died. That is
    the 2026-07-24 bug this branch was written for, and it must still fire."""
    cfg = _env(tmp_path, monkeypatch, heartbeat=TUE_AFTER, records=[])
    st = health.status(cfg=cfg, now=TUE_AFTER, read_only=True)
    assert _incomplete(st["problems"]), st["problems"]


# --------------------------------------------- unchanged behaviour

def test_a_normal_completed_weekday_is_healthy(tmp_path, monkeypatch):
    cfg = _env(tmp_path, monkeypatch, heartbeat=MON_CYCLE,
               records=_cycle_complete(MON_CYCLE))
    st = health.status(cfg=cfg, now=MON_AFTER, read_only=True)
    assert st["problems"] == [], st["problems"]
    assert st["healthy"]


def test_no_heartbeat_at_all_still_says_the_cycle_has_never_run(
        tmp_path, monkeypatch):
    """Absence is a different fact from staleness and must not be gated away —
    it is true at any hour of any day."""
    cfg = _env(tmp_path, monkeypatch, heartbeat=None)
    for when in (MON_MORNING, MON_AFTER, SAT):
        st = health.status(cfg=cfg, now=when, read_only=True)
        assert any("never run" in p for p in st["problems"]), (when, st["problems"])


def test_the_weekend_stays_quiet(tmp_path, monkeypatch):
    cfg = _env(tmp_path, monkeypatch, heartbeat=FRI_CYCLE)
    st = health.status(cfg=cfg, now=SAT, read_only=True)
    assert _missed(st["problems"]) == []
    assert _incomplete(st["problems"]) == []
