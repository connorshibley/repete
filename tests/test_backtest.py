"""Backtest harness — synthetic bars only, fully offline."""
import json

import pytest

import backtest
from backtest import SimTrade


def _bar(day, o, h, l, c):
    return {"ts": f"2026-01-{day:02d}T21:00:00+00:00", "open": o, "high": h,
            "low": l, "close": c, "volume": 1000}


def flat_bars(closes, start_day=1):
    return [_bar(start_day + i, c, c, c, c) for i, c in enumerate(closes)]


# ------------------------------------------------------------------- metrics

def test_max_drawdown_hand_computed():
    assert backtest.max_drawdown([100, 120, 90, 110]) == pytest.approx(25.0)


def test_max_drawdown_monotonic_curve_is_zero():
    assert backtest.max_drawdown([100, 110, 120]) == 0.0


def _trade(pnl):
    return SimTrade("S", "t", 10.0, 1, exit_price=10.0 + pnl)


def test_profit_factor_hand_computed():
    assert backtest.profit_factor([_trade(10), _trade(30), _trade(-20)]) == 2.0


def test_profit_factor_inf_safe():
    assert backtest.profit_factor([_trade(10)]) == float("inf")
    assert backtest.profit_factor([]) == 0.0


def test_buy_and_hold_hand_computed():
    bars = flat_bars([9, 10, 10.5, 11])
    # buy 2nd open (10), sell last close (11), no slip/fee, cash 1000 -> +10%
    assert backtest.buy_and_hold_return(bars, 0, 0, cash=1000) == pytest.approx(10.0)


# --------------------------------------------------------------- bar loading

def test_load_bars_json_round_trip(tmp_path):
    bars = {"SPY": flat_bars([10, 11])}
    path = tmp_path / "bars.json"
    path.write_text(json.dumps(bars))
    assert backtest.load_bars_file(str(path)) == bars


def test_load_bars_csv(tmp_path):
    path = tmp_path / "bars.csv"
    path.write_text("symbol,ts,open,high,low,close,volume\n"
                    "SPY,2026-01-01T21:00:00+00:00,10,11,9,10.5,100\n")
    out = backtest.load_bars_file(str(path))
    assert out["SPY"][0]["close"] == 10.5 and out["SPY"][0]["low"] == 9


# ---------------------------------------------------------------- simulation

def test_single_round_trip_exact_arithmetic(cfg):
    """Crossover buy fills at NEXT bar's open with slippage; strategy sell after
    the holding period; every number hand-computed."""
    closes = [10, 10, 10, 10, 10, 10, 9, 9, 9, 20, 20, 20, 20, 2, 2]
    r = backtest.simulate({"SPY": flat_bars(closes)}, cfg, {}, start_cash=100_000)

    assert r.n_trades == 1
    t = r.trades[0]
    # signal on close of bar 9 (20), fill at bar 10 open 20 * (1 + 5bps) = 20.01
    assert t.entry_price == pytest.approx(20.01)
    # 1% of 100k = $1000 -> int(1000 / 20.01) = 49 shares
    assert t.qty == 49
    # cross-down on bar 13, held >= 2 days, sells at bar 14 open 2 * (1 - 5bps)
    assert t.exit_price == pytest.approx(1.999)
    assert t.exit_reason == "strategy_sell"
    assert r.total_return_pct == pytest.approx(
        (1.999 - 20.01) * 49 / 100_000 * 100, abs=1e-3)


def test_no_lookahead_buy_never_fills_on_signal_bar(cfg):
    # data ends on the signal bar -> order can never fill
    closes = [10, 10, 10, 10, 10, 10, 9, 9, 9, 20]
    r = backtest.simulate({"SPY": flat_bars(closes)}, cfg, {})
    assert r.n_trades == 0


