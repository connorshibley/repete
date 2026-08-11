"""Unloved-sector swing entries on the SPDR sector funds themselves: buy a
deep-drawdown sector fund as it stabilizes, on a pullback to its short base,
and hold for the days-to-weeks repair.

Owner direction, 2026-08-11: swing trading — "finding undervalued sectors and
bottlenecks and accept volatility". This is the ETF-first expression of that
ask (single names inside lagging sectors are `reclaim`'s job, one level down
and stock-picking; the two are deliberately separate strategies with separate
gates, the same reasoning that keeps meanrev and reclaim apart).

WHY THE FUND AND NOT ITS CONSTITUENTS. A sector fund cannot go to zero the way
a single name can, does not gap on its own earnings, and carries no
survivorship story of its own — §51 measured +200.28pp of survivorship
inflation on the stock universe, and §57's probe showed the SPDR sector list
itself is stable back past this project's whole backtest window. "The sector
is cheap" is also simply a cleaner claim to test on the sector than on a
stock-level median proxy for it.

LEVELS, NOT SIGNALS — read this before touching the intraday scanner.
§19a declined intraday trading because signals live on COMPLETED daily bars
and a partial bar is an input no gate has measured. That decision stands.
What this strategy adds is a price LEVEL derived entirely from completed
bars: `assess()` names an entry zone, and whether a price is inside a
precomputed zone is a comparison, not a signal — the same standing as a
broker-side bracket stop, which also waits on live price all day. `generate()`
asks that question of the last completed close (what the 15:45 cycle and the
gate both see); `swing_scan.py` asks the SAME question of a live quote. One
implementation of the conditions and the zone, two prices. Anything that
computes an indicator from an unfinished bar does not belong in this file.

BASE, THEN DIP — the falling-knife refusal, inherited from `reclaim`:

  1. UNLOVED         — the fund is at least `min_drawdown_pct` off its own
                       `high_lookback_bars` high, AND among the deepest
                       `laggard_count` of the sectors ranked this cycle.
                       Depth relative to the cross-section, not just its own
                       history: "cheap" is a comparative claim.
  2. STOPPED FALLING — close above its `stabilize_sma_period` SMA, and that
                       SMA is no lower than it was `base_slope_bars` ago.
                       Without this the strategy catches knives: every crash
                       satisfies condition 1 all the way down.
  3. IN THE ZONE     — price inside [SMA, SMA + entry_zone_atr_mult·ATR]:
                       a pullback toward the rising base, not a chase of the
                       bounce. Intraday, a dip INTO the zone triggers the
                       entry; at the cycle, a close inside it does.

Exits: the thesis completes (drawdown recovers inside `recovered_drawdown_pct`
of the high), the base fails (close more than `exit_buffer_pct` below the
stabilize SMA), or the time stop (`max_hold_days` — a repair that has not
happened in that long is not happening). The wide bracket stop
(per-strategy `stop_atr_mult`, risk.py) protects below the base failure exit;
"accept volatility" is expressed there, per-trade, and NOT by moving any
portfolio rail.

ARGUED AGAINST IN ADVANCE, per house rule: sector "value" measured as
drawdown-from-high has a known failure mode — sectors can be cheap for
correct reasons for years (energy 2014-2020). The time stop and the base test
bound how long and how badly that can hurt one position, but nothing here
makes the thesis TRUE. It ships `enabled: false` and stays that way unless
the pre-registered §62 gate says otherwise; the gate is an ENABLEMENT claim
only (§52/§57 — no dataset this repo owns may back an EDGE claim).
"""
from datetime import datetime

from strategies.base import Signal, sma, atr

NAME = "swing_sectors"
NEEDS_CROSS_SECTION = True
NEEDS_ENTRY_TS = True     # max_hold_days; see strategies.generate dispatch


def required_lookback(params: dict) -> int:
    # The drawdown window dominates; +1 for today's close alongside it.
    return params["high_lookback_bars"] + 1


def drawdown_pct(bars: list[dict], lookback: int) -> float | None:
    """How far the last close sits below the highest HIGH of the last
    `lookback` bars, in percent. 0.0 = at the high; 25.0 = a quarter below it.

    None (never a number the caller might trust) on insufficient history or a
    non-positive peak — the same fail-open-by-None contract as every indicator
    in base.py: a data outage must not fabricate a drawdown of 0 (which would
    read as "fully recovered" and trigger exits) or 100 (deepest laggard in
    the cross-section).
    """
    if len(bars) < lookback or lookback < 1:
        return None
    window = bars[-lookback:]
    peak = max(float(b["high"]) for b in window)
    if peak <= 0:
        return None
    return (1.0 - float(window[-1]["close"]) / peak) * 100.0


def prepare(all_bars: dict, params: dict, cfg: dict | None = None) -> dict:
    """Rank the sector funds by drawdown depth, deepest first.

    Returns {"drawdown": {symbol: pct}, "laggards": ordered list of the
    `laggard_count` deepest symbols that also clear `min_drawdown_pct`,
    "n": how many symbols ranked}. Symbols with insufficient history are
    excluded from the ranking, as in hi52/xsmom: inventing a depth for them
    would put a fund with three months of data on the same scale as one with
    a full year.

    `cfg` is part of the prepare() contract and unused: the universe IS the
    sector map here — each fund is its own sector — so unlike `reclaim` there
    is no `sectors:` block to consult and no constituent median to build.
    `all_bars` arrives already scoped to this strategy's universe plus
    anything it holds (see strategies.prepare_one; the scoping is
    load-bearing).
    """
    lookback = params["high_lookback_bars"]
    dd = {}
    for sym, bars in all_bars.items():
        value = drawdown_pct(bars, lookback)
        if value is not None:
            dd[sym] = value
    ordered = sorted(dd, key=dd.get, reverse=True)          # deepest first
    laggards = [s for s in ordered[:params["laggard_count"]]
                if dd[s] >= params["min_drawdown_pct"]]
    return {"drawdown": dd, "laggards": laggards, "n": len(dd)}


