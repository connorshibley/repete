"""Dead-man watchdog: missed cycles and HALT states must be detected;
weekends stay quiet. All offline — osascript is never invoked here."""
from datetime import date, datetime, timedelta, timezone

import watchdog

MONDAY = date(2026, 7, 13)
SATURDAY = date(2026, 7, 18)


def _write_heartbeat(tmp_path, when: datetime) -> str:
    p = tmp_path / "heartbeat"
    p.write_text(when.isoformat() + "\n")
    return str(p)


def _completed(day: date):
    """Ledger records for a HEALTHY `day`: the cycle finished, and it had news.

    `market_context` joined the all-clear set on 2026-07-29 (W6-A3) — the
    watchdog now also asks whether the bot knew anything while it traded. A
    fixture carrying only `cycle_complete` describes a day with a real problem
    now, which is asserted in tests/test_news_sources_are_accounted_for.py
    rather than here.
    """
    return [{"type": "event", "event": ev,
             "ts": datetime(day.year, day.month, day.day, 19, 50,
                            tzinfo=timezone.utc).isoformat()}
            for ev in ("cycle_complete", "market_context")]


def test_fresh_heartbeat_and_a_completed_cycle_is_all_clear(tmp_path):
    """All-clear needs all three signals: the process ran, it finished, and it
    had a market read.

    Two things were wrong with the version this replaces. It asserted a fresh
    heartbeat ALONE was sufficient — which is the 2026-07-24 bug stated as a
    guarantee. And it used `date.today()`, so on a weekend the weekday branch
    never executed and the test asserted nothing at all; it would have gone
    red the next Monday morning in CI for reasons unrelated to any change.
    Both the date and the ledger are pinned here.
    """
    hb = _write_heartbeat(
        tmp_path, datetime(MONDAY.year, MONDAY.month, MONDAY.day, 19, 50,
                           tzinfo=timezone.utc))
    assert watchdog.check(today=MONDAY,
                          heartbeat_path=hb,
                          halt_path=str(tmp_path / "HALT"),
                          records=_completed(MONDAY)) == []


def test_missing_heartbeat_flagged_on_weekday(tmp_path):
    problems = watchdog.check(today=MONDAY,
                              heartbeat_path=str(tmp_path / "heartbeat"),
                              halt_path=str(tmp_path / "HALT"))
    assert len(problems) == 1 and "no heartbeat" in problems[0]


def test_stale_heartbeat_flagged_on_weekday(tmp_path):
    # Anchor the heartbeat to the fixed check date (not real `now`), so the
    # stale gap is deterministic regardless of when the suite runs.
    hb = _write_heartbeat(
        tmp_path,
        datetime(MONDAY.year, MONDAY.month, MONDAY.day, tzinfo=timezone.utc)
        - timedelta(days=3))
    problems = watchdog.check(today=MONDAY,
                              heartbeat_path=hb,
                              halt_path=str(tmp_path / "HALT"))
    assert problems and "did NOT run" in problems[0]


def test_weekend_missing_heartbeat_is_quiet(tmp_path):
    assert watchdog.check(today=SATURDAY,
                          heartbeat_path=str(tmp_path / "heartbeat"),
                          halt_path=str(tmp_path / "HALT")) == []


def test_halt_flagged_even_on_weekend(tmp_path):
    halt = tmp_path / "HALT"
    halt.write_text("kill switch fired\n")
    problems = watchdog.check(today=SATURDAY,
                              heartbeat_path=str(tmp_path / "heartbeat"),
                              halt_path=str(halt))
    assert len(problems) == 1 and "HALT" in problems[0]


def test_corrupt_heartbeat_treated_as_missing(tmp_path):
    p = tmp_path / "heartbeat"
    p.write_text("not-a-timestamp\n")
    problems = watchdog.check(today=MONDAY, heartbeat_path=str(p),
                              halt_path=str(tmp_path / "HALT"))
    assert problems and "no heartbeat" in problems[0]


def test_run_cycle_writes_heartbeat_even_when_halted(tmp_path, monkeypatch, cfg):
    """The heartbeat means 'the process ran' — a halted cycle still writes it."""
    import os
    import yaml
    import main
    import risk

    monkeypatch.chdir(tmp_path)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    risk.engage_halt("test")
    main.run_cycle()

    assert os.path.exists(main.HEARTBEAT_FILE)
    hb = watchdog.heartbeat_date(main.HEARTBEAT_FILE)
    assert hb == date.today()
