"""Donchian channel breakout: buy a new N-bar high, exit on an M-bar low.

The classical swing-trading breakout (Donchian's channels; the Turtle rules
made the 20-in/10-out asymmetry famous). It earns its place in the ensemble by
being mechanically *different* from what is already here rather than by
promising better numbers:

  ma_crossover  two moving averages cross          — lagging trend
  tsmom         the asset's own trailing return    — trend, measured directly
  meanrev       RSI(2) dip inside an uptrend       — counter-trend
  xsmom         relative strength vs the universe  — cross-sectional
  donchian      price makes a new extreme          — pure price-level breakout

Entry on a *level* rather than an average means it fires on the bar the range
resolves, where the moving-average strategies wait for their averages to catch
up. The exit channel is deliberately shorter than the entry channel: the
asymmetry lets a trend run while cutting the failed breakouts that make up most
of the signal count.

The trend filter is load-bearing, exactly as it is in meanrev. An unfiltered
breakout system buys every false high in a downtrend; SMA200 keeps entries on
the side of the primary trend.

Prior expectation, stated before this was ever run: breakout systems win rarely
and large, so a LOW win rate with a high profit factor is the shape that would
support the mechanism, and a high win rate would be a reason for suspicion
rather than celebration. Like every other strategy here it may only be enabled
after passing the walk-forward enablement gate.

Exits respect the swing guard (`min_holding_days`), so the earliest strategy
exit is day 2; the broker-side bracket legs still protect before that.
"""
from strategies.base import Signal, sma

NAME = "donchian"
NEEDS_CROSS_SECTION = False


def required_lookback(params: dict) -> int:
    # +1 because the breakout compares today's close against the channel formed
    # by the PRIOR bars — including today would make every new high trivially
    # equal to its own channel top.
    return max(params["entry_channel_bars"],
               params["exit_channel_bars"],
               params.get("trend_sma_period", 0)) + 1


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section=None) -> Signal:
    need = required_lookback(params)
    if len(bars) < need:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(bars)} bars, need {need})",
                      strategy=NAME)

    entry_n = params["entry_channel_bars"]
    exit_n = params["exit_channel_bars"]
    closes = [b["close"] for b in bars]
    close = closes[-1]

    # Channels from the bars BEFORE the current one — no look-ahead.
    prior = bars[:-1]
    channel_high = max(b["high"] for b in prior[-entry_n:])
    channel_low = min(b["low"] for b in prior[-exit_n:])

    ind = {"close": round(close, 2),
           f"high{entry_n}": round(channel_high, 2),
           f"low{exit_n}": round(channel_low, 2)}

    trend_period = params.get("trend_sma_period", 0)
    trend_sma = sma(closes, trend_period) if trend_period else None
    if trend_sma is not None:
        ind[f"sma{trend_period}"] = round(trend_sma, 2)

    if not holding:
        if close <= channel_high:
            return Signal(symbol, "hold",
                          f"no breakout — close {close:.2f} within the "
                          f"{entry_n}-bar range (high {channel_high:.2f})",
                          ind, NAME)
        # No "filter unavailable" branch here on purpose: required_lookback()
        # already includes trend_sma_period, so any bar list long enough to get
        # past the history check above is long enough for the SMA. A defensive
        # fail-closed clause would be unreachable code pretending to be a
        # safety net. The guarantee lives in required_lookback().
        if trend_sma is not None and close <= trend_sma:
            return Signal(symbol, "hold",
                          f"{entry_n}-bar breakout but price is below "
                          f"SMA{trend_period} — breakout against the primary "
                          "trend, skipped", ind, NAME)
        return Signal(symbol, "buy",
                      f"close {close:.2f} broke the {entry_n}-bar high "
                      f"{channel_high:.2f}"
                      + (f" with the trend intact above SMA{trend_period}"
                         if trend_sma is not None else ""),
                      ind, NAME)

    if close < channel_low:
        return Signal(symbol, "sell",
                      f"close {close:.2f} broke the {exit_n}-bar low "
                      f"{channel_low:.2f} — breakout failed, exiting",
                      ind, NAME)

    return Signal(symbol, "hold",
                  f"holding — close {close:.2f} above the {exit_n}-bar low "
                  f"{channel_low:.2f}", ind, NAME)
