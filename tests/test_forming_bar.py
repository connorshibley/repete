"""The forming-bar guard behind the 09:35 open cycle (datacheck.drop_forming_bar).

This is the load-bearing piece of the open cycle. `broker.bars()` windows its
request to *now*, so mid-session the last "daily bar" is today's PARTIAL bar —
at 09:35 a five-minute-old stub whose close is just the current price. Feeding
that to RSI(2)/SMA200 would mean trading an input no backtest has measured.

If these tests ever go green while the guard is broken, the open cycle silently
becomes partial-bar trading, which is the exact thing config.yaml:63 warns
against.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import datacheck

ET = ZoneInfo("America/New_York")


def bars(*dates):
    return [{"ts": f"{d}T21:00:00+00:00", "open": 1.0, "high": 1.0,
             "low": 1.0, "close": 1.0, "volume": 1} for d in dates]


def test_drops_todays_bar_during_the_session():
    b = bars("2026-07-20", "2026-07-21", "2026-07-22")
    now = datetime(2026, 7, 22, 9, 35, tzinfo=ET)
    out = datacheck.drop_forming_bar(b, now)
    assert len(out) == 2
    assert out[-1]["ts"].startswith("2026-07-21")


def test_keeps_todays_bar_after_the_close():
    """At 16:00 ET the daily bar is final — the 15:45 cycle's behaviour must
    not change, and neither must any post-close run."""
    b = bars("2026-07-21", "2026-07-22")
    now = datetime(2026, 7, 22, 16, 0, tzinfo=ET)
    assert datacheck.drop_forming_bar(b, now) == b


def test_keeps_todays_bar_exactly_at_the_close_boundary():
    b = bars("2026-07-22")
    assert datacheck.drop_forming_bar(
        b, datetime(2026, 7, 22, 16, 0, tzinfo=ET)) == b
    assert datacheck.drop_forming_bar(
        b, datetime(2026, 7, 22, 15, 59, tzinfo=ET)) == []


def test_1545_cycle_would_still_be_trimmed_if_it_opted_in():
    """15:45 is before the close, so the guard WOULD trim there. That is why
    the 15:45 job does not pass --open-cycle: at 15 minutes to the bell the
    forming bar is effectively the close, which is what the gates measured."""
    b = bars("2026-07-21", "2026-07-22")
    now = datetime(2026, 7, 22, 15, 45, tzinfo=ET)
    assert len(datacheck.drop_forming_bar(b, now)) == 1


def test_no_trim_when_the_last_bar_is_not_today():
    """Monday morning: the newest completed bar is Friday's. Nothing to drop —
    trimming here would throw away a real bar and shorten every lookback."""
    b = bars("2026-07-16", "2026-07-17")
    now = datetime(2026, 7, 20, 9, 35, tzinfo=ET)
    assert datacheck.drop_forming_bar(b, now) == b


def test_weekend_leaves_bars_untouched():
    b = bars("2026-07-16", "2026-07-17")
    now = datetime(2026, 7, 18, 11, 0, tzinfo=ET)   # Saturday
    assert datacheck.drop_forming_bar(b, now) == b


def test_empty_list_is_safe():
    assert datacheck.drop_forming_bar([], datetime(2026, 7, 22, 9, 35, tzinfo=ET)) == []


def test_naive_and_utc_now_are_converted_to_et():
    """13:35 UTC is 09:35 ET — mid-session, so today's bar must go. A caller
    passing UTC must not accidentally get post-close semantics."""
    from datetime import timezone
    b = bars("2026-07-21", "2026-07-22")
    utc_now = datetime(2026, 7, 22, 13, 35, tzinfo=timezone.utc)
    assert len(datacheck.drop_forming_bar(b, utc_now)) == 1


def test_scheduler_registers_the_open_cycle_before_the_close_cycle():
    """Order and flags matter: the open cycle must pass --open-cycle and the
    15:45 cycle must NOT."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "scheduler.py")
    spec = importlib.util.spec_from_file_location("sched_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    jobs = {j[0]: j for j in mod.JOBS}
    assert "open-cycle" in jobs, "the 09:35 open cycle is not scheduled"
    name, weekdays, hour, minute, argv = jobs["open-cycle"]
    assert (hour, minute) == (9, 35)
    assert list(weekdays) == [0, 1, 2, 3, 4]
    assert "--open-cycle" in " ".join(argv)

    _, _, c_hour, c_min, c_argv = jobs["cycle"]
    assert (c_hour, c_min) == (15, 45)
    assert "--open-cycle" not in " ".join(c_argv)
