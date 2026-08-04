"""Shared strategy primitives: the Signal contract and pure indicators.

Every strategy module is deterministic plain code — the LLM never generates
signals. Modules follow a duck-typed contract:

    NAME: str
    NEEDS_CROSS_SECTION: bool
    def required_lookback(params) -> int
    def prepare(all_bars, params) -> ctx        # optional, cross-sectional only
    def generate(symbol, bars, params, holding, cross_section=None) -> Signal
"""
from dataclasses import dataclass, field


@dataclass
class Signal:
    symbol: str
    action: str                 # "buy" | "sell" | "short" | "cover" | "hold"
    reason: str                 # human-readable rationale
    indicators: dict = field(default_factory=dict)
    strategy: str = ""          # which strategy produced this signal


#: Actions that OPEN or ADD TO risk. Every entry rail keys off this set, so a
#: new direction added here is automatically subject to all of them — which is
#: the opposite of the failure where `action == "buy"` let shorts bypass the
#: concentration cap because nobody remembered to update the condition.
ENTRY_ACTIONS = ("buy", "short")


def sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def total_return(closes: list[float], bars_back: int, skip: int = 0) -> float | None:
    """Return over `bars_back` bars, measured up to `skip` bars ago
    (skip-month support): closes[-1-skip] / closes[-1-skip-bars_back] - 1."""
    end = len(closes) - skip          # exclusive index of the last counted close
    start = end - 1 - bars_back       # index of the base close
    if start < 0 or end < 1 or closes[start] <= 0:
        return None
    return closes[end - 1] / closes[start] - 1


def rsi(closes: list[float], period: int) -> float | None:
    """Wilder's RSI. Needs period+1 closes; smoothed over full history given."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        chg = closes[i] - closes[i - 1]
        gains.append(max(chg, 0.0))
        losses.append(max(-chg, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(prev_close: float, bar: dict) -> float:
    """max(high-low, |high-prev_close|, |low-prev_close|)."""
    return max(bar["high"] - bar["low"],
               abs(bar["high"] - prev_close),
               abs(bar["low"] - prev_close))


def atr(bars: list[dict], period: int = 14) -> float | None:
    """Average True Range: simple mean of the last `period` true ranges.
    Needs period+1 bars; returns None on insufficient history."""
    if len(bars) < period + 1:
        return None
    window = bars[-(period + 1):]
    trs = [true_range(window[i - 1]["close"], window[i])
           for i in range(1, len(window))]
    return sum(trs) / period


def rvol(bars: list[dict], period: int = 20) -> float | None:
    """Relative volume: the last bar's volume over the mean of the `period`
    bars BEFORE it. 2.0 means "twice its recent normal".

    Added for §23. Every bar we load has carried a volume field since day one
    and no strategy ever read it, while two independent professional sources
    both treat volume expansion as core entry confirmation — a blind spot, not
    a decision.

    Returns None (never a number the caller might trust) when the answer is
    undefined: insufficient history, or a zero/absent baseline. That matters
    because callers FAIL OPEN on None — a missing volume feed must not silently
    block every entry, but it also must not fabricate an rvol of 0 or infinity.
    The current bar is excluded from its own baseline; including it would drag
    the mean toward today and understate a genuine spike.
    """
    if len(bars) < period + 1:
        return None
    prior = bars[-(period + 1):-1]
    base = sum(float(b.get("volume") or 0) for b in prior) / period
    if base <= 0:
        return None
    return float(bars[-1].get("volume") or 0) / base
