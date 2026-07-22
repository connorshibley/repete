"""Landing page — the site's front door (2026-07-21).

Warm Claude palette (creamy white + orange) in front of the dark terminal:
index.html is this page; the dashboard (with its CRT boot splash) lives one
click deeper at dash.html. Same conventions as dashboard.py: one
self-contained file, inline CSS/SVG, system font stacks, pure ledger reads,
cosmetic only — a render failure never touches trading.
"""
import html
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger import Ledger
import dashboard as dash_mod
import review
import scorecard

OUT_PATH = "landing.html"


def _esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


CSS = """
:root{--cream:#F5F0E8;--cream2:#FAF6EF;--orange:#D97757;--orange2:#C4633F;
      --ink:#3D3929;--ink2:#6B6353;--peach:#F0DCCE;--green:#3E7C4A;
      --red:#B3452F}
*{box-sizing:border-box}
body{margin:0;color:var(--ink);line-height:1.55;
  font-family:-apple-system,Segoe UI,sans-serif;
  background:
    radial-gradient(ellipse 90% 60% at 70% -10%,rgba(217,119,87,.16),transparent 60%),
    radial-gradient(ellipse 70% 50% at 10% 100%,rgba(240,220,206,.7),transparent 55%),
    radial-gradient(circle at 1px 1px,rgba(61,57,41,.06) 1px,transparent 0),
    var(--cream);
  background-size:auto,auto,26px 26px,auto}
.wrap{max-width:960px;margin:0 auto;padding:40px 20px 70px}
.topbar{display:flex;justify-content:space-between;align-items:center;
  font-size:13px;color:var(--ink2)}
.topbar b{letter-spacing:.18em;text-transform:uppercase;font-size:12px}
.topbar a{color:var(--orange2);text-decoration:none;font-weight:600}
.hero{display:flex;gap:36px;align-items:center;flex-wrap:wrap;
  margin:56px 0 30px}
.htext{flex:1 1 380px}
h1{font-family:Georgia,'Times New Roman',serif;font-weight:700;
  font-size:clamp(44px,7.5vw,74px);line-height:1.02;margin:0 0 6px;
  letter-spacing:-.01em}
h1 .stroke{position:relative;white-space:nowrap}
h1 .stroke svg{position:absolute;left:-2%;bottom:-10px;width:104%;height:16px}
.sub{font-size:17.5px;color:var(--ink2);max-width:44ch;margin:18px 0 26px}
.sub b{color:var(--ink)}
.cta{display:inline-block;background:var(--orange);color:#fff;
  text-decoration:none;font-weight:700;font-size:16px;padding:13px 26px;
  border-radius:12px;box-shadow:0 6px 0 var(--orange2);
  transition:transform .12s,box-shadow .12s}
.cta:hover{transform:translateY(2px);box-shadow:0 4px 0 var(--orange2)}
.alt{margin-left:16px;color:var(--orange2);font-weight:600;font-size:14px;
  text-decoration:none}
.alt:hover{text-decoration:underline}
.robotside{flex:0 1 auto;text-align:center;position:relative}
.robot{animation:bob 3.4s ease-in-out infinite}
.robot .eye{transform-origin:center;transform-box:fill-box;
  animation:blink 4.2s infinite}
.robot .tip{animation:pulse 2.2s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-6px)}}
@keyframes blink{0%,92%,100%{transform:scaleY(1)}95%,97%{transform:scaleY(.08)}}
@keyframes pulse{0%,100%{opacity:.5}50%{opacity:1}}
.sticker{position:absolute;right:-14px;top:-8px;background:#fff;
  border:2px solid var(--ink);border-radius:8px;padding:3px 10px;
  font-size:11px;font-weight:800;letter-spacing:.1em;transform:rotate(7deg);
  box-shadow:2px 3px 0 rgba(61,57,41,.25)}
.tape{overflow:hidden;border:2px solid var(--ink);border-radius:12px;
  background:var(--cream2);white-space:nowrap;margin:10px 0 40px;
  box-shadow:3px 4px 0 rgba(61,57,41,.14)}
.tape-track{display:inline-flex;align-items:center;gap:9px;padding:9px 0;
  animation:tapescroll 45s linear infinite;will-change:transform}
.tape:hover .tape-track{animation-play-state:paused}
@keyframes tapescroll{to{transform:translateX(-50%)}}
.tchip{display:inline-flex;gap:5px;padding:4px 13px;border-radius:999px;
  border:1.5px solid var(--peach);background:#fff;font-size:12.5px;
  color:var(--ink2);font-variant-numeric:tabular-nums;flex:0 0 auto}
.tchip b{color:var(--ink);font-weight:700}
.tchip.up b{color:var(--green)}.tchip.dn b{color:var(--red)}
.tchip.hot{border-color:var(--orange);color:var(--orange2)}
.tchip.hot b{color:var(--orange2)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));
  gap:18px;margin:8px 0 44px}
.fcard{background:#fff;border:2px solid var(--ink);border-radius:14px;
  padding:20px 22px;box-shadow:4px 5px 0 rgba(61,57,41,.16);
  transition:transform .15s;text-decoration:none;color:inherit;display:block}
.fcard:hover{transform:translateY(-3px) rotate(-.6deg)}
.fcard .fk{font-size:24px}
.fcard h3{margin:8px 0 6px;font-size:16.5px;font-family:Georgia,serif}
.fcard p{margin:0;font-size:13.5px;color:var(--ink2)}
.fcard .go{display:inline-block;margin-top:10px;color:var(--orange2);
  font-weight:700;font-size:13px}
.foot{font-size:12.5px;color:var(--ink2);border-top:2px dashed var(--peach);
  padding-top:16px}
.foot a{color:var(--orange2);font-weight:600;text-decoration:none}
@media (prefers-reduced-motion: reduce){
  .tape-track,.robot,.robot .eye,.robot .tip{animation:none}}
"""


