"""The cycle records how long it took and how much room was left.

MARGIN, NOT DURATION. The cycle fires at 15:45 ET against a 16:00 close, so the
budget is fifteen minutes — but duration alone misses the case that actually
bites. On 2026-07-30 the laptop woke and launchd fired three backlogged jobs at
once; a cycle that STARTS at 15:58 and takes two minutes is fast and still
finishes after the bell. `test_a_fast_cycle_that_started_late_still_alarms` is
the test that encodes that distinction.

A late order is not rejected — Alpaca queues a DAY order placed after the close
for the next session's open (divergence #18), silently turning a same-close fill
into a next-open one. Nothing here fixes that; these tests pin the visibility.
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import main
from ledger import Ledger

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def _cfg(margin=5):
    return {"ops": {"min_close_margin_min": margin}}


def _events(led, name=None):
    return [r for r in led.all_records()
            if r.get("type") == "event" and (name is None or r.get("event") == name)]


# --- the alarm ---------------------------------------------------------------

def test_a_thin_margin_alarms(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    # Wednesday 15:58 ET — two minutes to the bell.
    main._alarm_on_thin_margin(_cfg(), led, 2.0, 130.0, _et(2026, 8, 5, 15, 58))
    assert len(_events(led, "cycle_margin_low")) == 1


def test_a_comfortable_margin_is_silent(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, 12.0, 130.0, _et(2026, 8, 5, 15, 48))
    assert _events(led, "cycle_margin_low") == []


def test_a_fast_cycle_that_started_late_still_alarms(tmp_path):
    """The 2026-07-30 shape, and the reason this measures margin.

    120 seconds is FASTER than the median cycle. It is also 15:59, so the book
    is being traded into the bell. A duration threshold would have said nothing.
    """
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, 1.0, 120.0, _et(2026, 8, 5, 15, 59))
    assert len(_events(led, "cycle_margin_low")) == 1


def test_finishing_after_the_bell_alarms(tmp_path):
    """A negative margin is the worst case, not an excluded one."""
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, -6.0, 1200.0, _et(2026, 8, 5, 16, 6))
    assert len(_events(led, "cycle_margin_low")) == 1


def test_an_evening_manual_run_does_not_page(tmp_path):
    """22:00 ET is a person at a keyboard, not a missed close. Its margin is
    -360 minutes; without the band guard every manual run would alert."""
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, -360.0, 90.0, _et(2026, 8, 5, 22, 0))
    assert _events(led, "cycle_margin_low") == []


def test_a_weekend_run_does_not_page(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    # Saturday 2026-08-08
    main._alarm_on_thin_margin(_cfg(), led, 2.0, 130.0, _et(2026, 8, 8, 15, 58))
    assert _events(led, "cycle_margin_low") == []


def test_zero_disables_it(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(margin=0), led, 1.0, 130.0,
                               _et(2026, 8, 5, 15, 59))
    assert _events(led, "cycle_margin_low") == []


def test_it_alarms_once_per_day(tmp_path):
    """Same idiom as check_deploy_drift. A per-cycle alert on a condition that
    persists is how a channel gets muted."""
    led = Ledger(str(tmp_path / "l.jsonl"))
    for _ in range(3):
        main._alarm_on_thin_margin(_cfg(), led, 2.0, 130.0,
                                   _et(2026, 8, 5, 15, 58))
    assert len(_events(led, "cycle_margin_low")) == 1


def test_it_is_not_a_degradation_event(tmp_path):
    """`degradation` is counted against ops.max_degradations_per_day. A timing
    signal must not spend the fail-open error budget, and must not shift
    tests/test_main_cycle.py::test_degradation_slo_breach_logged_once."""
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, 2.0, 130.0, _et(2026, 8, 5, 15, 58))
    assert _events(led, "degradation") == []


def test_the_ledger_record_survives_a_dead_alert_channel(tmp_path, monkeypatch):
    """Ledger first, alert second. If the channel is down the finding must
    still be reconstructable from the ledger alone."""
    import alerting
    monkeypatch.setattr(alerting, "send",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    led = Ledger(str(tmp_path / "l.jsonl"))
    main._alarm_on_thin_margin(_cfg(), led, 2.0, 130.0, _et(2026, 8, 5, 15, 58))
    assert len(_events(led, "cycle_margin_low")) == 1


# --- the measurement ---------------------------------------------------------

def test_every_cycle_records_its_timing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "memory:\n  ledger_path: l.jsonl\nops:\n  min_close_margin_min: 5\n")
    monkeypatch.setattr(main.time, "monotonic", lambda: 1000.0)
    main.record_cycle_timing(880.0)          # 120 s earlier
    rec = _events(Ledger(str(tmp_path / "l.jsonl")), "cycle_timing")
    assert len(rec) == 1
    payload = json.loads(rec[0]["detail"])
    assert payload["duration_s"] == 120.0
    assert "margin_min" in payload and "finished_at_et" in payload


def test_timing_never_raises_even_with_no_config(tmp_path, monkeypatch):
    """It runs inside `run_cycle`'s `finally:`, possibly with an exception
    already in flight. Measurement must never replace a real failure."""
    monkeypatch.chdir(tmp_path)              # no config.yaml here at all
    main.record_cycle_timing(0.0)            # must not raise
