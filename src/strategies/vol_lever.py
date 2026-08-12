"""§64 discrete volatility-managed exposure (Moreira & Muir, JF 2017): hold
the index fund while realized volatility is LOW by its own expanding history,
stand aside while it is high, evaluated monthly.

DISCRETIZED, AND THE DISCRETIZATION IS PRE-COMMITTED. The published strategy
scales exposure continuously by c/RV²; this codebase's signal vocabulary is
enter/exit, so the rule here is the binary quantization — in when last
month's realized vol is below `entry_frac` × the expanding median of all
month-start readings to date, out when above `exit_frac` × it (hysteresis so
the boundary does not churn). The cap the paper's own Table V shows removing
most of the raw-return upside is exactly the `risk.margin.multiplier` a spec
arm sets; nothing here levers.

TWO LOOKAHEAD TRAPS, BOTH CLOSED BY CONSTRUCTION. Liu-Tang-Zhou showed the
usual c is calibrated on the FULL sample — average exposure ≈ 1 is set with
hindsight. The expanding median uses only readings available at each
evaluation date. Cederburg et al. showed the headline alpha needs ex-post
combination weights; there is no combination here, only the standalone rule.

Expected per its own dossier: total-return clause FAILS, drawdown clause
passes — the spec's reading rule pre-commits that this candidate can never
license an EDGE registration. Ships `enabled: false`.
"""
import statistics

from strategies.base import Signal

NAME = "vol_lever"
NEEDS_CROSS_SECTION = False


def required_lookback(params: dict) -> int:
    # Config-driven, NOT a constant, because this number feeds
    # `max_lookback_bars` across the WHOLE registry — disabled strategies
    # included — and a hard-coded 2520 here would make the live bot fetch ten
    # times the history it needs for a strategy that ships off (the shipped
    # ceiling is pinned at 253 by test_contraction). The shipped value stays
    # inside that ceiling; a spec ARM raises `history_bars` to give the
    # expanding median real history, and the `min_history_readings` floor in
    # generate() refuses to trade on less either way.
    return int(params.get("history_bars", 253))


def _bar_month(bar: dict) -> str:
    return bar["ts"][:7]


def realized_vol(closes: list[float], lookback: int) -> float | None:
    """Annualized stdev of simple daily returns over the last `lookback`
    closes. None on insufficient history — never a number to trust."""
    if len(closes) < lookback + 1:
        return None
    window = closes[-(lookback + 1):]
    rets = [(b / a) - 1.0 for a, b in zip(window, window[1:]) if a > 0]
    if len(rets) < 2:
        return None
    return statistics.pstdev(rets) * (252 ** 0.5)


def month_start_vols(bars: list[dict], lookback: int) -> list[float]:
    """Realized-vol readings as of each first-bar-of-month, oldest first —
    the expanding history the median is taken over. The latest month's
    reading is INCLUDED; median of n readings with one new member moves
    little, and excluding it would special-case the only reading that
    matters."""
    closes = [float(b["close"]) for b in bars]
    vols = []
    for i in range(1, len(bars)):
        if _bar_month(bars[i]) != _bar_month(bars[i - 1]):
            v = realized_vol(closes[:i + 1], lookback)
            if v is not None:
                vols.append(v)
    return vols


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    lookback = int(params.get("lookback_days", 21))
    min_hist = int(params.get("min_history_readings", 12))
    if len(bars) < 2:
        return Signal(symbol, "hold", "insufficient history", strategy=NAME)
    if "ts" not in bars[-1] or "ts" not in bars[-2]:
        # Month boundaries need dates; bare-OHLC bars are a legal input and
        # must produce a hold, not a KeyError (tom_tilt's reason exactly).
        return Signal(symbol, "hold", "bars carry no timestamps", strategy=NAME)
    if _bar_month(bars[-1]) == _bar_month(bars[-2]):
        state = "holding" if holding else "flat"
        return Signal(symbol, "hold",
                      f"not a month boundary ({state})", strategy=NAME)
    vols = month_start_vols(bars, lookback)
    if len(vols) < min_hist:
        return Signal(symbol, "hold",
                      f"only {len(vols)} month-start vol readings, "
                      f"need {min_hist} for a median worth trusting",
                      strategy=NAME)
    med = statistics.median(vols)
    cur = vols[-1]
    ind = {"realized_vol": round(cur, 4), "expanding_median": round(med, 4),
           "readings": len(vols)}
    if not holding and cur < float(params["entry_frac"]) * med:
        return Signal(symbol, "buy",
                      f"realized vol {cur:.1%} under {params['entry_frac']}x "
                      f"the expanding median {med:.1%} — calm regime",
                      ind, NAME)
    if holding and cur > float(params["exit_frac"]) * med:
        return Signal(symbol, "sell",
                      f"realized vol {cur:.1%} over {params['exit_frac']}x "
                      f"the expanding median {med:.1%} — stepping aside",
                      ind, NAME)
    state = "holding" if holding else "flat"
    return Signal(symbol, "hold", f"inside the hysteresis band ({state})",
                  ind, NAME)
