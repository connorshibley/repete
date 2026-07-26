"""A cycle that starts and dies must not look like a quiet market.

The incident this file exists for
---------------------------------
On Friday 2026-07-24 the 15:45 cycle produced zero decisions, no
`cycle_complete`, and no abort record. Five positions were open, and
main.py writes one unconditional `hold` per held position, so zero records
proves it never reached the decision loop. It died about six seconds in --
a healthy cycle takes ~5 minutes -- somewhere before its first ledger write.

Nothing noticed, for two days:

  * `write_heartbeat()` runs in a `finally:`, so the crash stamped a fresh
    heartbeat on its way out;
  * `watchdog.check()` compared only the heartbeat DATE;
  * `watchdog.catchup()` -- which exists precisely to re-run a missed cycle --
    used the same test, so **the crash silenced its own recovery**;
  * `alerting.heartbeat_ping(success=ok)` was passed a flag that meant "did
    not raise", so deliberate aborts reported success to the external monitor;
  * the EOD post read "no trades executed, no vetoes, no holds, nothing closed
    today", which is what a genuinely quiet day looks like.

`docs/slo.md` claimed cycle completion was measured by `cycle_complete`
events. No code anywhere asserted on them. This file is that assertion.
"""
from datetime import date, datetime, timezone

import pytest

import watchdog
from test_main_cycle import cycle_env  # noqa: F401 — full end-to-end harness

MONDAY = date(2026, 7, 27)
SATURDAY = date(2026, 7, 25)


def _hb(tmp_path, when: datetime) -> str:
    p = tmp_path / "heartbeat"
    p.write_text(when.isoformat() + "\n")
    return str(p)


def _fresh(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 19, 45, tzinfo=timezone.utc)


def _complete(day: date):
    return [{"type": "event", "event": "cycle_complete",
             "ts": _fresh(day).isoformat()}]


def _args(tmp_path, day):
    return {"today": day, "heartbeat_path": _hb(tmp_path, _fresh(day)),
            "halt_path": str(tmp_path / "HALT")}


# ---- the incident, replayed ----

def test_fresh_heartbeat_with_no_completion_is_a_problem(tmp_path):
    """2026-07-24 exactly: the process ran, and got nowhere."""
    problems = watchdog.check(**_args(tmp_path, MONDAY), records=[])
    assert problems, "a crashed cycle with a fresh heartbeat must alarm"
    assert "never reached cycle_complete" in problems[0]


def test_a_completed_cycle_is_quiet(tmp_path):
    assert watchdog.check(**_args(tmp_path, MONDAY),
                          records=_complete(MONDAY)) == []


def test_yesterdays_completion_does_not_count_for_today(tmp_path):
    """Off-by-one guard. Looking for 'any cycle_complete' rather than 'one
    dated today' would have reported all-clear straight through the
    incident, since 07-23 completed normally."""
    problems = watchdog.check(**_args(tmp_path, MONDAY),
                              records=_complete(date(2026, 7, 24)))
    assert problems and "never reached cycle_complete" in problems[0]


def test_the_two_failures_are_told_apart(tmp_path):
    """'Never started' and 'started and died' need different messages --
    they have different causes and different fixes."""
    never_ran = watchdog.check(today=MONDAY,
                               heartbeat_path=str(tmp_path / "nope"),
                               halt_path=str(tmp_path / "HALT"), records=[])
    died = watchdog.check(**_args(tmp_path, MONDAY), records=[])
    assert "no heartbeat" in never_ran[0]
    assert "never reached cycle_complete" in died[0]
    assert never_ran[0] != died[0]


# ---- the monitor must not go quiet when its input is missing ----

def test_an_unreadable_ledger_is_reported_not_ignored(tmp_path, monkeypatch):
    """Fail closed. A monitor that reports all-clear because it could not
    read anything is the exact failure being corrected here."""
    monkeypatch.chdir(tmp_path)          # no config.yaml -> ledger unreachable
    problems = watchdog.check(**_args(tmp_path, MONDAY))
    assert problems and "cannot read the ledger" in problems[0]


def test_completed_on_distinguishes_absent_from_unreadable():
    assert watchdog.completed_on(MONDAY, _complete(MONDAY)) is True
    assert watchdog.completed_on(MONDAY, []) is False
    assert watchdog.completed_on(MONDAY, watchdog._UNREADABLE) is None


def test_unparseable_timestamps_do_not_crash_the_check():
    records = [{"type": "event", "event": "cycle_complete", "ts": "garbage"},
               {"type": "event", "event": "cycle_complete"}]
    assert watchdog.completed_on(MONDAY, records) is False


# ---- guarantees that must survive ----

def test_weekend_stays_quiet_with_no_completion(tmp_path):
    """Existing hard guarantee: Saturday has no cycle and must not alarm."""
    assert watchdog.check(today=SATURDAY,
                          heartbeat_path=str(tmp_path / "heartbeat"),
                          halt_path=str(tmp_path / "HALT"), records=[]) == []


# ---- the crash leaves a record ----

