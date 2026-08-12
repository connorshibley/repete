"""§64 Global Equities Momentum (Antonacci, *Dual Momentum Investing*, 2014):
monthly, hold US equities if their 12-month return beats both international
equities and T-bills; else international if it beats T-bills; else bonds.

THE EXACT BOOK SPEC, AND ONLY THE BOOK SPEC. ReSolve ran 1,226 sibling
specifications and found the published 12-month/monthly one indistinguishable
from luck among them (64pp of five-year dispersion). So there is ONE lookback
here, no grid, and the spec registers this as a FALSIFICATION candidate: the
tracked post-publication record (8.4% vs SPY 13.6% CAGR, 2014-2026; the 2022
haven leg fell 13%) predicts it fails, and a clean recorded kill is the value.

THE T-BILL THRESHOLD FAILS CLOSED. Absolute momentum needs a risk-free
return, which no traded bar carries. It arrives as the ^IRX aux series via
`cfg["_rate_bars"]` (simulate_ensemble injects it from its `rate_bars`
kwarg; ts-bounded to the evaluation bar). Without it this module emits NO
entries — a GEM that silently used zero as the threshold would hold equities
through exactly the regimes the filter exists to leave. Live main.py never
injects the key, so the strategy is inert live by construction; it also
ships `enabled: false`, and enabling it live would first need a live rate
feed (that gap is recorded in the §64 write-up, not discovered later).

Not xsmom wearing fewer symbols: xsmom ranks 30 stocks against each other
with a skip-month and holds a top-quartile basket; this rotates among two
asset-class funds and a bond fund with an absolute filter — the Antonacci
composition, not the Jegadeesh-Titman ranking.
"""
from strategies.base import Signal, total_return

NAME = "gem"
NEEDS_CROSS_SECTION = True


def required_lookback(params: dict) -> int:
    return params["lookback_days"] + 1


def _bar_month(bar: dict) -> str:
    return bar["ts"][:7]


def tbill_return(rate_bars: dict | None, ts: str, lookback: int) -> float | None:
    """Cumulative T-bill return over `lookback` trading days ending at `ts`,
    from ^IRX daily annualized-percent prints, ts-bounded. None when the
    series is absent or short — the caller must treat that as 'no threshold',
    never as 'threshold zero'."""
    series = (rate_bars or {}).get("^IRX") or []
    rates = [b["close"] for b in series if b.get("ts", "") <= ts]
    if len(rates) < lookback:
        return None
    return sum(r / 100.0 / 252.0 for r in rates[-lookback:])


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Pick this month's target leg, or None between rebalance dates.

    Evaluation happens on the first bar of a new month, read off the US leg's
    own bars (every leg trades the same calendar). Returns
    {"target": symbol|None, "eval": bool, "returns": {...}} — target None with
    eval True means the T-bill threshold was unavailable and entries must not
    happen this month (fail closed, per the module docstring).
    """
    us, intl, bonds = params["us"], params["intl"], params["bonds"]
    lookback = params["lookback_days"]
    spine = all_bars.get(us) or []
    if len(spine) < 2 or "ts" not in spine[-1] or "ts" not in spine[-2]:
        return {"target": None, "eval": False, "returns": {}}
    if _bar_month(spine[-1]) == _bar_month(spine[-2]):
        return {"target": None, "eval": False, "returns": {}}
    r_us = total_return(spine, lookback)
    r_intl = total_return(all_bars.get(intl) or [], lookback)
    r_tb = tbill_return((cfg or {}).get("_rate_bars"), spine[-1]["ts"],
                        lookback)
    rets = {"us": r_us, "intl": r_intl, "tbill": r_tb}
    if r_us is None or r_intl is None or r_tb is None:
        return {"target": None, "eval": True, "returns": rets}
    winner, r_win = (us, r_us) if r_us >= r_intl else (intl, r_intl)
    target = winner if r_win > r_tb else bonds
    return {"target": target, "eval": True, "returns": rets}


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    cs = cross_section or {}
    target = cs.get("target")
    if not cs.get("eval"):
        state = "holding" if holding else "flat"
        return Signal(symbol, "hold", f"not a rebalance date ({state})",
                      strategy=NAME)
    rets = {k: (round(v, 4) if v is not None else None)
            for k, v in (cs.get("returns") or {}).items()}
    if target is None:
        # Threshold unavailable: never enter, but also never force an exit —
        # a data outage must not liquidate the book (the exit-rail polarity
        # this codebase keeps everywhere).
        return Signal(symbol, "hold",
                      "T-bill threshold unavailable — no entries this month",
                      rets, NAME)
    if holding and symbol != target:
        return Signal(symbol, "sell",
                      f"monthly rotation: target leg is {target}", rets, NAME)
    if not holding and symbol == target:
        return Signal(symbol, "buy",
                      f"monthly rotation: {symbol} is the dual-momentum leg",
                      rets, NAME)
    state = "holding target" if holding else "not the target"
    return Signal(symbol, "hold", f"{state} ({target})", rets, NAME)
