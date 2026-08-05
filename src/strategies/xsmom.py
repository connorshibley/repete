"""Cross-sectional momentum (Jegadeesh/Titman 1993 '12-1'): rank the whole
universe by trailing return (skipping the most recent month to dodge
short-term reversal), buy the top fraction, exit when a holding falls out of
the top half. Needs ALL symbols' bars, so it runs a once-per-cycle
prepare() step before per-symbol generation.
"""
from strategies.base import Signal, total_return

NAME = "xsmom"
NEEDS_CROSS_SECTION = True


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
    pct_rank = rank / n  # 0.0 = strongest
    mom = cross_section["returns"][symbol]
    ind = {"rank": rank + 1, "universe": n, "pct_rank": round(pct_rank, 3),
           "momentum_pct": round(mom * 100, 2)}

    if not holding and pct_rank < params["buy_top_fraction"] and mom > 0:
        return Signal(symbol, "buy",
                      f"ranked {rank + 1}/{n} by {params['rank_lookback_bars']}-bar "
                      f"momentum ({mom:+.1%}, top {params['buy_top_fraction']:.0%} "
                      "of universe) — relative strength leader", ind, NAME)
    if holding and pct_rank >= params["exit_below_fraction"]:
        return Signal(symbol, "sell",
                      f"dropped to rank {rank + 1}/{n} (below top "
                      f"{params['exit_below_fraction']:.0%}) — relative strength "
                      "faded, exiting", ind, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"rank {rank + 1}/{n}, no action ({state})",
                  ind, NAME)
