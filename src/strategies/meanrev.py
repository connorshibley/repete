"""Short-term mean reversion (Connors RSI-2 family): buy a sharp dip in a
symbol trading above its long-term trend; exit on reversion or max-hold.

Evidence: decades of backtests show equities bounce after short-term panic
WHEN the long-term trend filter is respected — unfiltered RSI(2) buying
lost heavily in the 2022 bear. The SMA200 filter is load-bearing.
Exits respect the swing guard (min_holding_days), so the earliest strategy
exit is day 2; broker-side bracket legs still protect earlier.
"""
from datetime import datetime

from strategies.base import Signal, sma, rsi

NAME = "meanrev"
NEEDS_CROSS_SECTION = False
NEEDS_ENTRY_TS = True     # max_hold_days; see strategies.generate dispatch

RSI_HISTORY = 30  # bars fed to Wilder smoothing beyond the raw period


def required_lookback(params: dict) -> int:
    return max(params["trend_sma_period"], RSI_HISTORY) + 1


def _held_days(bars: list[dict], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    now = datetime.fromisoformat(bars[-1]["ts"])
    entered = datetime.fromisoformat(entry_ts)
    return (now - entered).days


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section=None, entry_ts: str | None = None) -> Signal:
    need = required_lookback(params)
    closes = [b["close"] for b in bars]
    if len(closes) < need:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(closes)} bars, need {need})",
                      strategy=NAME)

    r = rsi(closes[-(RSI_HISTORY + 1):], params["rsi_period"])
    trend_sma = sma(closes, params["trend_sma_period"])
    exit_sma = sma(closes, params["exit_sma_period"])
    close = closes[-1]
    ind = {"close": round(close, 2), f"rsi{params['rsi_period']}": round(r, 1),
           f"sma{params['trend_sma_period']}": round(trend_sma, 2),
           f"sma{params['exit_sma_period']}": round(exit_sma, 2)}

    if not holding:
        if close > trend_sma and r < params["rsi_buy_below"]:
            return Signal(symbol, "buy",
                          f"RSI{params['rsi_period']} at {r:.0f} (< "
                          f"{params['rsi_buy_below']}) with uptrend intact above "
                          f"SMA{params['trend_sma_period']} — oversold dip in an uptrend",
                          ind, NAME)
        return Signal(symbol, "hold", "no oversold dip (flat)", ind, NAME)

    if close > exit_sma:
        return Signal(symbol, "sell",
                      f"close reverted above SMA{params['exit_sma_period']} — "
                      "mean reversion complete, exiting", ind, NAME)
    held = _held_days(bars, entry_ts)
    if held is not None and held >= params["max_hold_days"]:
        return Signal(symbol, "sell",
                      f"max hold {params['max_hold_days']}d reached without "
                      "reversion — time stop, exiting", ind, NAME)
    return Signal(symbol, "hold", "awaiting reversion (holding position)", ind, NAME)
