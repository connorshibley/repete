import pytest

import strategy


def _bar(h, l, c):
    return {"ts": "2026-01-01T21:00:00+00:00", "open": c, "high": h, "low": l,
            "close": c, "volume": 1000}


def test_true_range_plain_range():
    assert strategy.true_range(11.0, _bar(13, 11, 12)) == 2.0


def test_true_range_gap_up():
    # gap above prev close: |high - prev_close| dominates
    assert strategy.true_range(10.0, _bar(15, 12, 14)) == 5.0


def test_true_range_gap_down():
    assert strategy.true_range(20.0, _bar(15, 12, 13)) == 8.0


def test_true_range_inside_bar():
    assert strategy.true_range(10.0, _bar(10.5, 9.5, 10.0)) == 1.0


def test_atr_hand_computed():
    bars = [_bar(12, 10, 11), _bar(13, 11, 12), _bar(15, 12, 14), _bar(14, 10, 11)]
    # TRs (needing prev close): 2, 3, 4 -> ATR(3) = 3.0
    assert strategy.atr(bars, period=3) == pytest.approx(3.0)


def test_atr_none_on_short_history():
    bars = [_bar(12, 10, 11), _bar(13, 11, 12)]
    assert strategy.atr(bars, period=3) is None
