"""Regression: Alpaca's API-side `limit` truncates from the OLDEST end of the
requested window, so a large lookback silently returned months-stale bars.
bars() must fetch the whole window and slice the most RECENT `limit` rows."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from broker import Broker


def _fake_rows(n):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [SimpleNamespace(timestamp=base + timedelta(days=i), open=100 + i,
                            high=101 + i, low=99 + i, close=100 + i, volume=1000)
            for i in range(n)]


class FakeData:
    def __init__(self, rows):
        self.rows = rows
        self.last_request = None

    def get_stock_bars(self, req):
        self.last_request = req
        return SimpleNamespace(data={"SPY": self.rows})


def _broker_with(rows):
    b = Broker.__new__(Broker)  # skip __init__: no keys/network needed
    b.data = FakeData(rows)
    return b


def test_bars_returns_most_recent_limit_rows():
    b = _broker_with(_fake_rows(400))
    bars = b.bars("SPY", "1Day", 253)
    assert len(bars) == 253
    assert bars[-1]["close"] == 499  # newest row, not row 252
    assert bars[0]["close"] == 247


def test_bars_no_api_side_limit_sent():
    b = _broker_with(_fake_rows(10))
    b.bars("SPY", "1Day", 5)
    assert b.data.last_request.limit is None  # window fetch, sliced client-side


def test_bars_short_history_returned_whole():
    b = _broker_with(_fake_rows(3))
    assert len(b.bars("SPY", "1Day", 253)) == 3
