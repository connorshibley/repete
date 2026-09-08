"""The handoff record, and the four ways it could lie about the bot's state.

WHY THIS EXISTS. A bot that records nothing when it does nothing is
indistinguishable from a bot that is dead. This project has been bitten twice:
the 2026-07-24 cycle that crashed six seconds in and still stamped a fresh
heartbeat, and the 2026-08-21→28 outage that refused 53 entries for seven days
while every surface said "live". `handoff` writes one self-describing row per
cycle so "quiet" and "silent" stop looking the same.

The failure modes it must not have, in the order they would hurt:

  1. raising into the trading cycle — instrumentation that can stop a cycle is
     worse than the outage it reports
  2. reporting `unresolved: []` when health could not be read — an unknown
     rendered as an all-clear is this repo's canonical false pass
  3. reporting `realized_today: 0.0` on a day with no rows at all — absent
     collapsing to zero, the standing rule
  4. drifting from the scheduler's own cron predicate, so the "next job" names
     something the scheduler will not run
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import handoff  # noqa: E402

ET = ZoneInfo("America/New_York")
# A Tuesday, mid-session — a normal trading afternoon.
NOW = datetime(2026, 9, 8, 14, 0, tzinfo=ET)


def _rows(day="2026-09-08"):
    return [
        {"type": "decision", "ts": f"{day}T13:35:00Z", "action": "buy",
         "executed": True},
        {"type": "decision", "ts": f"{day}T13:35:01Z", "action": "buy",
         "executed": False, "rail": "live_kill"},
        {"type": "decision", "ts": f"{day}T13:36:00Z", "action": "sell",
         "executed": True},
        {"type": "outcome", "ts": f"{day}T13:36:01Z", "pnl": -45.20},
        {"type": "decision", "ts": "2026-09-04T13:35:00Z", "action": "buy",
         "executed": True},           # a previous day — must NOT be counted
    ]


# --- 1. never raises into the cycle ----------------------------------------

class _ExplodingLedger:
    def all_records(self):
        raise RuntimeError("sqlite3.OperationalError: database is locked")

    def log_event(self, *a, **k):
        raise AssertionError("should never be reached")


def test_record_never_raises_when_the_ledger_is_broken():
    """The cycle outranks its own reporting. A store that raises must produce
    None and a warning, never an exception in run_cycle's tail."""
    assert handoff.record(_ExplodingLedger(), {}, equity=1.0,
                          n_positions=0) is None


def test_record_never_raises_when_the_write_fails():
    class _WriteFails:
        def all_records(self):
            return []

        def log_event(self, *a, **k):
            raise OSError("disk full")

    assert handoff.record(_WriteFails(), {}, equity=1.0, n_positions=0) is None


# --- 2. unknown is not an all-clear ----------------------------------------

def test_unreadable_health_reports_none_not_an_empty_problem_list(monkeypatch):
    """`unresolved: []` means "checked, nothing wrong". If health could not be
    read, the honest answer is None. Rendering the two the same way is how a
    broken monitor reads as a healthy bot."""
    import health
    monkeypatch.setattr(health, "status",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    h = handoff.build({}, _rows(), equity=100.0, n_positions=1, now=NOW)
    assert h["unresolved"] is None


def test_readable_health_with_no_problems_is_an_empty_list(monkeypatch):
    """The mirror case, and it must NOT be None — checked-and-clean is a
    measurement."""
    import health
    monkeypatch.setattr(health, "status", lambda *a, **k: {"problems": []})
    h = handoff.build({}, _rows(), equity=100.0, n_positions=1, now=NOW)
    assert h["unresolved"] == []


# --- 3. absent never collapses to zero -------------------------------------

def test_a_day_with_no_rows_reports_none_realized_not_zero():
    """A quiet day and a day that netted exactly $0.00 are different facts.
    Reporting both as 0.00 is the absent-vs-zero collapse this repo refuses
    in the benchmark fields, fill quality, and the judge probe."""
    h = handoff.build({}, _rows(day="2026-09-04"), equity=100.0,
                      n_positions=1, now=NOW)      # rows exist, but not today
    assert h["outcomes"]["closed_today"] == 0
    assert h["outcomes"]["realized_today"] is None


def test_today_is_counted_and_other_days_are_not():
    h = handoff.build({}, _rows(), equity=100.0, n_positions=1, now=NOW)
    assert h["changed"] == {"entries": 1, "exits": 1, "refused": 1}
    assert h["outcomes"]["closed_today"] == 1
    assert h["outcomes"]["realized_today"] == -45.20


# --- 4. the next job comes from the scheduler's own predicate ---------------

def test_next_job_agrees_with_the_schedulers_own_due_predicate():
    """Pinned by AGREEMENT, not by a hardcoded name: whatever handoff reports,
    scheduler.due() must say that job is actually due at that minute. A second
    copy of the cron logic would drift, and a reporting copy would drift
    silently — printing a next job the scheduler no longer runs."""
    import scheduler
    nj = handoff.next_job(NOW)
    assert nj is not None, "no job found in a week of lookahead"
    when = datetime.strptime(nj["at_et"], "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    job = [j for j in scheduler.JOBS if j[0] == nj["name"]][0]
    assert scheduler.due(job, when), (
        f"handoff says {nj['name']} runs at {nj['at_et']}, scheduler disagrees")


def test_next_job_is_strictly_in_the_future():
    nj = handoff.next_job(NOW)
    assert nj["in_minutes"] >= 1


def test_next_job_is_none_rather_than_a_guess_when_the_table_is_gone(monkeypatch):
    monkeypatch.setitem(sys.modules, "scheduler", None)
    assert handoff.next_job(NOW) is None


# --- the record lands, and is readable --------------------------------------

def test_record_writes_one_parseable_handoff_event(tmp_path, monkeypatch):
    import health
    monkeypatch.setattr(health, "status", lambda *a, **k: {"problems": ["x"]})
    written = []

    class _Led:
        def all_records(self):
            return _rows()

        def log_event(self, event, detail):
            written.append((event, detail))

    payload = handoff.record(_Led(), {}, equity=99989.48, n_positions=12,
                             now=NOW)
    assert payload is not None
    assert len(written) == 1 and written[0][0] == "handoff"
    back = json.loads(written[0][1])
    assert back["remaining_risk"]["open_positions"] == 12
    assert back["remaining_risk"]["equity"] == 99989.48
    assert back["unresolved"] == ["x"]
    for field in ("changed", "outcomes", "remaining_risk", "unresolved",
                  "next_job"):
        assert field in back, f"handoff dropped {field}"
