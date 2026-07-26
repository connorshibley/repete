"""Position mark refresher — marks only, never trades.

The dashboard has shown value and unrealized ±% since 2026-07-25, but only the
trading cycle and the post job ever wrote a mark, so the numbers stopped moving
between 09:35 and 15:45 and all weekend. Measured 2026-07-26: 20.9 hours stale.
This module keeps them current; these tests keep it harmless.
"""
import inspect
import os

import pytest

import mark_positions
from ledger import Ledger


class FakeBroker:
    """Only `positions()` exists. Any other call is an AttributeError, which
    is the point — the module must not be reaching for anything else."""

    def __init__(self, positions=None, boom=False):
        self._p = positions or {}
        self._boom = boom
        self.calls = []

    def positions(self):
        self.calls.append("positions")
        if self._boom:
            raise RuntimeError("alpaca 503")
        return self._p


BOOK = {
    "SPY": {"qty": 1, "avg_entry": 753.14, "market_value": 738.93,
            "unrealized_pl": -14.21},
    "XLV": {"qty": 6, "avg_entry": 162.48, "market_value": 975.42,
            "unrealized_pl": 0.54},
}


@pytest.fixture
def led(tmp_path):
    return Ledger(str(tmp_path / "ledger.jsonl"))


# ---- it does the job ----

def test_writes_a_mark_and_summarises_the_book(led):
    out = mark_positions.refresh({}, FakeBroker(BOOK), led)
    assert out["ok"] and out["n"] == 2
    assert out["value"] == pytest.approx(738.93 + 975.42)
    assert out["unrealized"] == pytest.approx(-14.21 + 0.54)
    marks = [r for r in led.all_records()
             if r.get("event") == "positions_mark"]
    assert len(marks) == 1


def test_the_mark_is_what_the_dashboard_reads(led):
    """End to end: refresh -> ledger -> the dashboard's own reader."""
    import dashboard
    mark_positions.refresh({}, FakeBroker(BOOK), led)
    mark, ts = dashboard.latest_position_mark(led.all_records())
    assert sorted(mark) == ["SPY", "XLV"]
    assert mark["SPY"]["market_value"] == 738.93
    assert ts


def test_only_the_read_endpoint_is_touched(led):
    """One broker call, and it is a GET. FakeBroker exposes nothing else, so
    any reach for another endpoint would be an AttributeError."""
    b = FakeBroker(BOOK)
    mark_positions.refresh({}, b, led)
    assert b.calls == ["positions"], b.calls


# ---- it stays harmless ----

def test_a_flat_book_is_reported_as_flat_not_as_a_successful_mark(led):
    """A flat book is a real state, not a failure, and must read as one.

    Note what this does and does not prove. `Ledger.log_positions_mark` ALSO
    returns early on an empty dict, so no mark is written either way — the
    module's own guard cannot change that, and a test asserting only "no
    record was written" would pass with the guard deleted. It would be
    testing the ledger, not this file.

    What the guard actually buys is the distinction in the RETURN value: the
    operator log says "book is flat" instead of silently reporting a
    successful mark of zero positions. That is what is asserted here.
    """
    out = mark_positions.refresh({}, FakeBroker({}), led)
    assert out["ok"] and out["n"] == 0
    assert "flat" in out.get("reason", ""), (
        "a flat book must be reported as flat, not as a successful mark")
    assert not [r for r in led.all_records()
                if r.get("event") == "positions_mark"]


def test_a_broker_failure_never_raises(led):
    """A cosmetic snapshot must not be able to kill a scheduled job."""
    out = mark_positions.refresh({}, FakeBroker(boom=True), led)
    assert out["ok"] is False
    assert "503" in out["reason"]


def test_module_holds_no_order_call_path():
    """Structural, like opportunity_scan's. This one DOES take a broker — it
    cannot promise safety by refusing the import — so the guarantee is that no
    order verb appears anywhere in the file."""
    src = inspect.getsource(mark_positions)
    body = src.split('"""', 2)[-1]          # ignore the docstring's prose
    for verb in ("market_order", "bracket_market_order", "submit_order",
                 "flatten_all", "cancel_open_orders", "replace_stop",
                 "close_position"):
        assert verb not in body, f"mark_positions must not be able to {verb}"


def test_it_never_reads_a_mark_back_into_a_decision():
    """Display state only. If this module ever grew signal or risk imports it
    would have stopped being a viewer."""
    src = inspect.getsource(mark_positions)
    body = src.split('"""', 2)[-1]
    for mod in ("import strategies", "import risk", "import strategy",
                "generate_signal", "size_order"):
        assert mod not in body, f"mark_positions must not touch {mod}"


# ---- the schedule ----

def test_scheduler_runs_it_hourly_through_the_session():
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched", os.path.join(here, "scripts", "scheduler.py"))
    sched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sched)

    job = next(j for j in sched.JOBS if j[0] == "mark-book")
    assert job[3] == 30, "runs at :30, off the hour the cycles use"
    assert list(job[2]) == list(range(10, 16))
    assert list(job[1]) == list(range(0, 5)), "weekdays only"


def test_hour_windows_do_not_break_the_fixed_hour_jobs():
    """due() grew an hour-window branch; the int path must be untouched."""
    import importlib.util
    from datetime import datetime
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched2", os.path.join(here, "scripts", "scheduler.py"))
    sched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sched)

    cycle = next(j for j in sched.JOBS if j[0] == "cycle")      # 15:45 int
    mon = datetime(2026, 7, 27, 15, 45)
    assert sched.due(cycle, mon)
    assert not sched.due(cycle, datetime(2026, 7, 27, 14, 45))

    mark = next(j for j in sched.JOBS if j[0] == "mark-book")
    assert sched.due(mark, datetime(2026, 7, 27, 11, 30))
    assert not sched.due(mark, datetime(2026, 7, 27, 9, 30))    # before window
    assert not sched.due(mark, datetime(2026, 7, 27, 16, 30))   # after window
    assert not sched.due(mark, datetime(2026, 8, 1, 11, 30))    # Saturday
