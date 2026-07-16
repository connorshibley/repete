from datetime import datetime, timezone

import pytest

import counterfactual as cf


DECISION = "2026-01-05T21:00:00+00:00"


def _bar(day, o, h, l, c):
    return {"ts": f"2026-01-{day:02d}T21:00:00+00:00", "open": o, "high": h,
            "low": l, "close": c, "volume": 1000}


def test_stop_fires_pessimistically_when_both_legs_touch():
    # backtest.simulate parity: stop wins when stop AND tp touch in one bar
    bars = [_bar(6, 100, 120, 80, 110)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "stop_loss"
    assert r["pnl_pct"] == pytest.approx(-10.0)


def test_take_profit_fires_when_stop_untouched():
    bars = [_bar(6, 100, 116, 95, 110)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "take_profit"
    assert r["pnl_pct"] == pytest.approx(15.0)


def test_horizon_close_when_no_leg_fires():
    bars = [_bar(6, 100, 104, 97, 103), _bar(7, 103, 105, 100, 104)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "horizon_close"
    assert r["pnl_pct"] == pytest.approx(4.0)


def test_bars_on_decision_day_excluded_and_beyond_horizon_ignored():
    bars = [_bar(5, 100, 200, 50, 100),   # decision day itself: must not count
            _bar(6, 100, 101, 99, 100),
            _bar(20, 100, 200, 50, 150)]  # beyond 7d horizon: ignored
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "horizon_close" and r["pnl_pct"] == 0.0


def test_no_bars_in_window_returns_none():
    assert cf.simulate_veto_counterfactual([], DECISION, 100.0, 90.0, 115.0, 7) is None


def test_no_brackets_exits_at_horizon_close():
    bars = [_bar(6, 100, 130, 70, 95)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=None,
                                        tp=None, horizon_days=7)
    assert r["exit_reason"] == "horizon_close" and r["pnl_pct"] == -5.0


def test_resolution_due_embargo():
    now_early = datetime(2026, 1, 10, tzinfo=timezone.utc)   # 5 days after
    now_due = datetime(2026, 1, 13, tzinfo=timezone.utc)     # 8 days after
    assert not cf.resolution_due(DECISION, 2, 5, now_early)  # needs 7
    assert cf.resolution_due(DECISION, 2, 5, now_due)
