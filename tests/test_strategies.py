"""New strategy modules + registry — deterministic fixtures, no network."""
import pytest

import strategies
from strategies import tsmom, xsmom, meanrev


def _long_bars(closes):
    """Daily bars across months (make_bars only spans one month)."""
    from datetime import date, timedelta
    bars = []
    d = date(2025, 1, 1)
    for c in closes:
        bars.append({"ts": f"{d.isoformat()}T21:00:00+00:00", "open": c,
                     "high": c, "low": c, "close": c, "volume": 1000})
        d += timedelta(days=1)
    return bars


# ---- registry ----

def test_legacy_config_shim(cfg):
    assert "strategies" not in cfg
    en = strategies.enabled(cfg)
    assert en == [("ma_crossover", {"enabled": True, "priority": 1,
                                    "fast_period": 3, "slow_period": 5})]


def test_priority_order_and_enabled_filter(cfg):
    cfg["strategies"] = {
        "meanrev": {"enabled": True, "priority": 2, "rsi_period": 2,
                    "rsi_buy_below": 10, "trend_sma_period": 10,
                    "exit_sma_period": 5, "max_hold_days": 7},
        "tsmom": {"enabled": True, "priority": 1, "momentum_bars": 20,
                  "trend_sma_period": 30},
        "xsmom": {"enabled": False, "priority": 0},
    }
    assert [n for n, _ in strategies.enabled(cfg)] == ["tsmom", "meanrev"]


def test_prepare_cross_sections_includes_disabled_owner(cfg):
    """Regression: a DISABLED cross-sectional strategy that still owns an
    open position must get its cross-section prepared, or its exit logic
    would silently hold forever."""
    cfg["strategies"] = {
        "ma_crossover": {"enabled": True, "priority": 1,
                         "fast_period": 3, "slow_period": 5},
        "xsmom": {"enabled": False, "priority": 3, "rank_lookback_bars": 5,
                  "skip_bars": 1, "buy_top_fraction": 0.25,
                  "exit_below_fraction": 0.5},
    }
    # A strategy now ranks its own UNIVERSE rather than whatever bars it is
    # handed, so the config has to say which symbols xsmom trades. This test
    # always meant "the four names being ranked"; under the old code that was
    # implicit because the universe was ignored entirely. The assertion below is
    # unchanged — only the fact the new model needs has been supplied.
    cfg["symbols"] = ["A", "B", "C", "D"]
    all_bars = {s: _long_bars([100 + i for i in range(20)])
                for s in ("A", "B", "C", "D")}
    assert strategies.prepare_cross_sections(cfg, all_bars) == {}
    ctx = strategies.prepare_cross_sections(cfg, all_bars,
                                            extra_owners={"xsmom"})
    assert "xsmom" in ctx and ctx["xsmom"]["n"] == 4


def test_held_symbol_outside_the_universe_is_still_rankable(cfg):
    """The trap that scoping the cross-section introduces, pinned.

    A strategy can own a position in a symbol that is NOT in its universe —
    removed from config.yaml, or entered through a past news nomination
    (main.py's scan list explicitly keeps scanning both). Scope the
    cross-section to the universe alone and that symbol vanishes from `ranks`,
    so xsmom answers "insufficient history for ranking" and HOLDS, every cycle,
    forever: a position nothing will close.

    `held` is what prevents it, so this asserts the symbol is ranked BECAUSE it
    is held, and that it is absent when it is not — the boundary pair. Without
    the second half the first would pass on a function that simply ignored the
    universe."""
    cfg["strategies"] = {
        "xsmom": {"enabled": True, "priority": 1, "rank_lookback_bars": 5,
                  "skip_bars": 1, "buy_top_fraction": 0.25,
                  "exit_below_fraction": 0.5},
    }
    cfg["symbols"] = ["A", "B", "C", "D"]
    all_bars = {s: _long_bars([100 + i for i in range(20)])
                for s in ("A", "B", "C", "D", "GONE")}

    without = strategies.prepare_cross_sections(cfg, all_bars)
    assert "GONE" not in without["xsmom"]["ranks"], (
        "a symbol outside the universe must not dilute the percentile")
    assert without["xsmom"]["n"] == 4

    withheld = strategies.prepare_cross_sections(cfg, all_bars,
                                                 held={"GONE"})
    assert "GONE" in withheld["xsmom"]["ranks"], (
        "a HELD symbol must stay rankable or its exit can never fire")
    assert withheld["xsmom"]["n"] == 5


def test_max_lookback_covers_disabled_owners(cfg):
    cfg["strategies"] = {
        "ma_crossover": {"enabled": True, "priority": 1,
                         "fast_period": 3, "slow_period": 5},
        "tsmom": {"enabled": False, "priority": 2, "momentum_bars": 60,
                  "trend_sma_period": 200},  # disabled but may own positions
    }
    assert strategies.max_lookback_bars(cfg) == 201


# ---- tsmom ----

TS_PARAMS = {"momentum_bars": 5, "skip_bars": 0, "trend_sma_period": 10}


