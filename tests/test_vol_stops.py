"""Vol-regime-conditioned stop widths (param-gated: absent config keeps the
fixed multiplier everywhere)."""
import math

import backtest
import risk

from conftest import make_bars


def _cfg(base=2.0, high=0.0):
    return {"risk": {"brackets": {"enabled": True, "atr_period": 14,
                                  "stop_atr_mult": base,
                                  "take_profit_atr_mult": 0,
                                  **({"stop_atr_mult_high_vol": high}
                                     if high else {})}}}


def test_base_mult_without_param():
    stop, tp = risk.bracket_prices(100.0, 2.0, _cfg())
    assert stop == 96.0 and tp is None


def test_high_vol_widens_stop_only_in_high_bucket():
    cfg = _cfg(base=2.0, high=3.0)
    assert risk.bracket_prices(100.0, 2.0, cfg, vol_bucket="high")[0] == 94.0
    assert risk.bracket_prices(100.0, 2.0, cfg, vol_bucket="low")[0] == 96.0
    assert risk.bracket_prices(100.0, 2.0, cfg, vol_bucket=None)[0] == 96.0


def test_param_set_but_bucket_unknown_fails_open():
    cfg = _cfg(base=2.0, high=3.0)
    stop, _ = risk.bracket_prices(100.0, 2.0, cfg)  # no bucket passed
    assert stop == 96.0


def test_vol_bucket_series_no_lookahead_and_buckets():
    rcfg = {"sma_period": 5, "vol_period": 5, "vol_low": 0.15,
            "vol_high": 0.25}
    calm = [100 * math.exp(0.001 * i) for i in range(10)]      # tiny drift
    wild = [calm[-1] * (1.25 if i % 2 else 0.8) for i in range(6)]
    bars = make_bars(calm + wild)
    series = backtest.vol_bucket_series(bars, rcfg)
    assert series[bars[9]["ts"]] == "low"     # calm era: low vol
    assert series[bars[-1]["ts"]] == "high"   # wild era: high vol
    # early bars with insufficient history simply have no entry
    assert bars[0]["ts"] not in series
