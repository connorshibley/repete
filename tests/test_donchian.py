"""Donchian breakout (§17 candidate) — deterministic fixtures, no network.

The strategy is DISABLED in config; these tests pin its logic anyway, because a
disabled strategy still owns exits for any position it opened, and because a
breakout that silently stopped respecting its trend filter would be the version
the research says loses money.
"""
from datetime import date, timedelta

import pytest

import strategies
from strategies import donchian


def bars_from(highs, lows=None, closes=None):
    """OHLC bars from parallel lists; defaults keep every bar self-consistent."""
    lows = lows if lows is not None else [h * 0.98 for h in highs]
    closes = closes if closes is not None else highs
    out, d = [], date(2025, 1, 1)
    for h, lo, c in zip(highs, lows, closes):
        out.append({"ts": f"{d.isoformat()}T21:00:00+00:00",
                    "open": c, "high": max(h, c), "low": min(lo, c),
                    "close": c, "volume": 1000})
        d += timedelta(days=1)
    return out


PARAMS = {"entry_channel_bars": 20, "exit_channel_bars": 10,
          "trend_sma_period": 0}


def test_registered():
    assert "donchian" in strategies.REGISTRY
    assert strategies.REGISTRY["donchian"] is donchian


def test_required_lookback_leaves_room_for_the_prior_channel():
    """+1: the channel is built from the bars BEFORE the current one."""
    assert donchian.required_lookback(PARAMS) == 21
    assert donchian.required_lookback(
        {**PARAMS, "trend_sma_period": 200}) == 201


def test_insufficient_history_holds():
    sig = donchian.generate("AAA", bars_from([100] * 5), PARAMS, False)
    assert sig.action == "hold"
    assert "insufficient history" in sig.reason


def test_new_high_triggers_buy():
    bars = bars_from([100] * 25 + [120])
    sig = donchian.generate("AAA", bars, PARAMS, False)
    assert sig.action == "buy"
    assert "broke the 20-bar high" in sig.reason


def test_close_inside_the_channel_holds():
    bars = bars_from([100] * 25 + [99])
    sig = donchian.generate("AAA", bars, PARAMS, False)
    assert sig.action == "hold"
    assert "no breakout" in sig.reason


def test_equalling_the_channel_high_is_not_a_breakout():
    """Strictly greater — otherwise a flat series breaks out every single bar."""
    bars = bars_from([100] * 25 + [100])
    assert donchian.generate("AAA", bars, PARAMS, False).action == "hold"


def test_channel_excludes_the_current_bar():
    """If today's own high counted, every new high would trivially equal its
    own channel top and the strategy could never fire."""
    bars = bars_from([100] * 25 + [120])
    sig = donchian.generate("AAA", bars, PARAMS, False)
    assert sig.indicators["high20"] == 100.0


def test_trend_filter_blocks_a_breakout_below_the_sma():
    """Downtrend: a long decline, then a bounce that DOES clear the 20-bar high
    (120 > 100) but is still far under SMA200 (~180). This is exactly the false
    breakout the filter exists for — without it the entry would fire."""
    closes = [300 - i for i in range(220)] + [120]
    p = {**PARAMS, "trend_sma_period": 200}
    bars = bars_from(closes)
    sig = donchian.generate("AAA", bars, p, False)
    assert sig.indicators["close"] > sig.indicators["high20"]   # breakout is real
    assert sig.indicators["close"] < sig.indicators["sma200"]   # trend is not
    assert sig.action == "hold"
    assert "against the primary trend" in sig.reason

    # Same bars, filter off -> the entry fires. Proves the filter is load-bearing
    # rather than incidentally agreeing with the channel logic.
    off = donchian.generate("AAA", bars, PARAMS, False)
    assert off.action == "buy"


def test_trend_filter_allows_a_breakout_above_the_sma():
    closes = [100 + i * 0.5 for i in range(220)] + [400]
    p = {**PARAMS, "trend_sma_period": 200}
    sig = donchian.generate("AAA", bars_from(closes), p, False)
    assert sig.action == "buy"
    assert "trend intact" in sig.reason


def test_lookback_guarantees_the_trend_filter_is_available():
    """The strategy has no "filter unavailable" branch, and must not need one:
    required_lookback() includes trend_sma_period, so every bar list that gets
    past the history check can compute the SMA. Pin that invariant — if someone
    shrinks required_lookback, the filter would start silently failing OPEN."""
    p = {**PARAMS, "trend_sma_period": 200}
    need = donchian.required_lookback(p)
    sig = donchian.generate("AAA", bars_from([100] * (need - 1) + [500]), p, False)
    assert "sma200" in sig.indicators and sig.indicators["sma200"] is not None


def test_exit_on_new_low():
    bars = bars_from([100] * 25 + [80])
    sig = donchian.generate("AAA", bars, PARAMS, True)
    assert sig.action == "sell"
    assert "breakout failed" in sig.reason


def test_holding_above_the_exit_channel_holds():
    bars = bars_from([100] * 25 + [99])
    sig = donchian.generate("AAA", bars, PARAMS, True)
    assert sig.action == "hold"


def test_exit_ignores_the_trend_filter():
    """Exits must never be gated — a position has to be able to get out even
    when the entry filter would have blocked re-entry."""
    closes = [300 - i for i in range(220)] + [1.0]
    p = {**PARAMS, "trend_sma_period": 200}
    sig = donchian.generate("AAA", bars_from(closes), p, True)
    assert sig.action == "sell"


def test_signals_carry_the_strategy_tag():
    """Ownership routing depends on this tag; an untagged signal would be
    attributed to the legacy default owner on exit."""
    bars = bars_from([100] * 25 + [120])
    for holding in (True, False):
        assert donchian.generate("AAA", bars, PARAMS, holding).strategy == "donchian"


def test_disabled_in_shipped_config():
    """§17 FAILED its gate (OOS +1.54% vs a +1.95% exposure-matched bar).
    It must stay disabled until it passes one."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["strategies"]["donchian"]["enabled"] is False
    assert "donchian" not in {n for n, _ in strategies.enabled(cfg)}
