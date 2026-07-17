"""Dashboard generator — one self-contained HTML file from the audit trail.

`python src/dashboard.py` (or automatically at every cycle end) renders
`dashboard.html`: equity curve (from cycle_complete snapshots, with an
optional SPY overlay when bars are provided), open positions, recent
decisions with the judge's reasoning, the lesson book, judge calibration,
measured slippage, and exit reasons. Pure file reads — no network, no JS
libraries, inline everything; open it in any browser.
"""
import html
import json
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger import Ledger
from lessons import LessonStore
from judgments import JudgmentStore, calibration_metrics, calibration_line
import review

OUT_PATH = "dashboard.html"
N_DECISIONS = 30

CSS = """
body{font-family:-apple-system,Segoe UI,sans-serif;margin:0;background:#f5f6f8;
     color:#1c1e21}
.wrap{max-width:1000px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:22px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid
   #d8dbe0;padding-bottom:4px}
.small{color:#5f6673;font-size:12px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.card{background:#fff;border:1px solid #e2e5ea;border-radius:8px;
      padding:10px 14px;min-width:120px}
.card .v{font-size:20px;font-weight:600}.card .k{font-size:11px;color:#5f6673}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px}
th,td{border:1px solid #e2e5ea;padding:6px 8px;text-align:left;
      vertical-align:top}
th{background:#eef0f4;font-size:12px}
a.x{color:#2463eb;text-decoration:none;font-weight:600;font-size:14px}
a.x:hover{text-decoration:underline}
.win{color:#0a7d33}.loss{color:#b3261e}
.badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;
       background:#e8eaf0}
.veto{background:#fbd8d5}.downsize{background:#fdeeC8}.approve{background:#d9f0e0}
svg{background:#fff;border:1px solid #e2e5ea;border-radius:8px}
.reason{color:#434851;font-size:12px}
"""


def _fmt_money(v):
    return f"${v:,.2f}"


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def equity_series(records: list[dict]) -> list[tuple[str, float]]:
    """(ts, equity) from cycle_complete snapshot events (JSON detail)."""
    out = []
    for r in records:
        if r.get("type") == "event" and r.get("event") == "cycle_complete":
            try:
                d = json.loads(r.get("detail") or "")
                out.append((r["ts"], float(d["equity"])))
            except (ValueError, TypeError, KeyError):
                continue  # pre-snapshot records carried no detail
    return out


def realized_pnl_series(records: list[dict]) -> list[tuple[str, float]]:
    """Cumulative realized P&L from outcomes — the fallback history for the
    period before equity snapshots existed."""
    out, cum = [], 0.0
    for r in records:
        if r.get("type") == "outcome":
            cum += r.get("pnl") or 0.0
            out.append((r["ts"], round(cum, 2)))
    return out


