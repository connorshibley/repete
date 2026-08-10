"""52-week-high proximity (George & Hwang, *Journal of Finance* 2004): rank the
universe by how close each name trades to its own 52-week high, buy the nearest
fraction, exit when a holding falls out of the top half.

WHY THIS AND NOT MORE MOMENTUM. §35 and §37 tested cross-sectional momentum
(Jegadeesh-Titman '12-1') and it was rejected hard — −23% to −26% on 2007-2013,
"SIGNIFICANTLY WORSE" at −$169.18 per trade. This is not a variation on that
ranking; it is the paper that says that ranking is the wrong one. George & Hwang
found nearness-to-the-52-week-high SUBSUMES a large share of momentum profits,
and — the part that matters here — does so **without the long-run reversal**
that makes momentum expensive to hold. Two rankings, one nested claim.

The ranking statistic is `close / max(high over 252 bars)`, in [0, 1]. It is
scale-free by construction, so it means the same thing on a $30 fund and a $600
one, and it needs no cross-sectional standardisation.

LONG ONLY, and structurally so rather than by preference: `simulate()` refuses
short signals outright (backtest.py) and `risk.trail_stop` is long-only. The
short leg lives in `xsmom` alone, by the §53 design that keeps it attributable.
So this module never declares NEEDS_POSITION_SIDE and never emits "short".

ARGUED AGAINST IN ADVANCE. §35's prior applies with full force: a rule published
twenty-two years ago has had two decades to be arbitraged away, and that prior
was correct about Jegadeesh-Titman. It is registered because it is
PRE-SPECIFIED, not because it is likely. §59 registers it DIAGNOSTIC, which
decides nothing and can enable nothing; it ships `enabled: false`.
"""
from strategies.base import Signal

NAME = "hi52"
NEEDS_CROSS_SECTION = True


def required_lookback(params: dict) -> int:
    # +1 because the high window is the bars up to and including today, and the
    # ratio needs today's close as well.
    return params["high_lookback_bars"] + 1


def proximity(bars: list[dict], lookback: int) -> float | None:
    """close / max(high) over the last `lookback` bars. 1.0 = at the high.

    Deliberately NOT clamped and not allowed above 1.0 by construction: today's
    high is inside the window, and today's close cannot exceed today's high, so
    a value over 1.0 would mean the bar data is malformed. None on insufficient
    history or a non-positive high — never a number the caller might trust.
    """
    if len(bars) < lookback or lookback < 1:
        return None
    window = bars[-lookback:]
    peak = max(float(b["high"]) for b in window)
    if peak <= 0:
        return None
    return float(window[-1]["close"]) / peak


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Rank the universe by 52-week-high proximity, closest first.

    Returns {"ranks": {symbol: 0-based rank}, "n": universe size,
             "proximity": {symbol: ratio}} — symbols with insufficient history
    are excluded, exactly as xsmom excludes them: they cannot signal this cycle,
    and inventing a rank for them would put a name with three months of data on
    the same scale as one with three years.

    `cfg` is part of the prepare() contract and unused here: this ranking needs
    nothing outside its own params. `all_bars` arrives already scoped to this
    strategy's universe plus anything it holds — see `prepare_one`, and note
    that the scoping is load-bearing, since a percentile over 15 funds and a
    percentile over 150 names are different strategies wearing one name.
    """
    lookback = params["high_lookback_bars"]
    prox = {}
    for sym, bars in all_bars.items():
        value = proximity(bars, lookback)
        if value is not None:
            prox[sym] = value
    ordered = sorted(prox, key=prox.get, reverse=True)     # nearest the high first
    return {"ranks": {s: i for i, s in enumerate(ordered)},
            "proximity": prox, "n": len(ordered)}


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    if not cross_section or cross_section.get("n", 0) < 4:
        return Signal(symbol, "hold", "cross-section unavailable or too small",
                      strategy=NAME)
    ranks = cross_section["ranks"]
    if symbol not in ranks:
        return Signal(symbol, "hold",
                      "insufficient history for ranking", strategy=NAME)

    n = cross_section["n"]
    rank = ranks[symbol]
    pct_rank = rank / n                       # 0.0 = nearest its own 52w high
    prox = cross_section["proximity"][symbol]
    ind = {"rank": rank + 1, "universe": n, "pct_rank": round(pct_rank, 3),
           "proximity_pct": round(prox * 100, 2)}

    if holding:
        if pct_rank >= params["exit_below_fraction"]:
            return Signal(symbol, "sell",
                          f"slipped to rank {rank + 1}/{n} on 52-week-high "
                          f"proximity ({prox:.1%} of its high, below the top "
                          f"{params['exit_below_fraction']:.0%}) — exiting",
                          ind, NAME)
    elif (pct_rank < params["buy_top_fraction"]
            and prox >= params["min_proximity"]):
        # BOTH conditions, and the absolute floor is not decoration. A purely
        # relative rule buys the least-broken name in a universe where every
        # member is 40% off its high — which is a bet on relative strength
        # inside a bear market, not the claim the paper makes. The paper's
        # mechanism is anchoring AT the high, so a name has to actually be near
        # one.
        return Signal(symbol, "buy",
                      f"ranked {rank + 1}/{n} on 52-week-high proximity "
                      f"({prox:.1%} of its {params['high_lookback_bars']}-bar "
                      f"high, top {params['buy_top_fraction']:.0%} of universe)",
                      ind, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"rank {rank + 1}/{n}, no action ({state})",
                  ind, NAME)
