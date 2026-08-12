"""§64 trend-timed holding (Gayed-Bilello 2016, "Leverage for the Long Run"):
hold the index fund while it closes above its own long moving average, stand
in cash below it.

THE CLAIM IS ABOUT VOLATILITY, NOT RETURN. The paper's mechanism is that the
200-day line is a volatility-regime classifier — realized vol below the MA
runs roughly twice vol above it — and leverage compounds well only in the
calm regime. The strategy module is therefore JUST THE SWITCH; any leverage
comes from a frozen spec arm setting `risk.margin.multiplier`, priced by the
simulator's financing model, never from this file. Unlevered, this is plain
MA timing, which the paper itself reports beats buy-and-hold in only 49% of
rolling three-year windows.

ARGUED AGAINST IN ADVANCE (the hi52 discipline): the post-publication record
2016-2026 at 1.5x with honest financing TRAILED SPY total return by ~2pp/yr.
Registered because it is pre-specified; ships `enabled: false`.

Not ma_crossover wearing a new name: that compares two MAs of each of 38
names against each other; this compares ONE symbol's close against one MA —
the Gayed spec exactly — on a single-symbol universe (`spy_only`).
"""
from strategies.base import Signal, sma

NAME = "trend_hold"
NEEDS_CROSS_SECTION = False


def required_lookback(params: dict) -> int:
    return params["ma_days"] + 1


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    ma_days = params["ma_days"]
    if len(bars) < ma_days + 1:
        return Signal(symbol, "hold", "insufficient history for MA",
                      strategy=NAME)
    closes = [float(b["close"]) for b in bars]
    line = sma(closes, ma_days)
    px = closes[-1]
    ind = {"close": round(px, 4), "ma": round(line, 4), "ma_days": ma_days}
    if holding and px < line:
        return Signal(symbol, "sell",
                      f"closed below the {ma_days}-day MA "
                      f"({px:.2f} < {line:.2f}) — regime turned", ind, NAME)
    if not holding and px > line:
        return Signal(symbol, "buy",
                      f"closed above the {ma_days}-day MA "
                      f"({px:.2f} > {line:.2f}) — calm regime", ind, NAME)
    state = "holding" if holding else "flat"
    return Signal(symbol, "hold", f"no MA cross ({state})", ind, NAME)
