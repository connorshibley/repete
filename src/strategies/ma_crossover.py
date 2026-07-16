"""MA crossover — the original teaching-device strategy, math unchanged.

Single-asset MA crossovers have no documented post-cost edge
(Sullivan/Timmermann/White 1999); it stays as plumbing validation and a
baseline the ensemble must beat.
"""
from strategies.base import Signal, sma

NAME = "ma_crossover"
NEEDS_CROSS_SECTION = False


def required_lookback(params: dict) -> int:
    return params["slow_period"] + 1


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section=None) -> Signal:
    """Fast SMA crossing above slow SMA = buy; crossing below = sell."""
    fast_p, slow_p = params["fast_period"], params["slow_period"]
    closes = [b["close"] for b in bars]

    if len(closes) < slow_p + 1:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(closes)} bars, need {slow_p + 1})",
                      strategy=NAME)

    fast_now, slow_now = sma(closes, fast_p), sma(closes, slow_p)
    fast_prev, slow_prev = sma(closes[:-1], fast_p), sma(closes[:-1], slow_p)

    ind = {
        "close": round(closes[-1], 2),
        f"sma{fast_p}": round(fast_now, 2),
        f"sma{slow_p}": round(slow_now, 2),
    }

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now
    crossed_down = fast_prev >= slow_prev and fast_now < slow_now

    if crossed_up and not holding:
        return Signal(symbol, "buy",
                      f"SMA{fast_p} ({ind[f'sma{fast_p}']}) crossed above SMA{slow_p} "
                      f"({ind[f'sma{slow_p}']}) — momentum turning up", ind, NAME)
    if crossed_down and holding:
        return Signal(symbol, "sell",
                      f"SMA{fast_p} ({ind[f'sma{fast_p}']}) crossed below SMA{slow_p} "
                      f"({ind[f'sma{slow_p}']}) — momentum turning down, exiting", ind, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"no crossover this bar ({state})", ind, NAME)
