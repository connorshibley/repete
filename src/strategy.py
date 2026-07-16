"""Compatibility facade over the strategies/ package.

The single-strategy era imported `strategy.generate_signal` and the shared
indicators from here; those call sites (backtest.py, main.py's ATR use, the
original tests) keep working. New code should use the `strategies` registry.
The LLM never generates signals — every strategy is deterministic code.
"""
from strategies.base import Signal, sma as _sma, true_range, atr  # noqa: F401
from strategies import ma_crossover


def generate_signal(symbol: str, bars: list[dict], cfg: dict, holding: bool) -> Signal:
    """Legacy entry point: MA crossover with params from the old strategy:
    section (or the new strategies: mapping when present)."""
    smap = cfg.get("strategies", {}).get("ma_crossover")
    params = smap or {"fast_period": cfg["strategy"]["fast_period"],
                      "slow_period": cfg["strategy"]["slow_period"]}
    return ma_crossover.generate(symbol, bars, params, holding)
