"""Shared strategy primitives: the Signal contract and pure indicators.

Every strategy module is deterministic plain code — the LLM never generates
signals. Modules follow a duck-typed contract:

    NAME: str
    NEEDS_CROSS_SECTION: bool
    def required_lookback(params) -> int
    NEEDS_ENTRY_TS: bool                        # optional, default False
    NEEDS_POSITION_SIDE: bool                   # optional, default False
    def prepare(all_bars, params, cfg=None) -> ctx   # cross-sectional only
    def generate(symbol, bars, params, holding, cross_section=None) -> Signal

`prepare` receives `cfg` as well as its own `params` because a cross-section can
depend on config outside the strategy's sub-dict (`reclaim` reads the top-level
`sectors:` map). `generate` deliberately does NOT: everything it needs must be
carried in the `cross_section` context `prepare` built, which keeps per-symbol
signal generation a pure function of (bars, params, ctx).

`NEEDS_ENTRY_TS = True` makes `strategies.generate` pass `entry_ts=` through —
required by any strategy with a max-hold rule. It is DECLARED rather than
inferred from the module name; the dispatch used to test `name == "meanrev"`,
which silently gave a second such strategy `entry_ts=None` and a max-hold that
measured nothing.

`NEEDS_POSITION_SIDE = True` makes it pass `position_side=` — `"long"`,
`"short"` or `None` when flat. `holding: bool` says only THAT a position exists,
never which way it points, and for a strategy with a short leg that is not
enough to choose an exit action:

    the owner-only exit branch (main.py) calls generate(owner, ..., True, ...)
    for any held name. Asked about a SHORT with only `holding=True` to go on,
    a long-biased strategy answers "sell" — which EXIT_ACTIONS accepts and
    broker.market_order maps to SELL, DOUBLING the short instead of closing it.

Declared, not inferred, for the same reason as NEEDS_ENTRY_TS: a strategy that
silently receives `None` where it expected a side does not fail, it just decides
wrongly. Every strategy that does not declare it keeps its exact call signature,
so adding the flag cannot move a strategy that has no short leg.
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

#: The mirror of ENTRY_ACTIONS: actions that CLOSE or REDUCE risk.
#:
#: This was removed on 2026-08-04 as having no consumer — correct for what was
#: visible then, when only `action == "buy"` existed and nothing read the exit
#: side of the vocabulary. It is reinstated because `swing_guard` (risk.py) IS
#: that consumer: `pre_trade_checks` gated the swing guard on `action ==
#: "sell"`, so a `"cover"` (closing a short) bypassed the minimum-holding-period
#: check entirely. Do not remove this again on the "no consumer" argument
#: without first checking whether `swing_guard`'s caller still needs it — that
#: is exactly the check that was missed the first time.
EXIT_ACTIONS = ("sell", "cover")


def side_of_qty(qty) -> str | None:
    """`"long"` | `"short"` | `None`, from a SIGNED quantity.

    The one implementation of "which way does this position point", because the
    live cycle and the offline simulator both have to answer it and a strategy
    that gets two different answers is a divergence by construction.

    A signed quantity is the input on purpose. `broker.positions()` copies
    Alpaca's own `float(p.qty)`, negative for a short, and main.py's in-cycle
    view was made to match that sign in Phase 2-C. Deriving the side anywhere
    else — from an action, from a market value, from the sign of unrealised P&L
    — would be a second source of truth for something the position record
    already carries.

    **Zero is `None`, not `"long"`.** A flat book is not a long one, and
    `qty == 0` reaches here from a fully-exited position whose record has not
    been dropped yet. Returning `"long"` there would tell a strategy it holds
    something it does not.
    """
    if qty is None:
        return None
    q = float(qty)
    if q > 0:
        return "long"
    if q < 0:
        return "short"
    return None


def sector_map(cfg: dict) -> dict:
    """`symbol -> sector name`, inverted from config's frozen `sectors:` block.

    Lives here, in the dependency-free base module, because BOTH `risk.py` (the
    sector-concentration rail) and `strategies/reclaim.py` (sector ranking) need
    it, and a strategy must never import `risk`. `risk.py` already imports
    ENTRY_ACTIONS/EXIT_ACTIONS from this module, so the direction is established.

    A symbol in no sector is simply ABSENT from the result — callers treat that
    as "unmapped", not as a sector named None. That is what keeps every
    sector-keyed rail inert for the core universe, instead of lumping all 38
    core names into one giant pseudo-sector and capping them collectively.
    """
    out: dict = {}
    for sector, syms in (cfg.get("sectors") or {}).items():
        for s in (syms or ()):
            out[s] = sector
    return out


def sector_of(cfg: dict, symbol: str):
    """The sector `symbol` belongs to, or None when it is unmapped."""
    return sector_map(cfg).get(symbol)


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