def _robot_day(total: float) -> str:
    """Repete in daylight — warm colors, same personality."""
    mouth = ('<path d="M24 40 Q31 46 38 40" stroke="#C4633F" stroke-width="2.6"'
             ' fill="none" stroke-linecap="round"/>' if total >= 0 else
             '<path d="M25 42 L37 42" stroke="#C4633F" stroke-width="2.6" '
             'fill="none" stroke-linecap="round"/>')
    return f"""<svg class=robot width="150" height="180" viewBox="0 0 62 74"
 aria-label="Repete the trading robot">
  <line x1="31" y1="12" x2="31" y2="4" stroke="#3D3929" stroke-width="2"/>
  <circle class=tip cx="31" cy="4" r="3.6" fill="#D97757"/>
  <rect x="12" y="12" width="38" height="34" rx="9" fill="#FAF6EF"
        stroke="#3D3929" stroke-width="2.5"/>
  <circle class=eye cx="24" cy="27" r="4.8" fill="#3D3929"/>
  <circle class=eye cx="38" cy="27" r="4.8" fill="#3D3929"/>
  {mouth}
  <rect x="17" y="49" width="28" height="17" rx="6" fill="#F0DCCE"
        stroke="#3D3929" stroke-width="2.5"/>
  <rect x="24" y="54" width="14" height="6" rx="2" fill="#D97757"/>
  <line x1="12" y1="55" x2="6" y2="60" stroke="#3D3929" stroke-width="2.5"
        stroke-linecap="round"/>
  <line x1="50" y1="55" x2="56" y2="60" stroke="#3D3929" stroke-width="2.5"
        stroke-linecap="round"/>
</svg>"""


def _chips(rep: dict, n_open: int, total_pl: float, equity_now,
           n_symbols: int, sm: dict) -> str:
    cls = "up" if total_pl >= 0 else "dn"
    chips = ['<span class="tchip hot">🤖 <b>REPETE</b> · paper trading, '
             'in public</span>',
             f'<span class="tchip {cls}">total P/L '
             f'<b>{dash_mod._fmt_signed(total_pl)}</b></span>']
    if equity_now is not None:
        chips.append(f'<span class=tchip>equity '
                     f'<b>{dash_mod._fmt_money(equity_now)}</b></span>')
    chips.append(f'<span class=tchip><b>{n_open}</b> open position'
                 f'{"s" if n_open != 1 else ""}</span>')
    wr = rep.get("win_rate")
    if wr is not None:
        chips.append(f'<span class=tchip>win rate <b>{wr:.0%}</b></span>')
    chips.append(f'<span class=tchip>judge vetoes <b>{rep.get("n_vetoes", 0)}'
                 f'</b></span>')
    chips.append(f'<span class="tchip hot">scanning <b>{n_symbols} names'
                 f'</b></span>')
    if sm.get("months_total"):
        chips.append(f'<span class=tchip>vs S&amp;P <b>{sm["months_beaten"]}/'
                     f'{sm["months_total"]}</b> months</span>')
    chips.append('<span class=tchip>next decision <b>3:45 PM ET</b> 🔔</span>')
    return "".join(chips)


