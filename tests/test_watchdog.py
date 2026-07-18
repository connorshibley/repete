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


def test_fresh_heartbeat_all_clear(tmp_path):
    hb = _write_heartbeat(tmp_path, datetime.now(timezone.utc))
    assert watchdog.check(today=date.today(),
                          heartbeat_path=hb,
                          halt_path=str(tmp_path / "HALT")) == []


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