def test_tsmom_buys_positive_momentum_in_uptrend():
    closes = [100 + i for i in range(20)]  # steady uptrend
    sig = tsmom.generate("SPY", _long_bars(closes), TS_PARAMS, holding=False)
    assert sig.action == "buy" and sig.strategy == "tsmom"
    assert "momentum" in sig.reason


def test_tsmom_exits_when_momentum_flips():
    closes = [100 + i for i in range(15)] + [113, 111, 109, 107, 105]
    sig = tsmom.generate("SPY", _long_bars(closes), TS_PARAMS, holding=True)
    assert sig.action == "sell"


def test_tsmom_no_buy_below_trend_sma():
    closes = [130 - i for i in range(15)] + [114, 115, 115.5, 115.7, 116]
    bars = _long_bars(closes)
    sig = tsmom.generate("SPY", bars, TS_PARAMS, holding=False)
    assert sig.action == "hold"  # momentum up but price below SMA10? verify guard


def test_tsmom_insufficient_history():
    sig = tsmom.generate("SPY", _long_bars([100] * 5), TS_PARAMS, holding=False)
    assert sig.action == "hold" and "insufficient" in sig.reason


# ---- meanrev ----

MR_PARAMS = {"rsi_period": 2, "rsi_buy_below": 15, "trend_sma_period": 25,
             "exit_sma_period": 3, "max_hold_days": 7}


def test_meanrev_buys_sharp_dip_in_uptrend():
    # long uptrend, then two hard down days -> RSI(2) collapses; the wide
    # trend SMA (stand-in for SMA200) keeps the uptrend filter satisfied
    closes = [100 + i * 2 for i in range(30)] + [154, 150]
    sig = meanrev.generate("AAPL", _long_bars(closes), MR_PARAMS, holding=False)
    assert sig.action == "buy"
    assert "oversold dip" in sig.reason


def test_meanrev_no_buy_in_downtrend():
    closes = [200 - i * 2 for i in range(30)] + [138, 136]  # dip but downtrend
    sig = meanrev.generate("AAPL", _long_bars(closes), MR_PARAMS, holding=False)
    assert sig.action == "hold"


def test_meanrev_exits_on_reversion():
    closes = [100 + i for i in range(30)] + [120, 121, 135]  # close > SMA3
    sig = meanrev.generate("AAPL", _long_bars(closes), MR_PARAMS, holding=True,
                           entry_ts=closes and _long_bars(closes)[-2]["ts"])
    assert sig.action == "sell" and "reverted" in sig.reason


def test_meanrev_time_stop():
    # price stuck below exit SMA, held longer than max_hold_days
    closes = [100 + i for i in range(30)] + [80, 79, 78, 77, 76, 75, 74, 73, 72]
    bars = _long_bars(closes)
    sig = meanrev.generate("AAPL", bars, MR_PARAMS, holding=True,
                           entry_ts=bars[-9]["ts"])
    assert sig.action == "sell" and "time stop" in sig.reason


# ---- xsmom ----

XS_PARAMS = {"rank_lookback_bars": 10, "skip_bars": 2,
             "buy_top_fraction": 0.25, "exit_below_fraction": 0.50}


def _universe():
    def series(daily_ret):
        closes, p = [], 100.0
        for _ in range(20):
            p *= (1 + daily_ret)
            closes.append(p)
        return _long_bars(closes)
    return {"WIN": series(0.02), "MID1": series(0.005), "MID2": series(0.002),
            "FLAT": series(0.0), "LOSE": series(-0.01)}


def test_xsmom_prepare_ranks_universe():
    ctx = xsmom.prepare(_universe(), XS_PARAMS)
    assert ctx["n"] == 5
    assert ctx["ranks"]["WIN"] == 0 and ctx["ranks"]["LOSE"] == 4


def test_xsmom_buys_top_fraction_only():
    uni = _universe()
    ctx = xsmom.prepare(uni, XS_PARAMS)
    top = xsmom.generate("WIN", uni["WIN"], XS_PARAMS, False, cross_section=ctx)
    mid = xsmom.generate("MID2", uni["MID2"], XS_PARAMS, False, cross_section=ctx)
    assert top.action == "buy" and "relative strength leader" in top.reason
    assert mid.action == "hold"


def test_xsmom_exits_when_rank_fades():
    uni = _universe()
    ctx = xsmom.prepare(uni, XS_PARAMS)
    sig = xsmom.generate("LOSE", uni["LOSE"], XS_PARAMS, True, cross_section=ctx)
    assert sig.action == "sell" and "faded" in sig.reason


def test_xsmom_holds_without_cross_section():
    uni = _universe()
    sig = xsmom.generate("WIN", uni["WIN"], XS_PARAMS, False, cross_section=None)
    assert sig.action == "hold"


# ---- shared indicators ----

def test_rsi_extremes():
    from strategies.base import rsi
    up = [100 + i for i in range(10)]
    down = [100 - i for i in range(10)]
    assert rsi(up, 2) == 100.0
    assert rsi(down, 2) == pytest.approx(0.0)


def test_total_return_with_skip():
    from strategies.base import total_return
    closes = [100, 110, 121, 200]  # skip=1 ignores the last close
    assert total_return(closes, 2, skip=1) == pytest.approx(0.21)
