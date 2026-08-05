"""Cross-sectional low volatility (the 'low-beta anomaly', Black 1972;
Frazzini/Pedersen 2014 'Betting Against Beta'): rank the whole universe by
trailing realised volatility, buy the calmest fraction, exit when a holding
drifts into the noisy half.

Why this one, alongside momentum
--------------------------------
Momentum and low-volatility are the two anomaly families with the strongest
post-publication survival record, and neither needs data this repo lacks —
both are computable from the OHLCV already in the snapshot. §32 tests them as
one pre-registered family rather than one after another, so the multiple-
comparison correction is honest.

The claim is NOT "calm stocks go up". It is that risk-adjusted returns have
historically been higher at the low-volatility end than CAPM predicts, i.e.
the compensation for bearing volatility is negative over long horizons.

Like xsmom this needs ALL symbols' bars, so it runs a once-per-cycle
prepare() before per-symbol generation. Volatility is measured by
`risk.realized_annual_vol`, the same function the vol-target sizing rail uses
— one definition, so a name cannot be 'high vol' for sizing and 'low vol' for
selection in the same cycle.
"""
from strategies.base import Signal

NAME = "lowvol"
NEEDS_CROSS_SECTION = True


def required_lookback(params: dict) -> int:
    # realized_annual_vol needs period+1 closes; one spare for the ratio.
    return params.get("vol_period", 60) + 2


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Rank the universe by trailing realised volatility, calmest first.

    Returns {"ranks": {symbol: 0-based rank}, "vols": {symbol: annual vol},
    "n": universe size}. Symbols with insufficient history are excluded — they
    simply cannot signal this cycle, exactly as in xsmom.
    """
    # Local import: risk.py imports nothing from strategies, so this is acyclic.
    # Deliberately reusing the sizing rail's volatility definition rather than
    # writing a second one — two definitions of "volatility" that drift apart
    # is the §29 failure mode wearing a different hat.
    import risk

    period = params.get("vol_period", 60)
    vols = {}
    for sym, bars in all_bars.items():
        if len(bars) < required_lookback(params):
            continue
        v = risk.realized_annual_vol(bars, period)
        if v is not None and v > 0:
            vols[sym] = v
    ordered = sorted(vols, key=vols.get)          # calmest first
    return {"ranks": {s: i for i, s in enumerate(ordered)},
            "vols": vols, "n": len(ordered)}


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    if not cross_section or cross_section.get("n", 0) < 4:
        return Signal(symbol, "hold", "cross-section unavailable or too small",
                      strategy=NAME)
    ranks = cross_section["ranks"]
    if symbol not in ranks:
        return Signal(symbol, "hold", "insufficient history for ranking",
                      strategy=NAME)

    n = cross_section["n"]
    rank = ranks[symbol]
    pct_rank = rank / n                            # 0.0 = calmest
    vol = cross_section["vols"][symbol]
    ind = {"rank": rank + 1, "universe": n, "pct_rank": round(pct_rank, 3),
           "annual_vol_pct": round(vol * 100, 2)}

    if not holding and pct_rank < params["buy_bottom_fraction"]:
        return Signal(symbol, "buy",
                      f"ranked {rank + 1}/{n} by {params.get('vol_period', 60)}-bar "
                      f"realised volatility ({vol:.1%} annualised, calmest "
                      f"{params['buy_bottom_fraction']:.0%} of universe) — "
                      f"low-volatility anomaly", ind, NAME)
    if holding and pct_rank >= params["exit_above_fraction"]:
        return Signal(symbol, "sell",
                      f"volatility rose to rank {rank + 1}/{n} (above the calmest "
                      f"{params['exit_above_fraction']:.0%}) — no longer a "
                      f"low-volatility holding, exiting", ind, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"vol rank {rank + 1}/{n}, no action ({state})",
                  ind, NAME)
