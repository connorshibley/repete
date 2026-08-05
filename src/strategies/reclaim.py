"""Unloved-sector 200-DMA reclaim: buy a beaten-down name in a beaten-down
sector, on the bar it climbs back above its own 200-day moving average.

Owner direction, 2026-08-05. It earns its place by being mechanically opposite
to everything already here — every other strategy buys strength of some kind:

  ma_crossover  two moving averages cross          — lagging trend
  tsmom         the asset's own trailing return    — trend, measured directly
  meanrev       RSI(2) dip INSIDE an uptrend       — counter-trend, above SMA200
  xsmom         relative strength vs the universe  — cross-sectional leader
  donchian      price makes a new extreme          — price-level breakout
  reclaim       a laggard climbing back to trend   — counter-trend, BELOW SMA200

READ THIS BEFORE READING ANY BACKTEST OF IT
-------------------------------------------
Every entry here inverts the filter meanrev's docstring calls LOAD-BEARING:
"unfiltered RSI(2) buying lost heavily in the 2022 bear. The SMA200 filter is
load-bearing." That is not an argument against this strategy, but it is the
reason this is a SEPARATE strategy with its own gate rather than a flipped
comparison inside meanrev — the two make opposite bets and must be measured
apart, and meanrev keeps the shallow-dip-above-trend case exclusively.

It is also the strategy family most flattered by survivorship. The names that
fell below their 200-DMA and never came back are precisely the ones a survivor
universe deletes, and §51 measured +200.28pp of survivorship inflation on this
project's own live universe. Backtests are already a NEGATIVE FILTER ONLY here;
for this strategy a PASS is not merely uninformative, it is MISLEADING. Only a
FAIL is decisive.

Prior expectation, stated before this was ever run: a trend-repair trade should
show a MODERATE win rate with a large average winner (the repair either takes
hold or it does not), and a very high win rate would be a reason for suspicion —
it is the signature of a strategy that has learned which names survived.

BASE, THEN RECLAIM
------------------
"Below the 200-DMA" is the hunting ground, not the trigger. Three distinct
things must be true, and dropping any one of them turns this into a different
and worse strategy:

  1. OUT OF FAVOUR    — below its own 200-DMA for `min_days_below` bars, so it
                        is a genuine decline rather than a two-day dip (which
                        is meanrev's job, on the other side of the line).
  2. STOPPED FALLING  — the base test. Close above a `base_sma_period` SMA that
                        is HIGHER than it was `base_slope_bars` ago. Without
                        this the strategy catches falling knives: a knife
                        crosses its 200-DMA too, on the way down through it.
  3. RECLAIMED        — the cross happens on THIS bar. Entering while still
                        below would mean unbounded time in drawdown; entering
                        long after means the move is spent.

Sector selection is DEPTH, not breadth (owner decision): a sector ranks by the
MEDIAN distance of its constituents below their own 200-DMAs. Depth answers "how
far is the typical name from trend"; breadth would answer "how many names are
below it". They disagree — 90% of names 2% below is not the same market as 40%
of names 30% below — and depth was chosen.

Exits respect the swing guard (`min_holding_days`), so the earliest strategy
exit is day 2; the broker-side bracket legs protect before that.
"""
from datetime import datetime
from statistics import median

# No `sector_of` here on purpose: `prepare` builds the symbol -> sector mapping
# once and hands it to `generate` inside the cross-section context, so per-symbol
# generation stays a pure function of (bars, params, ctx) and never reaches back
# into cfg. `risk.py` is the module that imports the helper.
from strategies.base import Signal, sma

NAME = "reclaim"
NEEDS_CROSS_SECTION = True   # sector ranking is a whole-universe question
NEEDS_ENTRY_TS = True        # max_hold_days needs to know when we got in


def required_lookback(params: dict) -> int:
    """Enough bars to evaluate the 200-DMA at every bar of the look-back window.

    `trend_sma_period + min_days_below` is the floor: proving the close sat
    below its 200-DMA for the last N bars needs a 200-DMA computed AT each of
    those N bars, not just today's. The +1 is the previous close, which the
    reclaim test compares against.

    MAX, not sum. The base window (`base_sma_period + base_slope_bars`) sits
    INSIDE the below-trend window — they are the same recent bars looked at two
    ways, not two windows laid end to end. Summing them asked for 301 bars where
    241 suffice, and `strategies.max_lookback_bars` takes the max across ALL
    configured strategies including disabled ones, so that surplus would have
    raised the live per-symbol fetch from 253 bars to 301 for a strategy that
    ships turned off — a 19% larger data pull, on every symbol, every cycle,
    bought for nothing.
    """
    return max(params["trend_sma_period"] + params["min_days_below"],
               params["base_sma_period"] + params["base_slope_bars"]) + 1


