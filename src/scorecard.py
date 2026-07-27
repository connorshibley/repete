"""Monthly scorecard vs the S&P (2026-07-21, enterprise-hardening pass).

The owner's goal — beat the S&P 500 — is MEASURED month by month here and
published (review.py + dashboard). It is a tracked objective, not a promise:
no system beats the index every month, and wiring a return target into
sizing or signals is the documented failure mode of every LLM-trader we've
reviewed. The bot's monthly return comes from its own equity snapshots
(cycle_complete ledger events); SPY's from daily bars. Pure functions, no
network — callers pass bars in.
"""
import json
from collections import OrderedDict


def _month(ts: str) -> str:
    return ts[:7]  # YYYY-MM


def equity_by_month(records: list[dict]) -> "OrderedDict[str, dict]":
    """month -> {first, last, max_dd_pct} from cycle_complete equity events."""
    out: OrderedDict[str, dict] = OrderedDict()
    for r in records:
        if r.get("type") != "event" or r.get("event") != "cycle_complete":
            continue
        try:
            eq = float(json.loads(r.get("detail") or "")["equity"])
        except (ValueError, TypeError, KeyError):
            continue
        m = out.setdefault(_month(r["ts"]),
                           {"first": eq, "last": eq, "peak": eq, "max_dd_pct": 0.0})
        m["last"] = eq
        m["peak"] = max(m["peak"], eq)
        if m["peak"] > 0:
            m["max_dd_pct"] = min(m["max_dd_pct"],
                                  (eq - m["peak"]) / m["peak"] * 100)
    return out


def _close_on_or_before(bars: list[dict], ts: str) -> float | None:
    """Last close at or before `ts`. None when the window predates the bars.

    Strictly on-or-BEFORE: a trade that closed at 15:45 must be compared with
    the benchmark as of the same moment, never a later bar it could not have
    seen. Bars are assumed ascending by ts, which is how the broker returns
    them and how the snapshots are stored.
    """
    out = None
    for b in bars or []:
        if b.get("ts", "") <= ts:
            out = b.get("close")
        else:
            break
    return out


def benchmark_return_pct(bench_bars: list[dict], entry_ts: str,
                         exit_ts: str) -> float | None:
    """Benchmark % return over exactly this trade's holding window.

    None — not 0.0 — when it cannot be computed. A missing benchmark and a flat
    benchmark are different facts, and recording the second when you mean the
    first turns "unknown" into "the bot matched the market", which is the more
    flattering of the two.

    Why this exists (2026-07-27): the outcome record held only absolute pnl_pct,
    so a +3% trade in a +5% week was stored as a `win` and lessons.py taught the
    judge it was a good call. Same shape as the comparison this repo already
    applies to the strategy as a whole (buy-and-hold in backtest.py, monthly SPY
    below) — it was just never applied per trade.
    """
    if not bench_bars or not entry_ts or not exit_ts or exit_ts < entry_ts:
        return None
    start = _close_on_or_before(bench_bars, entry_ts)
    end = _close_on_or_before(bench_bars, exit_ts)
    if not start or end is None or start <= 0:
        return None
    return (end - start) / start * 100


def spy_by_month(spy_bars: list[dict]) -> dict:
    """month -> {first, last} closes from daily SPY bars."""
    out: dict = {}
    for b in spy_bars or []:
        m = out.setdefault(_month(b["ts"]), {"first": b["close"]})
        m["last"] = b["close"]
    return out


def monthly_scorecard(records: list[dict], spy_bars: list[dict],
                      realized_by_month: dict | None = None) -> dict:
    """{"months": [{month, bot_ret_pct, spy_ret_pct, beat, max_dd_pct,
    realized_pnl}], "summary": {months_beaten, months_total, cum_bot_pct,
    cum_spy_pct}}. A month with equity snapshots but no SPY overlap gets
    spy_ret_pct None and doesn't count toward beaten/total."""
    eq = equity_by_month(records)
    spy = spy_by_month(spy_bars)
    realized = realized_by_month or {}
    months, beaten, total = [], 0, 0
    cum_bot = cum_spy = 1.0
    for m, e in eq.items():
        bot = (e["last"] - e["first"]) / e["first"] * 100 if e["first"] else 0.0
        cum_bot *= 1 + bot / 100
        s = spy.get(m)
        spy_ret = ((s["last"] - s["first"]) / s["first"] * 100
                   if s and s.get("first") else None)
        beat = None
        if spy_ret is not None:
            cum_spy *= 1 + spy_ret / 100
            total += 1
            beat = bot > spy_ret
            beaten += 1 if beat else 0
        months.append({"month": m, "bot_ret_pct": round(bot, 2),
                       "spy_ret_pct": (round(spy_ret, 2)
                                       if spy_ret is not None else None),
                       "beat": beat,
                       "max_dd_pct": round(e["max_dd_pct"], 2),
                       "realized_pnl": round(realized.get(m, 0.0), 2)})
    return {"months": months,
            "summary": {"months_beaten": beaten, "months_total": total,
                        "cum_bot_pct": round((cum_bot - 1) * 100, 2),
                        "cum_spy_pct": round((cum_spy - 1) * 100, 2)}}


def realized_pnl_by_month(records: list[dict]) -> dict:
    """month -> realized P&L from outcome events (closed trades only)."""
    out: dict = {}
    for r in records:
        if r.get("type") == "outcome":
            m = _month(r["ts"])
            out[m] = out.get(m, 0.0) + (r.get("pnl") or 0.0)
    return out


def format_lines(card: dict) -> list[str]:
    """Human-readable block for review.py."""
    s = card["summary"]
    if not card["months"]:
        return ["MONTHLY SCORECARD vs S&P: no equity history yet"]
    lines = ["MONTHLY SCORECARD vs S&P (goal: beat SPY on a rolling basis — "
             "measured every month, promised never):"]
    for m in card["months"]:
        spy = (f"{m['spy_ret_pct']:+.2f}%" if m["spy_ret_pct"] is not None
               else "n/a")
        tag = {True: "BEAT", False: "trailed", None: "—"}[m["beat"]]
        lines.append(f"  {m['month']}: bot {m['bot_ret_pct']:+.2f}% vs SPY "
                     f"{spy} [{tag}]  maxDD {m['max_dd_pct']:.2f}%  "
                     f"realized ${m['realized_pnl']:,.2f}")
    lines.append(f"  beaten {s['months_beaten']}/{s['months_total']} months | "
                 f"cumulative bot {s['cum_bot_pct']:+.2f}% vs SPY "
                 f"{s['cum_spy_pct']:+.2f}%")
    return lines