def render(cfg: dict | None = None, out_path: str = OUT_PATH) -> str:
    if cfg is None:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
    ledger = Ledger(cfg["memory"]["ledger_path"])
    records = ledger.all_records()
    now = datetime.now(timezone.utc)
    rep = review.build_report(records, [], now)
    eq = dash_mod.equity_series(records)
    start = (cfg.get("reporting") or {}).get("starting_equity") or \
        (eq[0][1] if eq else dash_mod.DEFAULT_START_EQUITY)
    equity_now = eq[-1][1] if eq else None
    total_pl = (equity_now - start) if equity_now is not None \
        else rep["realized_pnl"]
    n_open = len(ledger.open_buys())
    n_symbols = len(cfg.get("symbols") or [])
    sm = scorecard.monthly_scorecard(records, [])["summary"]

    chips = _chips(rep, n_open, total_pl, equity_now, n_symbols, sm)
    stroke = ('<svg viewBox="0 0 200 14" preserveAspectRatio="none">'
              '<path d="M3 10 Q60 2 100 8 T197 6" stroke="#D97757" '
              'stroke-width="5" fill="none" stroke-linecap="round" '
              'opacity=".85"/></svg>')

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Repete — a robot that trades in public</title>
<style>{CSS}</style></head><body>
<div class=wrap>
<div class=topbar><b>Repete · autonomous swing desk</b>
<span><a href="journal.html">journal</a> · <a href="blog.html">blog</a> ·
<a href="https://x.com/Repete2026" target="_blank" rel="noopener">@Repete2026
↗</a></span></div>

<div class=hero>
  <div class=htext>
    <h1>Meet <span class=stroke>Repete.{stroke}</span></h1>
    <p class=sub>A robot that swing-trades <b>$100,000 of paper money</b>,
    fully in public. Deterministic strategies pick the trades, an AI judge
    argues the bull <b>and</b> bear case before every decision, and hard-coded
    risk rails have the final word. Every trade, veto and mistake is
    published — <b>nothing is deleted, ever</b>.</p>
    <a class=cta href="dash.html">Open the terminal →</a>
    <a class=alt href="journal.html">read the trade journal</a>
  </div>
  <div class=robotside>{_robot_day(total_pl)}
    <div class=sticker>[PAPER]</div></div>
</div>

<div class=tape><div class=tape-track>{chips}{chips}</div></div>

<div class=cards>
  <a class=fcard href="dash.html">
    <div class=fk>⚖️</div><h3>The judge argues with itself</h3>
    <p>Before any trade, the AI must write the strongest honest case for it
    AND against it — then its verdict is scored against what really
    happened.</p><span class=go>see the decisions →</span></a>
  <a class=fcard href="dash.html">
    <div class=fk>🚧</div><h3>Rails the AI can't override</h3>
    <p>Position caps, correlation caps, drift guards, kill switches — all
    deterministic code that runs after the AI. It can say no; it can never
    say more.</p><span class=go>see the rails at work →</span></a>
  <a class=fcard href="dash.html">
    <div class=fk>📈</div><h3>A public report card vs the S&amp;P</h3>
    <p>Every month, Repete's return is scored against the index and
    published — beaten or not. Hiding bad months is how track records get
    laundered.</p><span class=go>see the scoreboard →</span></a>
</div>

<p class=foot>Paper trading — simulated fills, no real money, not investment
advice. Rebuilt automatically from the bot's append-only ledger after every
trading cycle. <a href="dash.html">dashboard</a> ·
<a href="journal.html">journal</a> · <a href="blog.html">blog</a></p>
</div></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    print(f"wrote {render()}")
