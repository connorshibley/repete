"""Dashboard generator — one self-contained HTML file from the audit trail.

`python src/dashboard.py` (or automatically at every cycle end) renders
`dashboard.html`: a hero total-P/L banner, P/L-over-time and per-trade P/L
charts, the equity curve (optional SPY overlay when bars are provided), open
positions, recent decisions with the judge's reasoning, the lesson book,
judge calibration, measured slippage, and exit reasons. Dark trading-terminal
theme with vanilla inline JS (hover tooltips, count-up, decision filters) —
pure file reads, no network, no external libraries; open it in any browser.
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
DEFAULT_START_EQUITY = 100_000.0

# Chart palette validated for the dark surface (#131722): CVD separation and
# >=3:1 contrast all pass; green/red always ship with $ labels, never alone.
C_LINE = "#3987e5"
C_WIN = "#0ca30c"
C_LOSS = "#d03b3b"
C_MUTED = "#898781"

CSS = """
:root{--bg:#0b0e14;--surf:#131722;--surf2:#1a2130;--line:#232b3b;
      --ink:#e6e9f0;--ink2:#9aa4b5;--mut:#6b7482;--blue:#3987e5;
      --green:#0ca30c;--red:#d03b3b;--amber:#c98500}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,sans-serif;margin:0;background:var(--bg);
     color:var(--ink);line-height:1.5}
.wrap{max-width:1000px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:16px;margin:30px 0 10px;border-bottom:1px solid var(--line);
   padding-bottom:6px}
.small{color:var(--ink2);font-size:12px}
a.x{color:var(--blue);text-decoration:none;font-weight:600;font-size:13px}
a.x:hover{text-decoration:underline}
.hero{background:linear-gradient(160deg,var(--surf) 0%,var(--surf2) 100%);
      border:1px solid var(--line);border-radius:14px;padding:22px 26px;
      margin:18px 0}
.hero .hk{font-size:12px;letter-spacing:.14em;color:var(--ink2);
          text-transform:uppercase}
.hero .hv{font-size:46px;font-weight:700;margin:4px 0 2px;
          font-variant-numeric:tabular-nums}
.hero .hv.win{color:var(--green);text-shadow:0 0 22px rgba(12,163,12,.35)}
.hero .hv.loss{color:var(--red);text-shadow:0 0 22px rgba(208,59,59,.35)}
.hero .hs{color:var(--ink2);font-size:13px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.card{background:var(--surf);border:1px solid var(--line);border-radius:10px;
      padding:10px 14px;min-width:118px;flex:1 1 120px;
      transition:transform .12s,border-color .12s}
.card:hover{transform:translateY(-2px);border-color:var(--blue)}
.card .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
.card .k{font-size:11px;color:var(--ink2)}
.tblwrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;background:var(--surf);font-size:13px}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;
      vertical-align:top}
th{background:var(--surf2);font-size:12px;color:var(--ink2)}
tr:hover td{background:var(--surf2)}
.win{color:var(--green)}.loss{color:var(--red)}
.badge{display:inline-block;padding:1px 7px;border-radius:9px;font-size:11px;
       background:var(--surf2);border:1px solid var(--line)}
.veto{color:var(--red)}.downsize{color:var(--amber)}.approve{color:var(--green)}
svg{background:var(--surf);border:1px solid var(--line);border-radius:10px;
    display:block;margin:8px 0}
.reason{color:var(--ink2);font-size:12px}
details{margin:6px 0}
details>summary{cursor:pointer;color:var(--ink2);font-size:13px;
                user-select:none;margin:6px 0}
.chip{display:inline-block;padding:3px 12px;margin:0 6px 10px 0;cursor:pointer;
      border:1px solid var(--line);border-radius:14px;font-size:12px;
      color:var(--ink2);background:var(--surf)}
.chip:hover{border-color:var(--blue)}
.chip.on{color:var(--ink);border-color:var(--blue);background:var(--surf2)}
#tip{position:absolute;display:none;background:var(--surf2);
     border:1px solid #2e3950;color:var(--ink);padding:6px 10px;
     border-radius:6px;font-size:12px;pointer-events:none;z-index:10;
     box-shadow:0 4px 14px rgba(0,0,0,.5);max-width:280px}