def svg_line_chart(series: list[tuple[str, float]], width=940, height=220,
                   overlay: list[tuple[str, float]] | None = None,
                   label="equity", overlay_label="SPY (scaled)") -> str:
    """Inline SVG polyline chart; overlay is min-max scaled onto same axes."""
    if len(series) < 2:
        return ("<p class=small>Not enough data points yet — the curve grows "
                "one point per completed cycle.</p>")
    pad = 42

    def scale(vals):
        lo, hi = min(vals), max(vals)
        rng = (hi - lo) or 1.0
        return lo, rng

    ys = [v for _, v in series]
    lo, rng = scale(ys)

    def pts(vals, vlo, vrng, n):
        step = (width - 2 * pad) / max(n - 1, 1)
        return " ".join(
            f"{pad + i * step:.1f},"
            f"{height - pad - (v - vlo) / vrng * (height - 2 * pad):.1f}"
            for i, v in enumerate(vals))

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet">']
    if overlay and len(overlay) >= 2:
        ovals = [v for _, v in overlay]
        olo, orng = scale(ovals)
        parts.append(f'<polyline fill="none" stroke="#a6adba" '
                     f'stroke-width="1.5" stroke-dasharray="4 3" '
                     f'points="{pts(ovals, olo, orng, len(ovals))}"/>')
        parts.append(f'<text x="{pad}" y="16" font-size="11" '
                     f'fill="#a6adba">{overlay_label}</text>')
    parts.append(f'<polyline fill="none" stroke="#2463eb" stroke-width="2" '
                 f'points="{pts(ys, lo, rng, len(ys))}"/>')
    parts.append(f'<text x="{pad}" y="30" font-size="11" '
                 f'fill="#2463eb">{label}</text>')
    parts.append(f'<text x="{pad}" y="{height - 8}" font-size="10" '
                 f'fill="#5f6673">{series[0][0][:10]}</text>')
    parts.append(f'<text x="{width - pad - 70}" y="{height - 8}" '
                 f'font-size="10" fill="#5f6673">{series[-1][0][:10]}</text>')
    parts.append(f'<text x="{width - pad - 70}" y="16" font-size="11" '
                 f'fill="#1c1e21">{ys[-1]:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _positions_rows(open_trades: dict, now: datetime) -> str:
    rows = []
    for tid, r in open_trades.items():
        age = (now - datetime.fromisoformat(r["ts"])).days
        stop = (r.get("order") or {}).get("stop_price")
        rows.append(
            f"<tr><td>{_esc(r['symbol'])}</td>"
            f"<td>{_esc(r.get('strategy') or 'ma_crossover')}</td>"
            f"<td>{r.get('qty') or ''}</td>"
            f"<td>{_fmt_money(r['entry_price']) if r.get('entry_price') else ''}</td>"
            f"<td>{_fmt_money(stop) if stop else '—'}</td>"
            f"<td>{age}d</td>"
            f"<td class=reason>{_esc(r.get('strategy_reason'))}</td></tr>")
    if not rows:
        return "<p class=small>No open positions.</p>"
    return ("<table><tr><th>Symbol</th><th>Strategy</th><th>Qty</th>"
            "<th>Entry</th><th>Stop</th><th>Age</th><th>Entry reason</th></tr>"
            + "".join(rows) + "</table>")


def _decisions_rows(records: list[dict]) -> str:
    decisions = [r for r in records if r.get("type") == "decision"][-N_DECISIONS:]
    rows = []
    for r in reversed(decisions):
        rev = r.get("llm_review") or {}
        verdict = rev.get("verdict") or ""
        badge = (f'<span class="badge {verdict}">{_esc(verdict)}</span>'
                 if verdict else "")
        status = ("executed" if r.get("executed")
                  else _esc(r.get("detail") or "—"))
        why = _esc(r.get("strategy_reason"))
        judge = _esc((rev.get("reasoning") or "")[:160])
        rows.append(
            f"<tr><td>{_esc(r['ts'][:16].replace('T', ' '))}</td>"
            f"<td>{_esc(r['symbol'])}</td><td>{_esc(r['action'])}</td>"
            f"<td>{_esc(r.get('strategy') or '')}</td>"
            f"<td>{badge}</td><td>{status}</td>"
            f"<td class=reason>{why}"
            + (f"<br><i>judge: {judge}</i>" if judge else "") + "</td></tr>")
    if not rows:
        return "<p class=small>No decisions yet.</p>"
    return ("<table><tr><th>When (UTC)</th><th>Sym</th><th>Action</th>"
            "<th>Strategy</th><th>Judge</th><th>Status</th><th>Reasoning</th>"
            "</tr>" + "".join(rows) + "</table>")


def _lessons_rows(states: dict) -> str:
    live = sorted(states.values(), key=lambda s: s["created_ts"], reverse=True)
    rows = []
    for s in live[:15]:
        rows.append(
            f"<tr><td><span class=badge>{_esc(s['status'])}</span></td>"
            f"<td>{len(s['supports'])}/{len(s['contradicts'])}</td>"
            f"<td>{_esc(s['created_ts'][:10])}</td>"
            f"<td class=reason>{_esc(s['hypothesis'][:180])}</td></tr>")
    if not rows:
        return ("<p class=small>Empty — hypotheses generate from closed "
                "trades.</p>")
    return ("<table><tr><th>Status</th><th>n +/-</th><th>Created</th>"
            "<th>Hypothesis</th></tr>" + "".join(rows) + "</table>")


def render(cfg: dict | None = None, out_path: str = OUT_PATH,
           spy_bars: list[dict] | None = None) -> str:
    if cfg is None:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
    ledger = Ledger(cfg["memory"]["ledger_path"])
    records = ledger.all_records()
    now = datetime.now(timezone.utc)

    try:
        with open(cfg["memory"]["learnings_path"]) as f:
            learnings = f.readlines()
    except OSError:
        learnings = []
    rep = review.build_report(records, learnings, now)
    per_strat = review.per_strategy_breakdown(ledger.closed_trades())
    lcfg = cfg.get("learning", {})
    states = LessonStore(lcfg.get("lessons_path",
                                  "memory/lessons.jsonl")).replay()
    calib = calibration_line(calibration_metrics(
        JudgmentStore(lcfg.get("judgments_path",
                               "memory/judgments.jsonl")).replay()))

    eq = equity_series(records)
    overlay = ([(b["ts"], b["close"]) for b in spy_bars]
               if spy_bars else None)
    if eq:
        chart = svg_line_chart(eq, overlay=overlay, label="equity ($)")
        chart_note = ""
    else:
        chart = svg_line_chart(realized_pnl_series(records),
                               label="cumulative realized P&L ($)")
        chart_note = ("<p class=small>Equity snapshots start with the next "
                      "cycle; showing realized P&L until then.</p>")

    wr = f"{rep['win_rate']:.0%}" if rep["win_rate"] is not None else "n/a"
    pf = ("n/a" if rep["profit_factor"] is None
          else "inf" if rep["profit_factor"] == float("inf")
          else f"{rep['profit_factor']:.2f}")
    slip = rep.get("slippage")
    slip_txt = (f"{slip['median_bps']:+.1f} bps med / {slip['n_fills']} fills"
                if slip else "no fills measured yet")

    cards = "".join(
        f'<div class=card><div class=v>{v}</div><div class=k>{k}</div></div>'
        for k, v in [
            ("days of history", rep["history_days"]),
            ("decisions", rep["n_decisions"]),
            ("closed trades", rep["n_closed"]),
            ("open positions", rep["n_open"]),
            ("win rate", wr),
            ("profit factor", pf),
            ("realized P&L", _fmt_money(rep["realized_pnl"])),
            ("LLM vetoes", rep["n_vetoes"]),
            ("rail rejections", rep["n_risk_rejections"]),
            ("slippage", slip_txt),
        ])

    def _pf_text(v):
        if v is None:
            return "n/a"
        return "inf" if v == float("inf") else f"{v:.2f}"

    strat_rows = "".join(
        f"<tr><td>{_esc(n)}</td><td>{s['n_closed']}</td>"
        f"<td>{s['win_rate']:.0%}</td>"
        f"<td>{_pf_text(s['profit_factor'])}</td>"
        f"<td class={'win' if s['realized_pnl'] >= 0 else 'loss'}>"
        f"{_fmt_money(s['realized_pnl'])}</td></tr>"
        for n, s in sorted(per_strat.items()))
    strat_tbl = (("<table><tr><th>Strategy</th><th>Closed</th><th>Win</th>"
                  "<th>PF</th><th>P&L</th></tr>" + strat_rows + "</table>")
                 if strat_rows else "<p class=small>No closed trades yet.</p>")

    exits = ", ".join(f"{k}: {v}" for k, v in
                      sorted(rep["exit_reasons"].items())) or "none yet"

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>trading-agent dashboard</title><style>{CSS}</style></head><body>
<div class=wrap>
<h1>trading-agent <span class=small>[PAPER] — generated
{now.strftime('%Y-%m-%d %H:%M UTC')}</span>
&nbsp; <a class=x href="https://x.com/Repete2026" target="_blank"
rel="noopener">@Repete2026 on X ↗</a>
&nbsp; <a class=x href="journal.html">trade journal →</a></h1>
<div class=cards>{cards}</div>
<h2>Equity</h2>{chart}{chart_note}
<h2>Open positions</h2>{_positions_rows(ledger.open_buys(), now)}
<h2>Per-strategy</h2>{strat_tbl}
<p class=small>Exits — {_esc(exits)}</p>
<h2>Recent decisions (last {N_DECISIONS})</h2>{_decisions_rows(records)}
<h2>Lesson book</h2>{_lessons_rows(states)}
<p class=small>{_esc(calib)}</p>
<p class=small>Paper trading. Generated from memory/ledger.jsonl — the
append-only audit trail is the source of truth, this page is a view.
The bot narrates its trades and reasoning at
<a class=x href="https://x.com/Repete2026" target="_blank"
rel="noopener">x.com/Repete2026</a>.</p>
</div></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    print(f"wrote {render()}")
