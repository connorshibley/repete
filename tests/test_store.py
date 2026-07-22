"""Event-store abstraction (2026-07-22, backlog #4): the SQLite backend must
be indistinguishable from JSONL through every domain class, and JSONL must
stay the default so a config typo can never silently move the audit trail."""
import os

import pytest

import store as store_mod
from ledger import Ledger
from judgments import JudgmentStore
from lessons import LessonStore


@pytest.fixture(autouse=True)
def _reset_backend():
    """Backend selection is process-wide; never leak it between tests."""
    yield
    store_mod.configure(None)


def test_default_is_jsonl_and_bad_config_falls_back():
    store_mod.configure(None)
    assert store_mod.current_backend() == "jsonl"
    store_mod.configure({"storage": {"backend": "postgres-ish-typo"}})
    assert store_mod.current_backend() == "jsonl"   # typo never moves data
    store_mod.configure({"storage": {"backend": "sqlite"}})
    assert store_mod.current_backend() == "sqlite"


def test_stream_name_from_path():
    assert store_mod.stream_name("memory/ledger.jsonl") == "ledger"
    assert store_mod.stream_name("/tmp/x/judgments.jsonl") == "judgments"


def test_backends_round_trip_identically(tmp_path):
    records = [{"type": "decision", "n": 1, "nested": {"a": [1, 2]}},
               {"type": "outcome", "n": 2, "unicode": "café ✓"},
               {"type": "event", "n": 3, "null": None, "f": 1.5}]
    js = store_mod.JsonlStore(str(tmp_path / "s.jsonl"))
    sq = store_mod.SqliteStore(str(tmp_path / "s.db"), "s")
    for r in records:
        js.append(r)
        sq.append(r)
    assert js.read_all() == sq.read_all() == records


def test_sqlite_streams_are_isolated(tmp_path):
    db = str(tmp_path / "agent.db")
    a = store_mod.SqliteStore(db, "ledger")
    b = store_mod.SqliteStore(db, "judgments")
    a.append({"who": "ledger"})
    b.append({"who": "judgments"})
    assert a.read_all() == [{"who": "ledger"}]      # one DB, no bleed
    assert b.read_all() == [{"who": "judgments"}]


def test_missing_source_reads_empty(tmp_path):
    assert store_mod.JsonlStore(str(tmp_path / "nope.jsonl")).read_all() == []
    assert store_mod.SqliteStore(str(tmp_path / "new.db"), "x").read_all() == []


def _exercise_ledger(path):
    """Same writes through the domain class, whatever the backend."""
    led = Ledger(path)
    led.set_model_version("v-test")
    tid = led.log_decision("SPY", "buy", "signal", {"rsi": 5}, None,
                           executed=True, entry_price=100.0, qty=10,
                           strategy="meanrev", regime="up/low")
    led.log_event("cycle_complete", '{"equity": 100.0}')
    led.close_trade(tid, 110.0, 100.0, 10.0, exit_reason="strategy_sell")
    return led


def _strip_ts(records):
    return [{k: v for k, v in r.items() if k != "ts"} for r in records]


def test_ledger_identical_through_both_backends(tmp_path):
    store_mod.configure(None)                                  # jsonl
    j = _exercise_ledger(str(tmp_path / "memory" / "ledger.jsonl"))
    store_mod.configure({"storage": {"backend": "sqlite",
                                     "sqlite_path": str(tmp_path / "a.db")}})
    s = _exercise_ledger(str(tmp_path / "memory2" / "ledger.jsonl"))

    # trade_ids are random uuids; compare everything else
    def norm(recs):
        out = []
        for r in _strip_ts(recs):
            r = dict(r)
            r.pop("trade_id", None)
            out.append(r)
        return out

    assert norm(j.all_records()) == norm(s.all_records())
    assert len(j.closed_trades()) == len(s.closed_trades()) == 1
    assert j.open_buys() == {} and s.open_buys() == {}
    assert all(r.get("model_version") == "v-test" for r in s.all_records())


def test_lessons_and_judgments_work_on_sqlite(tmp_path):
    store_mod.configure({"storage": {"backend": "sqlite",
                                     "sqlite_path": str(tmp_path / "a.db")}})
    ls = LessonStore(str(tmp_path / "lessons.jsonl"))
    lid = ls.add_lesson("possible pattern (n=1): x", {"symbols": ["SPY"]}, "t1")
    assert list(ls.replay()) == [lid]

    js = JudgmentStore(str(tmp_path / "judgments.jsonl"))
    jid = js.log_judgment("t1", "SPY", "buy", "approve", 1.0, 100.0, None,
                          kind="llm", executed=True)
    js.log_resolution(jid, "realized", 5.0, "strategy_sell", "good_approve")
    replayed = js.replay()
    assert replayed[jid]["resolution"]["pnl_pct"] == 5.0


# ---- health + scheduler (Phase A ops surface) ----

def test_health_status_shape_and_problems(tmp_path, monkeypatch, cfg):
    import health
    from datetime import datetime, timedelta, timezone
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir()
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("cycle_complete", '{"equity": 100.0}')
    led.log_event("degradation", "drift_guard: test")

    now = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)   # Wednesday
    (tmp_path / "memory" / "heartbeat").write_text(
        (now - timedelta(hours=2)).isoformat())
    s = health.status(cfg, now=now)
    assert s["healthy"] and s["problems"] == []
    assert s["degradations_today"] == 1 and s["heartbeat_age_hours"] == 2.0
    assert s["storage_backend"] == "jsonl"

    # stale heartbeat on a weekday => degraded, with a reason
    (tmp_path / "memory" / "heartbeat").write_text(
        (now - timedelta(hours=40)).isoformat())
    s2 = health.status(cfg, now=now)
    assert not s2["healthy"] and any("missed" in p for p in s2["problems"])

    # HALT is always a problem
    (tmp_path / "HALT").write_text("stop")
    assert any("HALT" in p for p in health.status(cfg, now=now)["problems"])


def test_scheduler_job_timing():
    """Jobs fire only in their scheduled ET minute, weekdays only."""
    import importlib.util
    from datetime import datetime
    from zoneinfo import ZoneInfo
    spec = importlib.util.spec_from_file_location(
        "sched", os.path.join(os.path.dirname(__file__), "..", "scripts",
                              "scheduler.py"))
    sched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sched)
    ET = ZoneInfo("America/New_York")
    jobs = {j[0]: j for j in sched.JOBS}

    wed_1545 = datetime(2026, 7, 22, 15, 45, tzinfo=ET)
    assert sched.due(jobs["cycle"], wed_1545)
    assert not sched.due(jobs["cycle"], datetime(2026, 7, 22, 15, 46, tzinfo=ET))
    assert not sched.due(jobs["cycle"],                       # Sunday
                         datetime(2026, 7, 19, 15, 45, tzinfo=ET))
    # news-brain is hourly at :25 inside market hours only
    assert sched.due(jobs["news-brain"], datetime(2026, 7, 22, 11, 25, tzinfo=ET))
    assert not sched.due(jobs["news-brain"], datetime(2026, 7, 22, 3, 25, tzinfo=ET))