def test_sim_swing_guard_blocks_young_exit(cfg):
    cfg["strategy"].update(fast_period=2, slow_period=3)
    # buy signal bar 5 -> fill bar 6; sell signal bar 7 (1 day held) -> skipped
    closes = [10, 10, 10, 8, 8, 20, 20, 5, 5, 5]
    r = backtest.simulate({"SPY": flat_bars(closes)}, cfg, {})
    assert r.n_guard_skipped_exits == 1
    assert r.n_trades == 1
    assert r.trades[0].exit_reason == "end_of_data"  # liquidated at the end


def _bracket_cfg(cfg):
    cfg["strategy"].update(fast_period=2, slow_period=3)
    cfg["risk"]["brackets"].update(enabled=True, atr_period=2,
                                   stop_atr_mult=1.0, take_profit_atr_mult=2.0)
    return cfg


def test_stop_loss_fires_intrabar(cfg):
    cfg = _bracket_cfg(cfg)
    bars = flat_bars([10, 10, 10, 8, 8, 20, 20])
    # entry fills bar 6 (open 20 -> 20.01); ATR(2) over bars 5-6 = (12+0)/2 = 6
    # stop = 20.01 - 6 = 14.01; bar 7's low pierces it
    bars.append(_bar(8, 18, 20, 14, 18))
    r = backtest.simulate({"SPY": bars}, cfg, {})
    t = r.trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.exit_price == pytest.approx(14.01 * (1 - 5 / 1e4))


def test_stop_wins_when_both_legs_touch(cfg):
    cfg = _bracket_cfg(cfg)
    bars = flat_bars([10, 10, 10, 8, 8, 20, 20])
    # tp = 20.01 + 12 = 32.01; this bar touches BOTH legs -> stop wins
    bars.append(_bar(8, 18, 40, 14, 30))
    r = backtest.simulate({"SPY": bars}, cfg, {})
    assert r.trades[0].exit_reason == "stop_loss"


def test_take_profit_fires_when_stop_untouched(cfg):
    cfg = _bracket_cfg(cfg)
    bars = flat_bars([10, 10, 10, 8, 8, 20, 20])
    bars.append(_bar(8, 30, 40, 25, 35))
    r = backtest.simulate({"SPY": bars}, cfg, {})
    t = r.trades[0]
    assert t.exit_reason == "take_profit"
    assert t.exit_price == pytest.approx(32.01 * (1 - 5 / 1e4))


def test_simulate_never_mutates_caller_config(cfg):
    before = json.dumps(cfg, sort_keys=True)
    backtest.simulate({"SPY": flat_bars([10] * 12)}, cfg,
                      {"fast_period": 2, "slow_period": 3, "stop_atr_mult": 1.0})
    assert json.dumps(cfg, sort_keys=True) == before


# -------------------------------------------------------------- walk-forward

def test_walk_forward_logs_every_variant(cfg, tmp_path):
    trials = tmp_path / "trials.jsonl"
    closes = [10, 10, 10, 10, 10, 10, 9, 9, 9, 20, 20, 20, 20, 2, 2, 3, 4, 5, 6, 7]
    grid = backtest.build_grid([2, 3], [5], [0.0], [0.0])  # 2 valid variants
    out = backtest.walk_forward({"SPY": flat_bars(closes)}, cfg, grid,
                                split=0.7, trials_path=str(trials))
    lines = [json.loads(l) for l in trials.read_text().splitlines()]
    assert len(lines) == 3  # 2 in-sample + 1 oos
    assert [l["phase"] for l in lines] == ["in_sample", "in_sample", "oos"]
    best = {k: v for k, v in out["best_params"].items() if k != "strategy"}
    assert best in grid
    assert out["best_params"]["strategy"] == "ma_crossover"  # trials are tagged
    assert out["oos_result"]["params"] == out["best_params"]


def test_build_grid_skips_fast_ge_slow():
    grid = backtest.build_grid([5, 30], [10, 30], [0.0], [0.0])
    assert {(g["fast_period"], g["slow_period"]) for g in grid} == {(5, 10), (5, 30)}