"""

JS = """
(function(){
var tip=document.createElement('div');tip.id='tip';
document.body.appendChild(tip);
function move(e){tip.style.left=(e.pageX+14)+'px';
                 tip.style.top=(e.pageY-12)+'px';}
document.querySelectorAll('[data-tip]').forEach(function(el){
  el.addEventListener('mouseenter',function(e){
    tip.textContent=el.getAttribute('data-tip');
    tip.style.display='block';move(e);});
  el.addEventListener('mousemove',move);
  el.addEventListener('mouseleave',function(){tip.style.display='none';});
});
document.querySelectorAll('[data-count]').forEach(function(el){
  var end=parseFloat(el.getAttribute('data-count'))||0;
  var pre=el.getAttribute('data-prefix')||'';
  function fmt(v){
    var s=Math.abs(v).toLocaleString('en-US',
      {minimumFractionDigits:2,maximumFractionDigits:2});
    return (v<0?'-':'+')+pre+s;}
  var t0=null;
  function step(ts){
    if(!t0)t0=ts;
    var p=Math.min((ts-t0)/900,1),e=1-Math.pow(1-p,3);
    el.textContent=fmt(end*e);
    if(p<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);
});
document.querySelectorAll('.chip').forEach(function(c){
  c.addEventListener('click',function(){
    document.querySelectorAll('.chip').forEach(function(x){
      x.classList.remove('on');});
    c.classList.add('on');
    var f=c.getAttribute('data-f');
    document.querySelectorAll('#dtable tbody tr').forEach(function(r){
      r.style.display=(f==='all'||r.classList.contains(f))?'':'none';});
  });
});
})();
"""


def _fmt_money(v):
    return f"${v:,.2f}"


def _fmt_signed(v):
    return f"{'-' if v < 0 else '+'}${abs(v):,.2f}"


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


def pnl_series(records: list[dict],
               starting_equity: float) -> list[tuple[str, float]]:
    """(ts, equity - starting_equity): total P/L incl. unrealized."""
    return [(ts, round(eq - starting_equity, 2))
            for ts, eq in equity_series(records)]


def _chart_geometry(series, width, height, pad, include_zero=False):
    ys = [v for _, v in series]
    lo, hi = min(ys), max(ys)
    if include_zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    rng = (hi - lo) or 1.0

    def y_of(v):
        return height - pad - (v - lo) / rng * (height - 2 * pad)

    step = (width - 2 * pad) / max(len(series) - 1, 1)
    pts = [(pad + i * step, y_of(v)) for i, (_, v) in enumerate(series)]
    return pts, y_of


def svg_line_chart(series: list[tuple[str, float]], width=940, height=220,
                   overlay: list[tuple[str, float]] | None = None,
                   label="equity", overlay_label="SPY (scaled)",
                   uid="c1", color=C_LINE, zero_area=False) -> str:
    """Inline SVG polyline chart with hover tooltips per point.

    zero_area draws a zero baseline and tints the area green above it /
    red below it (for P/L charts). Overlay is min-max scaled onto the
    same axes."""
    if len(series) < 2:
        return ("<p class=small>Not enough data points yet — the curve grows "
                "one point per completed cycle.</p>")
    pad = 46
    pts, y_of = _chart_geometry(series, width, height, pad,
                                include_zero=zero_area)
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet">']
    if zero_area:
        y0 = y_of(0.0)
        area = (line + f" {pts[-1][0]:.1f},{y0:.1f} {pts[0][0]:.1f},{y0:.1f}")
        parts.append(
            f'<defs>'
            f'<clipPath id="{uid}-above"><rect x="0" y="0" width="{width}" '
            f'height="{y0:.1f}"/></clipPath>'
            f'<clipPath id="{uid}-below"><rect x="0" y="{y0:.1f}" '
            f'width="{width}" height="{height - y0:.1f}"/></clipPath>'
            f'</defs>'
            f'<polygon points="{area}" fill="{C_WIN}" opacity="0.18" '
            f'clip-path="url(#{uid}-above)"/>'
            f'<polygon points="{area}" fill="{C_LOSS}" opacity="0.18" '
            f'clip-path="url(#{uid}-below)"/>'
            f'<line x1="{pad}" y1="{y0:.1f}" x2="{width - pad}" '
            f'y2="{y0:.1f}" stroke="#383f4f" stroke-width="1" '
            f'stroke-dasharray="2 3"/>')
    if overlay and len(overlay) >= 2:
        opts, _ = _chart_geometry(overlay, width, height, pad)
        oline = " ".join(f"{x:.1f},{y:.1f}" for x, y in opts)
        parts.append(f'<polyline fill="none" stroke="{C_MUTED}" '
                     f'stroke-width="1.5" stroke-dasharray="4 3" '
                     f'points="{oline}"/>')
        parts.append(f'<text x="{pad}" y="16" font-size="11" '
                     f'fill="{C_MUTED}">{overlay_label}</text>')
    parts.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" '
                 f'points="{line}"/>')
    # invisible hover targets, one per point (tooltip via shared JS)
    for (x, y), (ts, v) in zip(pts, series):
        tip = f"{ts[:10]} · {_fmt_signed(v) if zero_area else _fmt_money(v)}"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="9" '
                     f'fill="transparent" data-tip="{_esc(tip)}"/>')
    parts.append(f'<text x="{pad}" y="30" font-size="11" '
                 f'fill="{color}">{label}</text>')
    parts.append(f'<text x="{pad}" y="{height - 8}" font-size="10" '
                 f'fill="{C_MUTED}">{series[0][0][:10]}</text>')
    parts.append(f'<text x="{width - pad - 70}" y="{height - 8}" '
                 f'font-size="10" fill="{C_MUTED}">{series[-1][0][:10]}</text>')
    parts.append(f'<text x="{width - pad - 70}" y="16" font-size="11" '
                 f'fill="#e6e9f0">{series[-1][1]:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_trade_bars(closed: list[dict], width=940, height=180) -> str:
    """One green/red bar per closed trade (chronological), hover for detail."""
    if not closed:
        return ("<p class=small>No closed trades yet — one bar appears per "
                "closed trade, green for wins, red for losses.</p>")
    pad = 46
    pnls = [t.get("pnl") or 0.0 for t in closed]
    lo, hi = min(min(pnls), 0.0), max(max(pnls), 0.0)
    rng = (hi - lo) or 1.0

    def y_of(v):
        return height - pad - (v - lo) / rng * (height - 2 * pad)

    y0 = y_of(0.0)
    plot_w = width - 2 * pad
    slot = plot_w / len(pnls)
    bar_w = max(min(slot - 2, 40), 2)  # 2px surface gap between bars

    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" '
             f'preserveAspectRatio="xMidYMid meet">',
             f'<line x1="{pad}" y1="{y0:.1f}" x2="{width - pad}" '
             f'y2="{y0:.1f}" stroke="#383f4f" stroke-width="1"/>']
    for i, t in enumerate(closed):
        pnl = t.get("pnl") or 0.0
        x = pad + i * slot + (slot - bar_w) / 2
        y_top, y_bot = sorted((y_of(pnl), y0))
        h = max(abs(y_top - y_bot), 1.5)
        cls = "win" if pnl > 0 else "loss"
        fill = C_WIN if pnl > 0 else C_LOSS
        tip = (f"{t.get('symbol', '?')} · {_fmt_signed(pnl)} "
               f"({t.get('pnl_pct', 0):+.2f}%) · "
               f"{t.get('exit_reason') or 'exit'}")
        parts.append(f'<rect class="{cls}" x="{x:.1f}" y="{y_top:.1f}" '
                     f'width="{bar_w:.1f}" height="{h:.1f}" rx="2" '
                     f'fill="{fill}" data-tip="{_esc(tip)}"/>')
    parts.append(f'<text x="{pad}" y="16" font-size="11" '
                 f'fill="{C_MUTED}">P/L per closed trade ($)</text>')
    parts.append("</svg>")
    return "".join(parts)


def _hero(total: float, start: float, equity_now: float | None,
          realized_only: bool) -> str:
    pct = total / start * 100 if start else 0.0
    cls = "win" if total >= 0 else "loss"
    sub = (f"{pct:+.2f}% on {_fmt_money(start)} starting capital"
           + (f" · account equity {_fmt_money(equity_now)}"
              if equity_now is not None else "")
           + (" · realized only (equity snapshots start next cycle)"
              if realized_only else ""))
    return (f'<div class=hero><div class=hk>Total P/L — paper account</div>'
            f'<div class="hv {cls}" data-count="{total:.2f}" '
            f'data-prefix="$">{_fmt_signed(total)}</div>'
            f'<div class=hs>{_esc(sub)}</div></div>')


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
    return ("<div class=tblwrap><table><tr><th>Symbol</th><th>Strategy</th>"
            "<th>Qty</th><th>Entry</th><th>Stop</th><th>Age</th>"
            "<th>Entry reason</th></tr>" + "".join(rows) + "</table></div>")


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
        bull = _esc((rev.get("bull_case") or "")[:160])
        bear = _esc((rev.get("bear_case") or "")[:160])
        debate = ((f"<br><i>bull: {bull}</i>" if bull else "")
                  + (f"<br><i>bear: {bear}</i>" if bear else ""))
        classes = ["r-exec" if r.get("executed") else "r-skip"]
        if verdict:
            classes.append(f"r-{verdict}")
        rows.append(
            f'<tr class="{" ".join(classes)}">'
            f"<td>{_esc(r['ts'][:16].replace('T', ' '))}</td>"
            f"<td>{_esc(r['symbol'])}</td><td>{_esc(r['action'])}</td>"
            f"<td>{_esc(r.get('strategy') or '')}</td>"
            f"<td>{badge}</td><td>{status}</td>"
            f"<td class=reason>{why}" + debate
            + (f"<br><i>judge: {judge}</i>" if judge else "") + "</td></tr>")
    if not rows:
        return "<p class=small>No decisions yet.</p>"
    chips = "".join(
        f'<span class="chip{" on" if f == "all" else ""}" data-f="{f}">'
        f'{label}</span>'
        for f, label in [("all", "All"), ("r-exec", "Executed"),
                         ("r-approve", "Approved"),
                         ("r-downsize", "Downsized"), ("r-veto", "Vetoed"),
                         ("r-skip", "Skipped")])
    return (chips + '<div class=tblwrap><table id=dtable>'
            "<thead><tr><th>When (UTC)</th><th>Sym</th><th>Action</th>"
            "<th>Strategy</th><th>Judge</th><th>Status</th><th>Reasoning</th>"
            "</tr></thead><tbody>" + "".join(rows) +
            "</tbody></table></div>")


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
    return ("<div class=tblwrap><table><tr><th>Status</th><th>n +/-</th>"
            "<th>Created</th><th>Hypothesis</th></tr>"
            + "".join(rows) + "</table></div>")


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
    start = (cfg.get("reporting") or {}).get("starting_equity")
    if start is None:
        start = eq[0][1] if eq else DEFAULT_START_EQUITY
    if eq:
        equity_now = eq[-1][1]
        total_pl = equity_now - start
        realized_only = False
    else:
        equity_now = None
        total_pl = rep["realized_pnl"]
        realized_only = True

    overlay = ([(b["ts"], b["close"]) for b in spy_bars]
               if spy_bars else None)
    pl = pnl_series(records, start)
    if len(pl) >= 2:
        pl_chart = svg_line_chart(pl, label="total P/L ($)", uid="pl",
                                  zero_area=True)
    else:
        rl = realized_pnl_series(records)
        pl_chart = (svg_line_chart(rl, label="cumulative realized P&L ($)",
                                   uid="pl", zero_area=True)
                    if len(rl) >= 2 else
                    "<p class=small>The P/L curve appears after two cycles "
                    "of history — one point per completed cycle.</p>")
    if eq:
        eq_chart = svg_line_chart(eq, overlay=overlay, label="equity ($)",
                                  uid="eq")
        chart_note = ""
    else:
        eq_chart = svg_line_chart(realized_pnl_series(records),
                                  label="cumulative realized P&L ($)",
                                  uid="eq")
        chart_note = ("<p class=small>Equity snapshots start with the next "
                      "cycle; showing realized P&L until then.</p>")
    bars = svg_trade_bars(ledger.closed_trades())

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
    strat_tbl = (("<div class=tblwrap><table><tr><th>Strategy</th>"
                  "<th>Closed</th><th>Win</th><th>PF</th><th>P&L</th></tr>"
                  + strat_rows + "</table></div>")
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
&nbsp; <a class=x href="journal.html">trade journal →</a>
&nbsp; <a class=x href="blog.html">blog →</a></h1>
{_hero(total_pl, start, equity_now, realized_only)}
<div class=cards>{cards}</div>
<h2>📈 P/L over time</h2>{pl_chart}
<h2>🪙 Trade scoreboard</h2>{bars}
<h2>💰 Equity</h2>{eq_chart}{chart_note}
<h2>💼 Open positions</h2>{_positions_rows(ledger.open_buys(), now)}
<h2>🧭 Per-strategy</h2>{strat_tbl}
<p class=small>Exits — {_esc(exits)}</p>
<h2>⚖️ Recent decisions (last {N_DECISIONS})</h2>
<details open><summary>every signal, the judge's verdict, and what
happened — filter with the chips</summary>
{_decisions_rows(records)}</details>
<h2>🧠 Lesson book</h2>
<details open><summary>falsifiable hypotheses the bot is testing from its
own closed trades</summary>{_lessons_rows(states)}</details>
<p class=small>{_esc(calib)}</p>
<p class=small>Paper trading. Generated from memory/ledger.jsonl — the
append-only audit trail is the source of truth, this page is a view.
The bot narrates its trades and reasoning at
<a class=x href="https://x.com/Repete2026" target="_blank"
rel="noopener">x.com/Repete2026</a>.</p>
</div><script>{JS}</script></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    print(f"wrote {render()}")