def _pct_vs_sma(closes: list[float], period: int) -> float | None:
    """How far the latest close sits above (+) or below (-) its own SMA, in %."""
    s = sma(closes, period)
    if not s:
        return None
    return (closes[-1] / s - 1.0) * 100.0


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Rank sectors by the MEDIAN of their constituents' distance from trend.

    Returns
      {"laggards": {sector, ...},          # the bottom `laggard_sector_count`
       "scores":   {sector: median_pct},   # negative = below trend
       "sectors":  {symbol: sector},       # so generate() needs no cfg
       "ranked":   [(sector, score), ...]} # most beaten-down first

    A sector with fewer than `min_sector_constituents` usable names this cycle
    is NOT RANKED AT ALL rather than ranked on what is left. A median over two
    names is not a sector reading, and ranking it anyway would let a data outage
    manufacture a laggard — the feed drops nine of twelve names, the three that
    remain happen to be weak, and the strategy buys into a sector nothing
    actually measured.
    """
    cfg = cfg or {}
    period = params["sector_sma_period"]
    min_n = params.get("min_sector_constituents", 1)

    scores: dict = {}
    for sector, syms in (cfg.get("sectors") or {}).items():
        pcts = []
        for sym in (syms or ()):
            bars = all_bars.get(sym)
            if not bars:
                continue
            p = _pct_vs_sma([b["close"] for b in bars], period)
            if p is not None:
                pcts.append(p)
        if len(pcts) >= min_n:
            scores[sector] = median(pcts)

    ranked = sorted(scores.items(), key=lambda kv: kv[1])   # most negative first
    n_lag = params.get("laggard_sector_count", 0)
    return {"laggards": {s for s, _ in ranked[:n_lag]},
            "scores": scores,
            "sectors": {sym: sec
                        for sec, syms in (cfg.get("sectors") or {}).items()
                        for sym in (syms or ())},
            "ranked": ranked}


def _held_days(bars: list[dict], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    return (datetime.fromisoformat(bars[-1]["ts"])
            - datetime.fromisoformat(entry_ts)).days


def _below_for(closes: list[float], period: int, n: int) -> bool:
    """Was the close below its OWN 200-DMA on each of the `n` bars before this?

    Consecutive, deliberately: a name that pokes above trend and falls back has
    not been persistently out of favour, and resetting the clock is the honest
    reading of "out of favour". It also keeps the definition checkable — there
    is no "below on 80% of the last N bars" threshold to tune after the fact.

    The window ENDS at the previous bar. Today's close is above the line (that
    is the reclaim), so including it would make this trivially false.
    """
    if n <= 0:
        return True
    if len(closes) < period + n + 1:
        return False
    for i in range(1, n + 1):
        window = closes[:len(closes) - i]      # closes up to and incl. bar -i
        s = sma(window, period)
        if s is None or window[-1] >= s:
            return False
    return True


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None,
             entry_ts: str | None = None) -> Signal:
    need = required_lookback(params)
    closes = [b["close"] for b in bars]
    trend_p = params["trend_sma_period"]
    if len(closes) < need:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(closes)} bars, need {need})",
                      strategy=NAME)

    sma_now = sma(closes, trend_p)
    if sma_now is None:
        return Signal(symbol, "hold", "no trend SMA", strategy=NAME)

    close = closes[-1]
    ind = {"close": round(close, 2), f"sma{trend_p}": round(sma_now, 2),
           "pct_vs_trend": round((close / sma_now - 1) * 100, 2)}

    # ---- EXIT (owns the position; runs even when the strategy is disabled) ---
    if holding:
        buf = params.get("exit_buffer_pct", 0.0)
        floor_px = sma_now * (1 - buf / 100.0)
        if close < floor_px:
            return Signal(symbol, "sell",
                          f"closed {ind['pct_vs_trend']:+.2f}% vs SMA{trend_p}, "
                          f"back below it by more than {buf}% — the reclaim "
                          "failed", ind, NAME)
        held = _held_days(bars, entry_ts)
        max_hold = params.get("max_hold_days", 0)
        if max_hold and held is not None and held >= max_hold:
            ind["held_days"] = held
            return Signal(symbol, "sell",
                          f"held {held}d (max {max_hold}) — the trend repair "
                          "has not delivered, releasing the capital", ind, NAME)
        return Signal(symbol, "hold",
                      f"above SMA{trend_p} less the {buf}% buffer — reclaim "
                      "intact", ind, NAME)

    # ---- ENTRY ------------------------------------------------------------
    if not cross_section:
        return Signal(symbol, "hold", "sector cross-section unavailable",
                      strategy=NAME)

    sector = cross_section.get("sectors", {}).get(symbol)
    if sector is None:
        # Not a mapped name. Reached whenever something outside the sector
        # universe is offered — a held symbol, a news nomination — and it is a
        # hold rather than an error: this strategy simply has no opinion.
        return Signal(symbol, "hold", "symbol is not in the sector map",
                      ind, NAME)
    ind["sector"] = sector
    score = cross_section.get("scores", {}).get(sector)
    if score is not None:
        ind["sector_median_pct"] = round(score, 2)

    if sector not in cross_section.get("laggards", set()):
        return Signal(symbol, "hold",
                      f"{sector} is not among the "
                      f"{params.get('laggard_sector_count', 0)} most "
                      "beaten-down sectors", ind, NAME)

    n_below = params["min_days_below"]
    if not _below_for(closes, trend_p, n_below):
        return Signal(symbol, "hold",
                      f"has not been below SMA{trend_p} for {n_below} "
                      "consecutive bars — not yet out of favour", ind, NAME)

    # BASE: the decline has stalled. Both halves matter — price back above the
    # short SMA says "it is lifting", the SMA's own slope says "the lift is not
    # just one green bar".
    # MEASURED AS OF THE PREVIOUS BAR, excluding today's close on both sides.
    # The base is a claim about the run-up TO the reclaim, and including the
    # reclaim bar lets that one bar satisfy it: a name in freefall that gaps up
    # violently drags its own short SMA above where the SMA sat three bars ago,
    # so "the base is rising" becomes true because of the very bar whose
    # validity the base test exists to judge. That is precisely the falling
    # knife this check is here to refuse — caught by
    # test_no_buy_without_a_base, which a same-bar formulation passed.
    base_p = params["base_sma_period"]
    slope_n = params["base_slope_bars"]
    prior = closes[:-1]
    base_prev = sma(prior, base_p)
    base_before = sma(prior[:len(prior) - slope_n], base_p)
    if base_prev is None or base_before is None:
        return Signal(symbol, "hold", "no base SMA", ind, NAME)
    ind[f"sma{base_p}"] = round(base_prev, 2)
    if prior[-1] <= base_prev:
        return Signal(symbol, "hold",
                      f"close is not above SMA{base_p} — still falling",
                      ind, NAME)
    if base_prev <= base_before:
        return Signal(symbol, "hold",
                      f"SMA{base_p} is not rising over {slope_n} bars — no "
                      "base has formed", ind, NAME)

    # RECLAIM: the cross happens on THIS bar. Compared against the PREVIOUS
    # bar's own 200-DMA, not today's — using today's for both would call a
    # symbol that had been above the line for weeks a fresh cross on any day the
    # average happened to tick up past the older close.
    sma_prev = sma(closes[:-1], trend_p)
    if sma_prev is None:
        return Signal(symbol, "hold", "no prior trend SMA", ind, NAME)
    if not (closes[-2] <= sma_prev < close):
        return Signal(symbol, "hold",
                      f"no SMA{trend_p} reclaim on this bar", ind, NAME)

    return Signal(symbol, "buy",
                  f"{sector} is among the most beaten-down sectors "
                  f"(median {score:+.2f}% vs SMA{trend_p}); {symbol} spent "
                  f"{n_below}+ bars below its own SMA{trend_p}, based above a "
                  f"rising SMA{base_p}, and reclaimed the line this bar",
                  ind, NAME)