def assess(symbol: str, bars: list[dict], params: dict,
           cross_section: dict | None) -> dict | None:
    """The entry conditions and zone, price-free — THE single implementation.

    Returns {"zone_low", "zone_high", "drawdown", "atr", "sma"} when every
    condition EXCEPT the price test holds, else None. `generate()` compares
    the zone against the last completed close; `swing_scan.py` compares the
    SAME zone against a live quote. Both callers and the §62 gate therefore
    run one set of conditions — a second copy of any test in this function is
    a live/sim divergence waiting to be measured (§13, §19a, §19b, §22 are
    the four that already cost real rework).
    """
    if not cross_section or cross_section.get("n", 0) < params["min_ranked"]:
        return None
    if symbol not in cross_section["laggards"]:
        return None
    closes = [b["close"] for b in bars]
    base_sma = sma(closes, params["stabilize_sma_period"])
    prior_sma = sma(closes[:-params["base_slope_bars"]],
                    params["stabilize_sma_period"])
    a = atr(bars, params["atr_period"])
    if base_sma is None or prior_sma is None or a is None:
        return None
    close = float(closes[-1])
    # STOPPED FALLING: above the short base, and the base itself no longer
    # declining. `>=` on the slope on purpose — a flat base is a base; only a
    # still-falling one is the knife.
    if close <= base_sma or base_sma < prior_sma:
        return None
    return {
        "zone_low": base_sma,
        "zone_high": base_sma + params["entry_zone_atr_mult"] * a,
        "drawdown": cross_section["drawdown"][symbol],
        "atr": a,
        "sma": base_sma,
    }


def _held_days(bars: list[dict], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    now = datetime.fromisoformat(bars[-1]["ts"])
    entered = datetime.fromisoformat(entry_ts)
    return (now - entered).days


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None,
             entry_ts: str | None = None) -> Signal:
    need = required_lookback(params)
    if len(bars) < need:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(bars)} bars, need {need})",
                      strategy=NAME)
    close = float(bars[-1]["close"])

    if holding:
        # EXIT LOGIC IS PER-SYMBOL ONLY — no cross-section dependence, so a
        # held fund that has left the universe or fallen out of the ranking
        # can always still be exited (the unexitable-position trap the
        # prepare_one docstring names).
        dd = drawdown_pct(bars, params["high_lookback_bars"])
        closes = [b["close"] for b in bars]
        base_sma = sma(closes, params["stabilize_sma_period"])
        ind = {"close": round(close, 2),
               "drawdown_pct": round(dd, 2) if dd is not None else None}
        if dd is not None and dd <= params["recovered_drawdown_pct"]:
            return Signal(symbol, "sell",
                          f"drawdown recovered to {dd:.1f}% of its "
                          f"{params['high_lookback_bars']}-bar high (inside "
                          f"{params['recovered_drawdown_pct']:.0f}%) — thesis "
                          f"complete, exiting", ind, NAME)
        if (base_sma is not None
                and close < base_sma * (1 - params["exit_buffer_pct"] / 100)):
            return Signal(symbol, "sell",
                          f"close {params['exit_buffer_pct']:.0f}% below the "
                          f"SMA{params['stabilize_sma_period']} base — the "
                          f"stabilization failed, exiting", ind, NAME)
        held = _held_days(bars, entry_ts)
        if held is not None and held >= params["max_hold_days"]:
            return Signal(symbol, "sell",
                          f"max hold {params['max_hold_days']}d reached without "
                          f"repair — time stop, exiting", ind, NAME)
        return Signal(symbol, "hold", "awaiting sector repair (holding)",
                      ind, NAME)

    zone = assess(symbol, bars, params, cross_section)
    if zone is None:
        return Signal(symbol, "hold", "conditions not met (flat)",
                      strategy=NAME)
    ind = {"close": round(close, 2),
           "drawdown_pct": round(zone["drawdown"], 2),
           "zone_low": round(zone["zone_low"], 2),
           "zone_high": round(zone["zone_high"], 2)}
    if zone["zone_low"] <= close <= zone["zone_high"]:
        return Signal(symbol, "buy",
                      f"unloved sector {zone['drawdown']:.1f}% off its "
                      f"{params['high_lookback_bars']}-bar high, stabilized "
                      f"above a rising SMA{params['stabilize_sma_period']}, "
                      f"close {close:.2f} inside the entry zone "
                      f"[{zone['zone_low']:.2f}, {zone['zone_high']:.2f}] — "
                      f"swing entry on the pullback", ind, NAME)
    return Signal(symbol, "hold",
                  f"armed — close {close:.2f} outside the entry zone "
                  f"[{zone['zone_low']:.2f}, {zone['zone_high']:.2f}]",
                  ind, NAME)