def test_build_grid_empty_raises():
    with pytest.raises(SystemExit):
        backtest.build_grid([30], [10], [0.0], [0.0])


def test_build_generic_grid_cross_product():
    grid = backtest.build_generic_grid([["momentum_bars", "3", "5"],
                                        ["trend_sma_period", "8"]],
                                       [2.0], [0.0])
    assert len(grid) == 2
    assert grid[0]["momentum_bars"] == 3 and grid[0]["trend_sma_period"] == 8
    assert grid[0]["stop_atr_mult"] == 2.0


def test_simulate_named_strategy_tsmom(cfg):
    # steady uptrend: tsmom enters and rides to end_of_data
    closes = [100 + i for i in range(30)]
    bars = flat_bars(closes)
    r = backtest.simulate({"SPY": bars}, cfg,
                          {"momentum_bars": 3, "skip_bars": 0,
                           "trend_sma_period": 5},
                          strategy_name="tsmom")
    assert r.params["strategy"] == "tsmom"
    assert r.n_trades == 1
    assert r.trades[0].exit_reason == "end_of_data"
    assert r.total_return_pct > 0


def test_simulate_cross_sectional_xsmom(cfg):
    def series(daily_ret, n=30):
        closes, p = [], 100.0
        for _ in range(n):
            p *= (1 + daily_ret)
            closes.append(round(p, 4))
        return flat_bars(closes)
    sym_bars = {"WIN": series(0.01), "MID": series(0.002),
                "FLAT": series(0.0), "LOSE": series(-0.005)}
    r = backtest.simulate(sym_bars, cfg,
                          {"rank_lookback_bars": 5, "skip_bars": 1,
                           "buy_top_fraction": 0.25, "exit_below_fraction": 0.5},
                          strategy_name="xsmom")
    assert r.n_trades >= 1
    assert all(t.symbol == "WIN" for t in r.trades)  # only the leader entered


def test_enablement_gate_exposure_matched():
    # profitable strategy at 5% deployment: trails raw B&H massively but
    # beats the exposure-matched benchmark (40% x 0.05 = 2%)
    oos = {"total_return_pct": 2.4, "n_trades": 50, "profit_factor": 3.0,
           "buy_hold_return_pct": 40.0, "max_drawdown_pct": 1.0,
           "buy_hold_max_drawdown_pct": 20.0, "avg_deployment_pct": 5.0}
    ok, reasons = backtest.enablement_gate(oos)
    assert ok, reasons
    # same shape but returns below even the exposure-matched bar -> fail
    ok, reasons = backtest.enablement_gate({**oos, "total_return_pct": 1.0})
    assert not ok and any("exposure-matched" in r for r in reasons)


def test_enablement_gate_truth_table():
    base = {"total_return_pct": 10.0, "n_trades": 20, "profit_factor": 1.5,
            "buy_hold_return_pct": 8.0, "max_drawdown_pct": 5.0,
            "buy_hold_max_drawdown_pct": 20.0, "avg_deployment_pct": 100.0}
    ok, reasons = backtest.enablement_gate(base)
    assert ok and reasons == []

    for patch, frag in [({"total_return_pct": -1.0}, "not positive"),
                        ({"n_trades": 10}, "need >=15"),
                        ({"profit_factor": 1.1}, "< 1.3")]:
        ok, reasons = backtest.enablement_gate({**base, **patch})
        assert not ok and any(frag in r for r in reasons)

    # trails B&H but qualifies risk-adjusted (70% of return, half the drawdown)
    ra = {**base, "total_return_pct": 6.0, "max_drawdown_pct": 5.0}
    ok, _ = backtest.enablement_gate(ra)
    assert ok

    # trails B&H and drawdown too deep -> fail
    bad = {**base, "total_return_pct": 6.0, "max_drawdown_pct": 15.0}
    ok, reasons = backtest.enablement_gate(bad)
    assert not ok and any("beats neither" in r for r in reasons)
