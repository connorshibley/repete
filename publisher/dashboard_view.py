"""Authenticated subscriber dashboard (Phase E, 2026-07-22).

The signed-in counterpart to the public dashboard: the same honest numbers,
scoped to the viewer's tier. Free sees decisions delayed by
`publisher.free_delay_days` with the judge's reasoning withheld; paid sees
same-day decisions with the full reasoning, the bull/bear debate, stated
confidence, and journal entries.

READ-ONLY (invariant #9): every figure comes from `content.feed()` and the
ReadOnlyLedger's records via dashboard.py's PURE series/SVG helpers. This
module never constructs a writable store and never calls `dashboard.render()`
(which would build a writable Ledger and write a file).

Every ledger-derived string — judge reasoning, journal prose, symbols, detail
text — is HTML-escaped. The ledger holds model-generated text; it must never
be rendered as markup.

Performance figures ship with their sample size (PRODUCT.md): the closed-trade
count sits beside every P&L number, and no figure is projected or annualized.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import dashboard as dash  # noqa: E402 — pure helpers only (CSS, series, SVG)
from publisher import content  # noqa: E402

_esc = dash._esc


def _cards(feed: dict) -> str:
    eq = (feed.get("equity") or {}).get("equity")
    cards = [
        ("equity (broker-fresh)",
         dash._fmt_money(eq) if eq is not None else "—"),
        ("open positions", str(feed.get("open_positions", 0))),
        ("closed trades", str(feed.get("closed_trades", 0))),
        ("your tier", feed.get("tier", "free")),
    ]
    return ("<div class=cards>" + "".join(
        f"<div class=card><div class=v>{_esc(v)}</div>"
        f"<div class=k>{_esc(k)}</div></div>" for k, v in cards)
        + "</div>")


def _tier_notice(feed: dict, gate_open: bool, gate_reasons: list) -> str:
    if feed.get("tier") == "paid":
        return ("<p class=small>Paid tier: decisions appear same-day with the "
                "judge's full reasoning, bull/bear debate, and stated "
                "confidence, plus journal entries.</p>")
    delay = feed.get("delayed_by_days", 1)
    note = (f"<p class=small><b>Free tier.</b> Decisions below are delayed by "
            f"{_esc(delay)} day(s) and the judge's reasoning is withheld. "
            f"The numbers are the same ones the paid tier sees — the free view "
            f"is smaller, never rosier.</p>")
    if gate_open:
        note += ("<p class=small><a class=x href='/billing/checkout'>"
                 "Upgrade for same-day decisions and full reasoning</a></p>")
    else:
        reasons = "".join(f"<li>{_esc(r)}</li>" for r in (gate_reasons or []))
        note += ("<p class=small>Paid subscriptions are <b>not open yet</b>. "
                 "The gates we hold ourselves to, honestly unmet:</p>"
                 f"<ul class=small>{reasons}</ul>")
    return note


def _scorecard(card: dict, n_closed: int) -> str:
    months = (card or {}).get("months") or []
    if not months:
        return ("<p class=small>No full month of equity history yet — the "
                "monthly scorecard appears once there is one.</p>")
    rows = "".join(
        "<tr>"
        f"<td>{_esc(m.get('month'))}</td>"
        f"<td class={'win' if (m.get('bot_ret_pct') or 0) >= 0 else 'loss'}>"
        f"{m.get('bot_ret_pct')}%</td>"
        f"<td>{'—' if m.get('spy_ret_pct') is None else str(m['spy_ret_pct']) + '%'}</td>"
        f"<td>{m.get('max_dd_pct')}%</td>"
        f"<td>{dash._fmt_money(m.get('realized_pnl') or 0.0)}</td>"
        "</tr>" for m in months)
    return (
        "<div class=tblwrap><table>"
        "<tr><th>month</th><th>bot return</th><th>S&amp;P 500</th>"
        "<th>max drawdown</th><th>realized P&amp;L</th></tr>"
        f"{rows}</table></div>"
        f"<p class=small>Sample size: {n_closed} closed trade(s). Paper "
        "trading. Monthly returns are measured from equity snapshots; a month "
        "without an S&amp;P overlap shows &quot;—&quot; rather than a guess. "
        "Past results are not indicative of future results.</p>")


def _judge_block(j: dict) -> str:
    """Paid-only. Rendered only when content.feed() supplied a judge dict."""
    bits = []
    if j.get("verdict"):
        conf = j.get("confidence")
        bits.append(f"<b>judge:</b> {_esc(j['verdict'])}"
                    + (f" (confidence {_esc(conf)})" if conf is not None else ""))
    if j.get("reasoning"):
        bits.append(_esc(j["reasoning"]))
    if j.get("bull_case"):
        bits.append(f"<b>bull:</b> {_esc(j['bull_case'])}")
    if j.get("bear_case"):
        bits.append(f"<b>bear:</b> {_esc(j['bear_case'])}")
    if not bits:
        return ""
    return "<div class=small>" + "<br>".join(bits) + "</div>"


def _decisions(feed: dict) -> str:
    decisions = list(reversed(feed.get("decisions") or []))
    if not decisions:
        return "<p class=small>No decisions visible at your tier yet.</p>"
    rows = []
    for d in decisions:
        judge = _judge_block(d.get("judge") or {}) if d.get("judge") else ""
        reason = d.get("strategy_reason")
        rows.append(
            "<tr>"
            f"<td>{_esc((d.get('ts') or '')[:10])}</td>"
            f"<td><b>{_esc(d.get('symbol'))}</b></td>"
            f"<td>{_esc(d.get('action'))}</td>"
            f"<td>{_esc(d.get('strategy'))}</td>"
            f"<td>{'executed' if d.get('executed') else 'not taken'}</td>"
            f"<td>{_esc(d.get('detail'))}"
            + (f"<div class=small>{_esc(reason)}</div>" if reason else "")
            + judge + "</td></tr>")
    return ("<div class=tblwrap><table>"
            "<tr><th>date</th><th>symbol</th><th>action</th><th>strategy</th>"
            "<th>outcome</th><th>detail</th></tr>"
            + "".join(rows) + "</table></div>")


def _journal(feed: dict) -> str:
    entries = feed.get("journal")
    if entries is None:
        return ""   # free tier: key absent by construction
    if not entries:
        return "<h2>journal</h2><p class=small>No journal entries yet.</p>"
    items = "".join(
        f"<div class=card style='display:block;text-align:left'>"
        f"<div class=v style='font-size:14px'>{_esc(e.get('title') or e.get('trade_id'))}</div>"
        f"<div class=k>{_esc((e.get('ts') or '')[:10])}</div>"
        f"<p class=small>{_esc(e.get('text'))}</p></div>"
        for e in reversed(entries))
    return f"<h2>journal</h2><div class=cards>{items}</div>"


def render(cfg: dict, ledger, tier: str, email: str,
           gate_open: bool = False, gate_reasons: list | None = None,
           now=None) -> str:
    """The signed-in subscriber dashboard as a self-contained HTML page."""
    feed = content.feed(cfg, ledger, tier, now=now)
    records = ledger.all_records()
    closed = ledger.closed_trades()

    equity_chart = dash.svg_line_chart(dash.equity_series(records),
                                       label="equity", uid="eq")
    pnl_chart = dash.svg_line_chart(dash.realized_pnl_series(records),
                                    label="realized P&L", uid="pl",
                                    zero_area=True)
    trade_bars = dash.svg_trade_bars(closed)

    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Repete — your dashboard</title>"
        f"<style>{dash.CSS}</style></head><body><div class=wrap>"
        f"<h1>Your Repete dashboard</h1>"
        f"<p class=small>Signed in as {_esc(email)} · "
        f"<a class=x href='/account'>account</a> · "
        f"<a class=x href='/feed'>raw feed</a> · "
        f"<a class=x href='/legal/risk'>risk disclosure</a></p>"
        + _cards(feed)
        + _tier_notice(feed, gate_open, gate_reasons or [])
        + "<h2>equity curve</h2>" + equity_chart
        + "<h2>realized P&amp;L over time</h2>" + pnl_chart
        + "<h2>per-trade P&amp;L</h2>" + trade_bars
        + "<h2>monthly scorecard</h2>"
        + _scorecard(feed.get("monthly_scorecard") or {},
                     feed.get("closed_trades", 0))
        + "<h2>decisions</h2>" + _decisions(feed)
        + _journal(feed)
        + f"<hr><p class=small>{_esc(feed.get('disclaimer'))}</p>"
        "</div></body></html>")
