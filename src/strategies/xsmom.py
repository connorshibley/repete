"""Cross-sectional momentum (Jegadeesh/Titman 1993 '12-1'): rank the whole
universe by trailing return (skipping the most recent month to dodge
short-term reversal), buy the top fraction, exit when a holding falls out of
the top half. Needs ALL symbols' bars, so it runs a once-per-cycle
prepare() step before per-symbol generation.

THE SHORT LEG is the mirror image and nothing more: short the bottom
`short_bottom_fraction` when its momentum is also negative, cover when the name
climbs back inside the top `exit_below_fraction`. Both conditions are mirrored
on purpose — a rank-only short would bet on relative weakness inside a rising
universe, which is a different claim from the one the long leg makes.

It is the ONLY source of shorts in this repo (the 130/30 design confines them
here so the leg stays attributable and `enabled: false` restores today's bot
exactly), and it ships with `enabled: false`.
"""
from strategies.base import Signal, total_return

NAME = "xsmom"
NEEDS_CROSS_SECTION = True

#: This is the strategy that will carry the short leg, so it is the one that
#: cannot work from `holding: bool` alone — see strategies/base.py's contract
#: note for what goes wrong when a short is asked for an exit with only that.
#:
#: Declared BEFORE the short leg exists, and deliberately IGNORED by generate()
#: below until it does. That makes the wiring live and testable end to end
#: while this change is still provably a no-op for every signal xsmom emits —
#: as opposed to landing the plumbing dead, which is how `short_bottom_fraction`
#: shipped naming nothing and had to be fixed in a later PR.
NEEDS_POSITION_SIDE = True


def required_lookback(params: dict) -> int:
    return params["rank_lookback_bars"] + params.get("skip_bars", 21) + 1


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Rank the universe by skip-adjusted trailing return.
    Returns {"ranks": {symbol: 0-based rank}, "n": universe size,
             "returns": {symbol: momentum}} — symbols with insufficient
    history are excluded (they simply can't signal this cycle).

    `cfg` is part of the prepare() contract and unused here: this ranking needs
    nothing outside its own params. `all_bars` now arrives already scoped to
    this strategy's universe (plus anything it holds), so the percentile this
    computes still means what its gate measured."""
    rets = {}
    need = required_lookback(params)
    for sym, bars in all_bars.items():
        closes = [b["close"] for b in bars]
        if len(closes) < need:
            continue
        r = total_return(closes, params["rank_lookback_bars"],
                         params.get("skip_bars", 21))
        if r is not None:
            rets[sym] = r
    ordered = sorted(rets, key=rets.get, reverse=True)
    return {"ranks": {s: i for i, s in enumerate(ordered)},
            "returns": rets, "n": len(ordered)}


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None,
             position_side: str | None = None) -> Signal:
    if not cross_section or cross_section.get("n", 0) < 4:
        return Signal(symbol, "hold", "cross-section unavailable or too small",
                      strategy=NAME)
    ranks = cross_section["ranks"]
    if symbol not in ranks:
        return Signal(symbol, "hold",
                      "insufficient history for ranking", strategy=NAME)

    n = cross_section["n"]
    rank = ranks[symbol]
    pct_rank = rank / n  # 0.0 = strongest
    mom = cross_section["returns"][symbol]
    ind = {"rank": rank + 1, "universe": n, "pct_rank": round(pct_rank, 3),
           "momentum_pct": round(mom * 100, 2)}

    # 0 (or absent) means NO SHORT LEG, matching how every other numeric
    # switch in this repo reads 0 — and matching preflight's shorting guard,
    # which keys off this exact name being truthy.
    short_frac = params.get("short_bottom_fraction") or 0

    if holding:
        # REFUSE TO GUESS. A held position with no side is not assumed long:
        # guessing wrong here emits "sell" against a SHORT, which every rail
        # passes (it is in EXIT_ACTIONS) and broker.market_order maps to
        # OrderSide.SELL — DOUBLING the short. Holding is bounded and visible;
        # doubling an unbounded-loss position is neither. No production caller
        # reaches this today (main.py derives the side from the broker's signed
        # qty, and backtest.py passes "long" explicitly), so this is the guard
        # for a future caller that forgets, not for a case that exists.
        if position_side is None:
            return Signal(symbol, "hold",
                          "position side unknown — refusing to choose an exit "
                          "direction", ind, NAME)
        if position_side == "short":
            if pct_rank < params["exit_below_fraction"]:
                return Signal(symbol, "cover",
                              f"recovered to rank {rank + 1}/{n} (back inside the "
                              f"top {params['exit_below_fraction']:.0%}) — relative "
                              "weakness faded, covering",
                              {**ind, "side": "short"}, NAME)
        elif pct_rank >= params["exit_below_fraction"]:
            return Signal(symbol, "sell",
                          f"dropped to rank {rank + 1}/{n} (below top "
                          f"{params['exit_below_fraction']:.0%}) — relative strength "
                          "faded, exiting", ind, NAME)
    else:
        if pct_rank < params["buy_top_fraction"] and mom > 0:
            return Signal(symbol, "buy",
                          f"ranked {rank + 1}/{n} by {params['rank_lookback_bars']}-bar "
                          f"momentum ({mom:+.1%}, top {params['buy_top_fraction']:.0%} "
                          "of universe) — relative strength leader", ind, NAME)
        # The mirror of the buy, and deliberately mirrored in BOTH conditions.
        # Dropping `mom < 0` would short the weakest quarter of a universe that
        # is rising as a whole — a relative-weakness bet expressed as an
        # absolute-direction position, which is not what the long leg does and
        # not what this leg is being asked to test.
        if short_frac and pct_rank >= 1 - short_frac and mom < 0:
            return Signal(symbol, "short",
                          f"ranked {rank + 1}/{n} by {params['rank_lookback_bars']}-bar "
                          f"momentum ({mom:+.1%}, bottom {short_frac:.0%} "
                          "of universe) — relative strength laggard",
                          {**ind, "side": "short"}, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"rank {rank + 1}/{n}, no action ({state})",
                  ind, NAME)