def test_a_crash_is_recorded_and_still_raises(tmp_path, monkeypatch, cfg):
    """The traceback has to reach the ledger, because it reaches nowhere else:
    the scheduler captures stderr, and the log files had already rotated by
    the time anyone looked at 07-24."""
    import yaml
    import main
    from ledger import Ledger

    monkeypatch.chdir(tmp_path)
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    monkeypatch.setattr(main, "_run_cycle",
                        lambda **k: (_ for _ in ()).throw(
                            RuntimeError("alpaca 503")))

    with pytest.raises(RuntimeError):
        main.run_cycle()

    crashes = [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
               if r.get("event") == "cycle_crashed"]
    assert len(crashes) == 1
    assert "RuntimeError" in crashes[0]["detail"]
    assert "alpaca 503" in crashes[0]["detail"]


def test_systemexit_is_caught_too(tmp_path, monkeypatch, cfg):
    """broker.py raises SystemExit when the Alpaca keys are missing, and
    SystemExit is a BaseException -- `except Exception` would miss the very
    failure most likely to kill a cycle before its first ledger write."""
    import yaml
    import main
    from ledger import Ledger

    monkeypatch.chdir(tmp_path)
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    monkeypatch.setattr(main, "_run_cycle",
                        lambda **k: (_ for _ in ()).throw(
                            SystemExit("ALPACA_API_KEY missing")))

    with pytest.raises(SystemExit):
        main.run_cycle()

    crashes = [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
               if r.get("event") == "cycle_crashed"]
    assert crashes and "ALPACA_API_KEY" in crashes[0]["detail"]


def test_recording_the_crash_never_masks_it(tmp_path, monkeypatch, cfg):
    """If the ledger is the thing that broke, the original exception must
    still be what surfaces -- not a secondary failure from the recorder."""
    import main

    monkeypatch.chdir(tmp_path)          # no config.yaml at all
    monkeypatch.setattr(main, "_run_cycle",
                        lambda **k: (_ for _ in ()).throw(
                            RuntimeError("the real problem")))
    with pytest.raises(RuntimeError, match="the real problem"):
        main.run_cycle()


# ---- the external monitor stops being told a comforting lie ----

def test_ping_reports_failure_when_the_cycle_did_not_complete(
        tmp_path, monkeypatch, cfg):
    """`ok` used to mean "did not raise", so every deliberate abort -- HALT,
    kill switch, stale data -- pinged the external monitor as a success. A
    cycle that did not trade is not a cycle that worked."""
    import yaml
    import alerting
    import main

    monkeypatch.chdir(tmp_path)
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    seen = []
    monkeypatch.setattr(alerting, "heartbeat_ping",
                        lambda success: seen.append(success))

    monkeypatch.setattr(main, "_run_cycle", lambda **k: None)   # early return
    main.run_cycle()
    assert seen == [False], "an aborted cycle must not ping success"

    seen.clear()
    monkeypatch.setattr(main, "_run_cycle", lambda **k: True)   # completed
    main.run_cycle()
    assert seen == [True]


def test_a_real_completed_cycle_signals_success(cycle_env, monkeypatch):
    """The other half, against the REAL _run_cycle rather than a stub.

    A negative control caught this gap: deleting `return True` from the end
    of _run_cycle broke nothing, because every test above replaces the
    function. That failure mode is not harmless -- every healthy cycle would
    ping the external monitor as a failure, the alerts would be ignored
    within a week, and the monitoring would be worth less than none.
    """
    import alerting
    import main
    from ledger import Ledger
    from test_main_cycle import BUY_CLOSES, FakeCycleBroker
    from conftest import make_bars

    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    seen = []
    monkeypatch.setattr(alerting, "heartbeat_ping",
                        lambda success: seen.append(success))

    main.run_cycle()

    assert seen == [True], "a cycle that ran to the end must ping success"
    assert [r for r in Ledger(cfg["memory"]["ledger_path"]).all_records()
            if r.get("event") == "cycle_complete"], "no cycle_complete written"


# ---- health.py, which feeds the Docker HEALTHCHECK and /healthz ----

def test_health_flags_a_cycle_that_never_completed(tmp_path, monkeypatch, cfg):
    import health
    from ledger import Ledger

    monkeypatch.chdir(tmp_path)
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    Ledger(cfg["memory"]["ledger_path"]).log_event("cycle_crashed", "boom")
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / "heartbeat").write_text(
        datetime.now(timezone.utc).isoformat() + "\n")

    now = datetime(2026, 7, 27, 20, tzinfo=timezone.utc)   # a Monday
    monkeypatch.setattr(health, "heartbeat_age_hours", lambda now=None: 0.1)
    out = health.status(cfg, now=now)
    assert any("never completed" in p for p in out["problems"]), out["problems"]
    assert out["healthy"] is False


# ---- the logging side effect that erased the evidence ----

def test_importing_main_does_not_touch_the_real_log_files():
    """14 test files import `main`. While logging was configured at import
    time, every pytest run appended the whole session to logs/agent.jsonl --
    240 records in one run -- and logs/agent.log stayed 0 bytes because
    pytest owns the root logger first. That is why 07-24 left no stderr."""
    import logging
    import main
    assert hasattr(main, "configure_logging")
    for h in logging.getLogger().handlers:
        assert not getattr(h, "_repete_json", False), (
            "importing main attached the production JSON log handler")
