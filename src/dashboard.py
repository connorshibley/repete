"""Dashboard generator — one self-contained HTML file from the audit trail.

`python src/dashboard.py` (or automatically at every cycle end) renders
`dashboard.html`: a hero total-P/L banner, P/L-over-time and per-trade P/L
charts, the equity curve (optional SPY overlay when bars are provided), open
positions, recent decisions with the judge's reasoning, the lesson book,
judge calibration, measured slippage, and exit reasons. Dark trading-terminal
theme with vanilla inline JS (hover tooltips, count-up, decision filters) —
pure file reads, no network, no external libraries; open it in any browser.
"""
import base64
import hashlib
import html
import json
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ledger import Ledger
from lessons import LessonStore
from judgments import JudgmentStore, calibration_metrics, calibration_line
import disclaimer
import review
import sitepaths

OUT_PATH = "dashboard.html"
DATA_PATH = "dashboard_data.json"
N_DECISIONS = 30
DEFAULT_START_EQUITY = 100_000.0

# Ratio statistics below this many closed trades render as "not yet meaningful"
# instead of as a number.
#
# On 2026-07-26 this page showed `profit factor: inf` and `win rate: 100%` off
# ONE closed trade. Both were arithmetically correct and both invited exactly
# the wrong conclusion — anyone glancing at the page would read a flawless bot.
# A figure a viewer cannot help but misread is a reporting defect, not a
# rounding preference.
#
# 10 sits below `risk.live_kill.min_trades` (15) deliberately: that value gates
# a RAIL that stops trading and is conservative for that reason. This one gates
# only what a human is shown.
MIN_CLOSED_FOR_RATIOS = 10

# Freshness badge thresholds. Display only — nothing here can block a trade.
STALE_AMBER_HOURS = 8
STALE_RED_HOURS = 24

# ---- palette -------------------------------------------------------------
#
# LIGHT SURFACE (#ffffff), 2026-07-26. These are baked into SVG fill/stroke
# attributes, so unlike everything in CSS they do NOT follow a variable —
# miss them and the charts stay dark-theme on a white page.
#
# The previous values were chosen for #131722 and do not survive the move.
# Measured against white: green #0ca30c fell to 3.35:1 and red #d03b3b sat at
# 4.80:1, which made PROFIT less legible than LOSS — backwards, on a page
# whose entire job is showing which one you have. Every colour below clears
# 4.5:1 on white and is pinned by tests/test_dashboard_contrast.py, so the
# claim in this comment can no longer quietly expire the way the old one did.
#
# CVD discipline is unchanged and is not decoration: red/green is the most
# common colour-vision confusion, and this page is public. Every signed figure
# ships with its sign and its $, so colour is reinforcement, never the only
# channel carrying the meaning.
SURFACE = "#ffffff"          # what these are drawn on; the contrast test's base
C_LINE = "#1d5fbf"           # 6.10:1
C_WIN = "#087a08"            # 5.53:1
C_LOSS = "#93150f"           # 8.92:1
C_MUTED = "#5c6673"          # 5.83:1
C_GRID = "#dfe3ea"           # gridlines — decorative, deliberately below text
C_ZERO = "#94a0b0"           # zero baseline, must read against gridlines
C_AXIS = "#3d4652"           # axis value labels — 9.56:1

CSS = """
/* Light surface, 2026-07-26. Every ink/accent below clears 4.5:1 on white and
   is pinned by tests/test_dashboard_contrast.py. --line and --grid are
   structural, not text, and are deliberately lighter. */
:root{--bg:#ffffff;--surf:#f7f8fa;--surf2:#eef1f5;--line:#dfe3ea;
      --ink:#141a22;--ink2:#3d4652;--mut:#5c6673;--blue:#1d5fbf;
      --green:#087a08;--red:#93150f;--amber:#8a5a00;
      --cyan:#0e7490;--violet:#6d28d9;--pink:#be185d}
*{box-sizing:border-box}
body{font-family:-apple-system,Segoe UI,sans-serif;margin:0;background:var(--bg);
     color:var(--ink);line-height:1.5}
.wrap{max-width:1000px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:21px;margin:0 0 4px}
h2{font-size:16px;margin:30px 0 10px;border-bottom:1px solid var(--line);
   padding-bottom:6px;position:relative}
h2:after{content:"";position:absolute;left:0;bottom:-1px;width:64px;height:2px;
   border-radius:2px;background:linear-gradient(90deg,var(--cyan),var(--violet))}
.small{color:var(--ink2);font-size:12px}
a.x{color:var(--blue);text-decoration:none;font-weight:600;font-size:13px}
a.x:hover{text-decoration:underline}
.hero{background:linear-gradient(160deg,var(--surf) 0%,var(--surf2) 78%,
      rgba(139,92,246,.12) 100%);
      border:1px solid var(--line);border-radius:14px;padding:22px 26px;
      margin:18px 0;display:flex;align-items:center;gap:22px;flex-wrap:wrap}
.hero .htext{flex:1 1 260px}
.robotbox{display:flex;align-items:center;gap:10px;flex:0 1 auto}
/* Repete on the swing. 240px is exactly half the 480px asset, so it lands on
   whole device pixels at 2x. The pivot sits above the frame because that is
   where the chains converge -- rotating about the image's own centre reads as
   a wobble, not a swing. */
.swing{width:240px;max-width:40vw;height:auto;display:block;
       transform-origin:50% -22%;will-change:transform;
       animation:swingarc 4.6s ease-in-out infinite}
@keyframes swingarc{0%,100%{transform:rotate(-3.5deg)}
                    50%{transform:rotate(3.5deg)}}
/* Losing book: same Repete, same swing, no colour. Stops short of full
   grayscale so he reads as muted rather than as a broken image. */
.swing.flat{filter:grayscale(.85) opacity(.92)}
.robot{animation:bob 3.4s ease-in-out infinite}
.robot .eye{transform-origin:center;transform-box:fill-box;
            animation:blink 4.2s infinite}
.robot .tip{animation:tippulse 2.2s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-4px)}}
@keyframes blink{0%,92%,100%{transform:scaleY(1)}95%,97%{transform:scaleY(.08)}}
@keyframes tippulse{0%,100%{opacity:.55}50%{opacity:1}}
/* White, not --surf2. The bubble used to sit beside a 62px robot, well inside
   the light end of the hero gradient. The swing illustration pushes it to the
   far right, where the gradient IS --surf2 -- same fill on same fill, and the
   bubble lost its shape entirely. White reads at every point along it. */
.bubble{position:relative;background:#ffffff;border:1px solid var(--line);
        border-radius:12px;padding:8px 12px;font-size:12.5px;color:var(--ink2);
        max-width:210px;min-height:38px;display:flex;align-items:center;
        transition:opacity .45s}
.bubble:before{content:"";position:absolute;left:-7px;top:50%;margin-top:-6px;
        border:6px solid transparent;border-right-color:var(--line)}
/* Narrow viewports: the illustration and a 210px bubble side by side need
   ~430px, which pushed the bubble off the right edge of a phone. Stack them
   and drop the tail, which points at nothing once the bubble is underneath. */
@media (max-width:620px){
  .robotbox{flex-wrap:wrap;justify-content:center}
  .swing{width:200px;max-width:70vw}
  .bubble{max-width:none;flex:1 1 100%}
  .bubble:before{display:none}}
.tape{overflow:hidden;border:1px solid var(--line);border-radius:10px;
      background:var(--surf);margin:14px 0;white-space:nowrap;position:relative}
.tape:before,.tape:after{content:"";position:absolute;top:0;bottom:0;width:26px;
      z-index:2;pointer-events:none}
.tape:before{left:0;background:linear-gradient(90deg,var(--bg),transparent)}
.tape:after{right:0;background:linear-gradient(270deg,var(--bg),transparent)}
.tape-track{display:inline-flex;align-items:center;gap:8px;padding:8px 0;
      animation:tapescroll 45s linear infinite;will-change:transform}
.tape:hover .tape-track{animation-play-state:paused}
@keyframes tapescroll{to{transform:translateX(-50%)}}
.tchip{display:inline-flex;align-items:center;gap:5px;padding:3px 11px;
      border-radius:12px;border:1px solid var(--line);background:var(--surf2);
      font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums;
      flex:0 0 auto}
.tchip b{color:var(--ink);font-weight:600}
.tchip.up{border-color:rgba(8,122,8,.55)}.tchip.up b{color:var(--green)}
.tchip.dn{border-color:rgba(208,59,59,.5)}.tchip.dn b{color:var(--red)}
.tchip.bot{border-color:var(--violet);color:var(--violet)}
.tchip.fun{border-color:rgba(34,211,238,.45)}.tchip.fun b{color:var(--cyan)}
.livedot{display:inline-block;width:8px;height:8px;border-radius:50%;
      background:var(--green);margin-right:5px;vertical-align:1px;
      animation:tippulse 1.6s ease-in-out infinite;
      box-shadow:0 0 8px rgba(12,163,12,.7)}
/* Stat cards. Colour is SEMANTIC only — green/red mean signed money and
   nothing else. The previous 4-colour repeating border encoded position in
   the list, which reads as meaning and is not. */
.cards .card{border-top:2px solid var(--line)}
.cardgroup{margin:14px 0 4px}
.cardgroup h3{font-size:11px;letter-spacing:.09em;text-transform:uppercase;
      color:var(--ink2);font-weight:600;margin:0 0 6px;padding-bottom:4px;
      border-bottom:1px solid var(--line)}
.card.win{border-top-color:var(--green)}
.card.loss{border-top-color:var(--red)}
.card.big{min-width:150px}
.card.big .v{font-size:25px}
/* Ratio with too small a sample: shown, but visibly not load-bearing. */
.card.pending{border-top-color:var(--line);opacity:.72}
.pendingv{color:var(--ink2)}
.pendingn{display:block;font-size:10px;color:var(--ink2);
      font-variant-numeric:tabular-nums;margin-top:1px}
td.pending .pendingn{display:inline;margin-left:6px}
.tinylist{list-style:none;padding:0;margin:6px 0 0;display:flex;
      flex-wrap:wrap;gap:8px}
.tinylist li{background:var(--surf);border:1px solid var(--line);
      border-radius:8px;padding:5px 10px;font-size:12px;
      font-variant-numeric:tabular-nums}
/* Collapsed run of quiet holds — present, findable, not shouting. */
.holdrun td{color:var(--ink2);font-size:12px;background:var(--surf)}
.holdsyms{color:var(--mut);font-size:11px;margin-left:6px}
/* Freshness badge. Amber/red are the whole point — a dashboard that looks
   identical whether it is 2 minutes or 2 days old is worse than none, because
   it invites decisions on data that silently stopped arriving. */
.fresh{display:inline-block;font-size:10px;letter-spacing:.04em;
      padding:2px 8px;border-radius:999px;border:1px solid var(--line);
      color:var(--ink2);font-variant-numeric:tabular-nums;vertical-align:1px}
.fresh.green{border-color:rgba(8,122,8,.55);color:var(--green)}
.fresh.amber{border-color:rgba(138,90,0,.6);color:var(--amber)}
.fresh.red{border-color:rgba(147,21,15,.6);color:var(--red);
      background:rgba(147,21,15,.07)}
#boot{position:fixed;inset:0;z-index:50;display:none;cursor:pointer;
  flex-direction:column;align-items:center;justify-content:center;gap:0;
  background:
    radial-gradient(ellipse 120% 90% at 50% 30%,rgba(34,211,238,.07),transparent 55%),
    repeating-linear-gradient(0deg,transparent 0 39px,rgba(57,135,229,.05) 39px 40px),
    repeating-linear-gradient(90deg,transparent 0 39px,rgba(57,135,229,.05) 39px 40px),
    #05070c;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  transition:opacity .55s,transform .55s}
#boot.show{display:flex}
#boot.bye{opacity:0;transform:translateY(-3vh);pointer-events:none}
#boot:after{content:"";position:absolute;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.22) 0 1px,
  transparent 1px 3px);mix-blend-mode:multiply}
#boot:before{content:"";position:absolute;inset:0;pointer-events:none;
  background:radial-gradient(ellipse at center,transparent 55%,rgba(0,0,0,.55))}
#boot .robot{transform:scale(2);margin-bottom:34px;filter:
  drop-shadow(0 0 18px rgba(139,92,246,.35))}
#boot.show .robot .eye{animation:eyeson 1s .3s both, blink 4.2s 2s infinite}
@keyframes eyeson{0%{fill:#12314a}55%{fill:#12314a}75%{fill:#22d3ee}
  85%{fill:#0b6a80}100%{fill:#22d3ee}}
#boot .sys{font-size:10px;letter-spacing:.5em;color:#8e9bb0;
  text-transform:uppercase;margin-bottom:10px}
#boot .mark{display:flex;gap:.14em;margin-bottom:6px}
#boot .mark b{font-size:clamp(34px,7vw,64px);font-weight:800;line-height:1;
  background:linear-gradient(180deg,#a7f3d0 0%,#22d3ee 55%,#0ca30c 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent;
  opacity:0;transform:translateY(14px);
  text-shadow:0 0 28px rgba(34,211,238,.25)}
#boot.show .mark b{animation:markup .5s cubic-bezier(.2,.9,.3,1.3) forwards,
  crt 5s 1.4s infinite}
@keyframes markup{to{opacity:1;transform:none}}
@keyframes crt{0%,96.5%,100%{opacity:1}97%{opacity:.72}97.6%{opacity:1}
  98.2%{opacity:.85}98.8%{opacity:1}}
#boot .tagline{font-size:11px;letter-spacing:.34em;color:var(--green);
  text-transform:uppercase;margin-bottom:28px;
  text-shadow:0 0 12px rgba(12,163,12,.6)}
#boot .log{width:min(420px,86vw);text-align:left;margin-bottom:26px}
#boot .bl{font-size:12.5px;color:var(--ink2);opacity:0;min-height:19px;
  transform:translateX(-6px);transition:opacity .22s,transform .22s;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#boot .bl:before{content:"▸ ";color:var(--violet)}
#boot .bl.on{opacity:1;transform:none}
#boot .bl.on.cur:after{content:"█";color:var(--green);
  animation:cursorblink .7s steps(1) infinite;margin-left:2px}
@keyframes cursorblink{50%{opacity:0}}
#boot .bl b{color:var(--cyan);font-weight:600}
#boot .bl .ok{color:var(--green)}
#candles{display:flex;align-items:flex-end;gap:5px;height:34px;
  margin-bottom:10px}
#candles i{width:7px;border-radius:2px;transform-origin:bottom;
  transform:scaleY(0)}
#candles i.g{background:var(--green);box-shadow:0 0 8px rgba(8,122,8,.55)}
#candles i.r{background:var(--red);box-shadow:0 0 8px rgba(208,59,59,.4)}
#boot.show #candles i{animation:candlepop .3s cubic-bezier(.2,.8,.3,1.4) forwards}
@keyframes candlepop{to{transform:scaleY(1)}}
#boot .skip{font-size:10px;letter-spacing:.22em;color:var(--mut);
  text-transform:uppercase}
@media (prefers-reduced-motion: reduce){
  .tape-track,.robot,.robot .eye,.robot .tip,.livedot,.swing,
  #boot .mark b,#candles i,#boot .bl.on.cur:after{animation:none}}
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
tr.totrow td{background:var(--surf2);border-top:2px solid var(--line)}
tr.totrow:hover td{background:var(--surf2)}
.warn{color:var(--amber)}
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
     border:1px solid var(--line);color:var(--ink);padding:6px 10px;
     border-radius:6px;font-size:12px;pointer-events:none;z-index:10;
     box-shadow:0 4px 14px rgba(20,26,34,.18);max-width:280px}
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
var boot=document.getElementById('boot');
if(boot){
  var played=false;
  try{played=sessionStorage.getItem('repete_boot')==='1';}catch(e){}
  var noMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(played||noMotion){boot.remove();}
  else{
    try{sessionStorage.setItem('repete_boot','1');}catch(e){}
    boot.classList.add('show');
    var bls=boot.querySelectorAll('.bl');
    bls.forEach(function(el,i){
      setTimeout(function(){
        el.classList.add('on');
        bls.forEach(function(x){x.classList.remove('cur');});
        el.classList.add('cur');
      },700+i*330);});
    function bye(){boot.classList.add('bye');
      setTimeout(function(){boot.remove();},580);}
    var t=setTimeout(bye,3400);
    boot.addEventListener('click',function(){clearTimeout(t);bye();});
  }
}
var bl=document.getElementById('bubble'),
    src=document.getElementById('replines');
if(bl&&src&&!window.matchMedia('(prefers-reduced-motion: reduce)').matches){
  try{
    var lines=JSON.parse(src.textContent),i=0;
    setInterval(function(){
      bl.style.opacity='0';
      setTimeout(function(){
        i=(i+1)%lines.length;bl.textContent=lines[i];
        bl.style.opacity='1';},450);
    },6000);
  }catch(e){}
}
})();
"""

# Self-refresh. The page polls its own data file and swaps the volatile regions
# in place, so a decision landing mid-session appears without a reload and
# without losing your scroll position.
#
# Replaces a <meta http-equiv=refresh content=300>, which hard-reloaded the
# whole document every five minutes — it did keep the page current, but it
# threw away scroll position and any open filter, so reading a long decisions
# table meant racing the timer.
#
# The freshness badge is computed from the generation stamp on EVERY tick, not
# only on a successful fetch. That is the important part: if polling breaks,
# the badge still goes amber and then red on schedule. A staleness indicator
# that depends on the update path working is exactly the control that fails
# silently when you need it.
LIVE_JS = """
(function(){
var badge=document.getElementById('fresh');
if(!badge)return;
var genAt=new Date(badge.getAttribute('data-gen'));
var hash=badge.getAttribute('data-hash');
var AMBER=%(amber)d, RED=%(red)d, canPoll=location.protocol!=='file:';

function paint(note){
  var hrs=(Date.now()-genAt.getTime())/3600000;
  var cls=hrs>=RED?'red':hrs>=AMBER?'amber':'green';
  var age=hrs<1?Math.max(0,Math.round(hrs*60))+'m':Math.round(hrs)+'h';
  badge.className='fresh '+cls;
  badge.textContent=(cls==='green'?'live · ':'stale · ')+age+' old'+
                    (note?' · '+note:'');
}
paint(canPoll?'':'auto-update unavailable — opened as a local file');
setInterval(function(){paint(canPoll?'':
            'auto-update unavailable — opened as a local file');},30000);
if(!canPoll)return;

function swap(d){
  Object.keys(d.regions||{}).forEach(function(k){
    var el=document.getElementById('rgn-'+k);
    if(el&&el.innerHTML!==d.regions[k])el.innerHTML=d.regions[k];
  });
  hash=d.hash; genAt=new Date(d.generated_at);
  badge.setAttribute('data-gen',d.generated_at);
  paint('updated just now');
}
function poll(){
  fetch('%(data)s',{cache:'no-store'}).then(function(r){
    if(!r.ok)throw 0; return r.json();
  }).then(function(d){
    if(d&&d.hash&&d.hash!==hash)swap(d); else paint('');
  }).catch(function(){ paint('update check failed'); });
}
setInterval(poll,60000);
})();
"""


def ratio_is_meaningful(n_closed: int | None,
                        min_n: int = MIN_CLOSED_FOR_RATIOS) -> bool:
    """Is there enough closed history for a win rate / profit factor to mean
    anything? Pure and separately testable — see MIN_CLOSED_FOR_RATIOS."""
    return bool(n_closed) and n_closed >= min_n


def _ratio_cell(text: str, n_closed: int | None) -> tuple[str, str]:
    """(display_text, css_tone) for a ratio statistic.

    Returns the number when the sample supports it, and an explicit
    'n=N · not yet meaningful' otherwise. It deliberately still shows N —
    hiding the sample size entirely would trade one kind of opacity for
    another.
    """
    if ratio_is_meaningful(n_closed):
        return text, ""
    return (f"<span class=pendingv>{text}</span>"
            f"<span class=pendingn>n={n_closed or 0} · not yet meaningful</span>",
            "pending")


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
    y_of.lo, y_of.hi = lo, hi          # exposed so callers can label the axis
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

    # Gridlines + value labels. Without these a line has no frame of reference:
    # the P/L series was entirely negative and read as "a shape" rather than a
    # quantity. Drawn first so the data line sits on top of them.
    lo, hi = y_of.lo, y_of.hi
    for frac in (0.0, 0.5, 1.0):
        v = lo + (hi - lo) * frac
        gy = y_of(v)
        parts.append(f'<line x1="{pad}" y1="{gy:.1f}" x2="{width - pad}" '
                     f'y2="{gy:.1f}" stroke="{C_GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{pad - 6}" y="{gy + 3:.1f}" font-size="10" '
                     f'text-anchor="end" fill="{C_MUTED}">'
                     f'{v:,.0f}</text>')
    # A zero line whenever zero is actually inside the plotted range, not only
    # on zero_area charts — an equity curve that crosses its starting capital
    # needs the same reference the P/L curve gets.
    if not zero_area and lo < 0 < hi:
        parts.append(f'<line x1="{pad}" y1="{y_of(0.0):.1f}" '
                     f'x2="{width - pad}" y2="{y_of(0.0):.1f}" '
                     f'stroke="{C_ZERO}" stroke-width="1" '
                     f'stroke-dasharray="2 3"/>')
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
            f'y2="{y0:.1f}" stroke="{C_ZERO}" stroke-width="1" '
            f'stroke-dasharray="2 3"/>'
            f'<text x="{width - pad + 4}" y="{y0 + 3:.1f}" font-size="10" '
            f'fill="{C_MUTED}">0</text>')
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
                 f'fill="{C_AXIS}">{series[-1][1]:,.0f}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_trade_bars(closed: list[dict], width=940, height=180) -> str:
    """One green/red bar per closed trade (chronological), hover for detail."""
    if not closed:
        return ("<p class=small>No closed trades yet — one bar appears per "
                "closed trade, green for wins, red for losses.</p>")
    # A chart is a comparison. Below a handful of trades there is nothing to
    # compare, and a lone bar in a wide empty field reads as a broken render
    # rather than as a small sample. Say it in words until the shape means
    # something. Same threshold family as MIN_CLOSED_FOR_RATIOS.
    if len(closed) < 5:
        rows = "".join(
            f"<li>{_esc(t.get('symbol', '?'))} "
            f"<b class={'win' if (t.get('pnl') or 0) >= 0 else 'loss'}>"
            f"{_fmt_signed(t.get('pnl') or 0.0)}</b></li>"
            for t in closed)
        return (f"<p class=small>{len(closed)} closed trade"
                f"{'s' if len(closed) != 1 else ''} so far — too few for the "
                f"distribution to have a shape, so here they are individually. "
                f"The chart appears at 5.</p><ul class=tinylist>{rows}</ul>")
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
             f'y2="{y0:.1f}" stroke="{C_ZERO}" stroke-width="1"/>']
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


def _robot(total: float) -> str:
    """Repete, the resident robot (inline SVG, no assets). Mood is real:
    he smiles when total P/L >= 0 and puts on his determined face when not."""
    mouth = ('<path id=mouth-smile d="M24 40 Q31 46 38 40" stroke="#22d3ee" '
             'stroke-width="2.4" fill="none" stroke-linecap="round"/>'
             if total >= 0 else
             '<path id=mouth-flat d="M25 42 L37 42" stroke="#c98500" '
             'stroke-width="2.4" fill="none" stroke-linecap="round"/>')
    return f"""<svg class=robot width="62" height="74" viewBox="0 0 62 74"
 style="background:none;border:none" aria-label="Repete the trading robot">
  <line x1="31" y1="12" x2="31" y2="4" stroke="#8b5cf6" stroke-width="2"/>
  <circle class=tip cx="31" cy="4" r="3.4" fill="#ec4899"/>
  <rect x="12" y="12" width="38" height="34" rx="9" fill="#1a2130"
        stroke="#8b5cf6" stroke-width="2"/>
  <circle class=eye cx="24" cy="27" r="4.6" fill="#22d3ee"/>
  <circle class=eye cx="38" cy="27" r="4.6" fill="#22d3ee"/>
  {mouth}
  <rect x="17" y="49" width="28" height="17" rx="6" fill="#1a2130"
        stroke="#3987e5" stroke-width="2"/>
  <rect x="24" y="54" width="14" height="6" rx="2" fill="#0ca30c" opacity=".8"/>
  <line x1="12" y1="55" x2="6" y2="60" stroke="#3987e5" stroke-width="2"
        stroke-linecap="round"/>
  <line x1="50" y1="55" x2="56" y2="60" stroke="#3987e5" stroke-width="2"
        stroke-linecap="round"/>
</svg>"""


SWING_ASSET = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "assets", "repete_swing.webp")
_SWING_CACHE: str | None = None


def _swing_data_uri() -> str | None:
    """The swing illustration as a base64 data URI, or None if it is missing.

    Inlined rather than linked on purpose. scripts/publish_dashboard.sh copies
    four hardcoded named files into .site/ -- no glob, no directory copy -- so a
    linked asset could ship one commit behind its HTML and render as a broken
    image on the live site. That is the same failure the sidecar comment in
    that script warns about. A data URI cannot desynchronise from its page.

    Read once and cached: render() is called on every cycle and this is 40 KB
    of base64 that never changes between builds.
    """
    global _SWING_CACHE
    if _SWING_CACHE is None:
        try:
            with open(SWING_ASSET, "rb") as fh:
                _SWING_CACHE = base64.b64encode(fh.read()).decode("ascii")
        except OSError as e:                          # noqa: BLE001
            print(f"dashboard: swing asset unavailable ({e}) -- "
                  f"falling back to the SVG robot", file=sys.stderr)
            return None
    return f"data:image/webp;base64,{_SWING_CACHE}"


def _swing(total: float) -> str:
    """Repete swinging -- the landing-page hero, on every day.

    The illustration has one expression, and a bot grinning above a red number
    would be the page flattering itself. This first shipped by handing losing
    days back to _robot()'s determined face -- correct, but it meant the hero
    art was simply absent on an ordinary down day, which is most of them early
    on. Draining the colour out of him says the same thing and keeps the page
    looking like itself: `.swing.flat` greyscales him, the number beside him
    stays red, and the swing keeps moving because `filter` and `transform` do
    not collide.

    Colour is reinforcement here, not information -- the signed P/L figure
    carries that -- so the alt text is the same either way.

    Falls back to the SVG if the asset is missing, which is a different concern
    entirely: a decorative file that failed to load must never break a render.
    """
    uri = _swing_data_uri()
    if uri is None:
        return _robot(total)
    cls = "swing" if total >= 0 else "swing flat"
    return (f'<img class="{cls}" src="{uri}" width="240" height="323" '
            f'alt="Repete the trading robot, swinging on a playground swing" '
            f'decoding="async">')


def _boot(total: float, n_positions: int, n_symbols: int) -> str:
    """Repete's CRT power-on splash (~3s, click to skip, once per visit):
    scanline terminal, wordmark igniting letter by letter, robot eyes
    powering on, boot log with a live cursor, and a candlestick tape as the
    progress bar. Hidden by default — no-JS and reduced-motion visitors go
    straight to the data. Every line is true."""
    lines = [
        "power on · repete os v2.1 <span class=ok>[paper]</span>",
        "mounting the append-only ledger… <span class=ok>ok</span>",
        "waking the judge… <b>bull and bear reporting in</b>",
        "arming the risk rails… <span class=ok>all deterministic</span>",
        f"book: <b>{n_positions} position{'s' if n_positions != 1 else ''}"
        f"</b> · scanning <b>{n_symbols} names</b>",
        "market brain online <span class=ok>✓</span>",
    ]
    log = "".join(f'<div class=bl>{ln}</div>' for ln in lines)
    mark = "".join(f'<b style="animation-delay:{.15 + i * .07:.2f}s'
                   f',{1.4 + i * .1:.2f}s">{c}</b>'
                   for i, c in enumerate("REPETE"))
    # the loading tape: 14 candles, deterministic pseudo-random green/red mix
    candles = "".join(
        f'<i class={"g" if (i * 7 + 3) % 10 > 3 else "r"} '
        f'style="height:{8 + (i * 13 + 5) % 24}px;'
        f'animation-delay:{.4 + i * .13:.2f}s"></i>'
        for i in range(14))
    return (f'<div id=boot>{_robot(total)}'
            f'<div class=sys>autonomous swing desk</div>'
            f'<div class=mark>{mark}</div>'
            f'<div class=tagline>the market brain is waking</div>'
            f'<div class=log>{log}</div>'
            f'<div id=candles>{candles}</div>'
            f'<div class=skip>click anywhere to skip</div></div>')


def _hero(total: float, start: float, equity_now: float | None,
          realized_only: bool, speech_lines: list[str] | None = None) -> str:
    pct = total / start * 100 if start else 0.0
    cls = "win" if total >= 0 else "loss"
    sub = (f"{pct:+.2f}% on {_fmt_money(start)} starting capital"
           + (f" · account equity {_fmt_money(equity_now)}"
              if equity_now is not None else "")
           + (" · realized only (equity snapshots start next cycle)"
              if realized_only else ""))
    lines = speech_lines or ["beep boop — paper trading, honestly"]
    mascot = _swing(total)
    bubble = (f'<div class=robotbox>{mascot}'
              f'<div class=bubble id=bubble>{_esc(lines[0])}</div></div>'
              f'<script type="application/json" id=replines>'
              f'{json.dumps(lines)}</script>')
    return (f'<div class=hero><div class=htext>'
            f'<div class=hk>Total P/L — paper account</div>'
            f'<div class="hv {cls}" data-count="{total:.2f}" '
            f'data-prefix="$">{_fmt_signed(total)}</div>'
            f'<div class=hs>{_esc(sub)}</div></div>{bubble}</div>')


def _ticker_chips(rep: dict, open_trades: dict, total_pl: float,
                  equity_now: float | None, regime: str | None,
                  n_symbols: int, card: dict, mark: dict | None = None) -> str:
    """One pass of Repete's tape — real facts only, chip-styled."""
    chips = ['<span class="tchip bot">🤖 REPETE · [PAPER]</span>']
    cls = "up" if total_pl >= 0 else "dn"
    eq_txt = f" · eq {_fmt_money(equity_now)}" if equity_now is not None else ""
    chips.append(f'<span class="tchip {cls}">P/L <b>{_fmt_signed(total_pl)}'
                 f'</b>{_esc(eq_txt)}</span>')
    now = datetime.now(timezone.utc)
    for r in open_trades.values():
        age = (now - datetime.fromisoformat(r["ts"])).days
        # With a mark, the tape carries CURRENT value and move. Without one it
        # fell back to qty x the ledger's entry_price — which is the SIGNAL
        # price, not the fill, so the chip advertised a number the bot never
        # paid (SPY: $689.30 signalled, $753.14 filled). Same misleading figure
        # as the positions table had; fixed in both places or in neither.
        m = (mark or {}).get(r["symbol"]) or {}
        val = m.get("market_value")
        if val is None:
            val = (r.get("qty") or 0) * (r.get("entry_price") or 0)
            move = ""
        else:
            cost = (m.get("avg_entry") or 0) * (m.get("qty") or 0)
            pl = m.get("unrealized_pl")
            move = (f' <b class={"win" if pl >= 0 else "loss"}>{pl / cost * 100:+.1f}%</b>'
                    if pl is not None and cost else "")
        chips.append(f'<span class=tchip>HOLDING <b>{_esc(r["symbol"])}</b> '
                     f'{_fmt_money(val)}{move} · {age}d</span>')
    if not open_trades:
        chips.append('<span class=tchip>book is <b>flat</b> — '
                     'patience is a position</span>')
    if regime:
        chips.append(f'<span class="tchip fun">regime <b>{_esc(regime)}</b></span>')
    wr = rep.get("win_rate")
    if wr is not None:
        chips.append(f'<span class=tchip>win rate <b>{wr:.0%}</b> '
                     f'({rep.get("n_closed", 0)} closed)</span>')
    chips.append(f'<span class=tchip>judge vetoes <b>{rep.get("n_vetoes", 0)}'
                 f'</b> · rail blocks <b>{rep.get("n_risk_rejections", 0)}'
                 f'</b></span>')
    chips.append(f'<span class="tchip fun">scanning <b>{n_symbols} names'
                 f'</b> for setups</span>')
    sm = (card or {}).get("summary") or {}
    if sm.get("months_total"):
        chips.append(f'<span class=tchip>vs S&amp;P: beaten '
                     f'<b>{sm["months_beaten"]}/{sm["months_total"]}</b> '
                     f'months</span>')
    chips.append('<span class=tchip>next decision <b>3:45 PM ET</b> 🔔</span>')
    return "".join(chips)


def _tape(chips_html: str) -> str:
    """The repeating strip: content twice + translateX(-50%) = seamless loop."""
    return (f'<div class=tape><div class=tape-track>{chips_html}{chips_html}'
            f'</div></div>')


def latest_position_mark(records: list[dict]) -> tuple[dict, str] | tuple[None, None]:
    """Newest `positions_mark` event as ({symbol: {...}}, iso_ts), else (None, None).

    The dashboard is a STATIC file published to GitHub Pages and the test suite
    pins that it renders offline. So current value comes from the last mark the
    agent recorded — never a live quote fetched at render time, which would need
    broker keys everywhere this renders (CI included) and, for a genuinely live
    public page, a credential shipped to the browser.
    """
    for r in reversed(records):
        if r.get("type") == "event" and r.get("event") == "positions_mark":
            try:
                return json.loads(r.get("detail") or "{}"), r.get("ts")
            except (ValueError, TypeError):
                return None, None
    return None, None


def _mark_age_note(mark_ts: str | None, now: datetime) -> str:
    """"as of" line for the positions heading. A number with no timestamp on a
    page regenerated three times a weekday is a number that will eventually be
    read as live when it is three days old."""
    if not mark_ts:
        return ""
    try:
        when = datetime.fromisoformat(mark_ts)
    except (ValueError, TypeError):
        return ""
    hours = (now - when).total_seconds() / 3600
    stamp = when.astimezone(ZoneInfo("America/New_York")).strftime("%a %d %b, %H:%M ET")
    if hours > 24:
        return (f' <span class="small warn">— valued {stamp}, '
                f'{hours / 24:.0f}d old (market closed or no cycle since)</span>')
    return f' <span class=small>— valued {stamp}</span>'


def _positions_rows(open_trades: dict, now: datetime,
                    mark: dict | None = None) -> str:
    """Open book. With a mark, each row also carries what it is worth now.

    Every derived number is guarded: a zero qty or zero entry renders an
    em-dash rather than raising. `size_order()` carried exactly this bug
    (ZeroDivisionError on a non-positive price) until 2026-07-25, and a crash
    here would take down the whole page rather than one cell.
    """
    mark = mark or {}
    live = bool(mark)
    rows, tot_val, tot_pl, tot_cost = [], 0.0, 0.0, 0.0

    for tid, r in open_trades.items():
        age = (now - datetime.fromisoformat(r["ts"])).days
        stop = (r.get("order") or {}).get("stop_price")
        m = mark.get(r["symbol"]) or {} if live else {}
        # WHICH entry price? The ledger's `entry_price` is the SIGNAL price —
        # the close the decision was made on — NOT what the bot paid.
        # fill_quality shows the gap is real and can be large (SPY signalled
        # 689.30, filled 753.14: 926 bps, a hangover from the 2026-07-16
        # stale-bars incident). Printing the signal price next to a live "Now"
        # invites the reader to subtract them and read a gain where the account
        # shows a loss. So a marked row uses the broker's avg_entry — the true
        # cost basis — and Cost -> Now -> Value -> Unrealized all reconcile.
        # Unmarked rows keep the old column and the old meaning.
        basis = m.get("avg_entry") if live else None
        shown_entry = basis if basis else r.get("entry_price")
        cells = [
            f"<td>{_esc(r['symbol'])}</td>",
            f"<td>{_esc(r.get('strategy') or 'ma_crossover')}</td>",
            f"<td>{r.get('qty') or ''}</td>",
            f"<td>{_fmt_money(shown_entry) if shown_entry else '—'}</td>",
        ]
        if live:
            # A broker position with no ledger record (or vice versa) must not
            # invent a row or a number — absent symbols simply show em-dashes.
            qty = m.get("qty") or 0
            entry = m.get("avg_entry") or 0
            value = m.get("market_value")
            pl = m.get("unrealized_pl")
            now_px = (value / qty) if (value is not None and qty) else None
            cost = entry * qty
            pct = (pl / cost * 100) if (pl is not None and cost) else None
            if value is not None:
                tot_val += value
                tot_cost += cost
            if pl is not None:
                tot_pl += pl
            pl_cls = "win" if (pl or 0) >= 0 else "loss"
            cells += [
                f"<td>{_fmt_money(now_px) if now_px is not None else '—'}</td>",
                f"<td>{_fmt_money(value) if value is not None else '—'}</td>",
                (f'<td class={pl_cls}>{_fmt_signed(pl)}'
                 f'{f" ({pct:+.2f}%)" if pct is not None else ""}</td>'
                 if pl is not None else "<td>—</td>"),
            ]
        cells += [
            f"<td>{_fmt_money(stop) if stop else '—'}</td>",
            f"<td>{age}d</td>",
            f"<td class=reason>{_esc(r.get('strategy_reason'))}</td>",
        ]
        rows.append("<tr>" + "".join(cells) + "</tr>")

    if not rows:
        return "<p class=small>No open positions.</p>"

    # "Cost" when marked (broker avg_entry, the price actually paid) vs "Entry"
    # when not (the ledger's signal price). Different numbers, different names.
    head = ("<tr><th>Symbol</th><th>Strategy</th><th>Qty</th>"
            + ("<th>Cost</th><th>Now</th><th>Value</th><th>Unrealized</th>"
               if live else "<th>Entry</th>")
            + "<th>Stop</th><th>Age</th><th>Entry reason</th></tr>")
    total = ""
    if live:
        tpct = (tot_pl / tot_cost * 100) if tot_cost else None
        tcls = "win" if tot_pl >= 0 else "loss"
        total = (f'<tr class=totrow><td colspan=5><b>Book</b></td>'
                 f'<td><b>{_fmt_money(tot_val)}</b></td>'
                 f'<td class={tcls}><b>{_fmt_signed(tot_pl)}'
                 f'{f" ({tpct:+.2f}%)" if tpct is not None else ""}</b></td>'
                 f'<td colspan=3></td></tr>')
    return ("<div class=tblwrap><table>" + head + "".join(rows) + total
            + "</table></div>")


def _decisions_rows(records: list[dict]) -> str:
    decisions = [r for r in records if r.get("type") == "decision"][-N_DECISIONS:]
    rows = []

    def _is_quiet_hold(rec) -> bool:
        """A 'nothing happened' row: held, no judge verdict, nothing to read.

        These made up the bulk of the table and each consumed a full row to
        say "no entry from 3 strategies", pushing the rows that DO carry
        bull/bear/judge reasoning off the screen.
        """
        return (rec.get("action") == "hold" and not rec.get("llm_review")
                and not rec.get("executed"))

    pending_holds: list[dict] = []

    def _flush_holds():
        """Collapse a run of quiet holds into one line, naming the symbols.

        The symbols are listed rather than hidden behind a toggle — the list
        IS the information, and a disclosure widget that has to be opened to
        learn nothing happened is worse than a sentence.
        """
        if not pending_holds:
            return
        syms = [h.get("symbol", "?") for h in pending_holds]
        n = len(syms)
        rows.append(
            f'<tr class="r-skip r-hold holdrun"><td colspan=7>'
            f'<b>{n}</b> symbol{"s" if n != 1 else ""} held — no entry '
            f'<span class=holdsyms>{_esc(", ".join(syms))}</span></td></tr>')
        pending_holds.clear()

    for r in reversed(decisions):
        if _is_quiet_hold(r):
            pending_holds.append(r)
            continue
        _flush_holds()
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
    _flush_holds()          # a run ending the list must still be emitted
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


def render(cfg: dict | None = None, out_path: str | None = None,
           spy_bars: list[dict] | None = None) -> str:
    if cfg is None:
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
    out_path = out_path or sitepaths.resolve(cfg, OUT_PATH)
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

    # Last recorded broker valuation of the open book (display only — never an
    # input to a trading decision; invariant #4). Absent on an old ledger or a
    # host that has not run a cycle, in which case the table renders as before.
    mark, mark_ts = latest_position_mark(records)

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
                if slip and slip.get("median_bps") is not None
                else "no clean fills yet")

    # Book value / unrealized only appear when a mark exists — a card reading
    # "$0.00" would be indistinguishable from a flat book.
    book_cards = []
    if mark:
        _val = sum(p.get("market_value") or 0 for p in mark.values())
        _upl = sum(p.get("unrealized_pl") or 0 for p in mark.values())
        _cost = sum((p.get("avg_entry") or 0) * (p.get("qty") or 0)
                    for p in mark.values())
        _pct = f" ({_upl / _cost * 100:+.1f}%)" if _cost else ""
        book_cards = [("open position value", _fmt_money(_val)),
                      ("unrealized P&L", f"{_fmt_signed(_upl)}{_pct}")]

    # ---- stat cards, grouped and semantically coloured -------------------
    #
    # Was twelve equal cards on a 4-colour repeating border, so "days of
    # history" carried the same visual weight as unrealized P&L and the colour
    # meant only "position in the list". Now: four labelled groups, colour
    # reserved for signed money, and Performance set larger than Activity so
    # the hierarchy survives with the labels covered up.
    n_closed = rep["n_closed"]
    wr_txt, wr_tone = _ratio_cell(wr, n_closed)
    pf_txt, pf_tone = _ratio_cell(pf, n_closed)

    def _tone_for_money(v):
        return "win" if v > 0 else "loss" if v < 0 else ""

    perf = [("realized P&L", _fmt_money(rep["realized_pnl"]),
             _tone_for_money(rep["realized_pnl"]))]
    if mark:
        perf.append(("unrealized P&L", book_cards[1][1],
                     _tone_for_money(_upl)))
    perf += [("win rate", wr_txt, wr_tone), ("profit factor", pf_txt, pf_tone)]

    book = [("open positions", rep["n_open"], ""),
            ("closed trades", n_closed, "")]
    if mark:
        book.insert(1, ("open position value", book_cards[0][1], ""))

    groups = [
        ("Performance", "big", perf),
        ("Book", "", book),
        ("Activity", "", [("decisions", rep["n_decisions"], ""),
                          ("days of history", rep["history_days"], ""),
                          ("rail rejections", rep["n_risk_rejections"], ""),
                          ("LLM vetoes", rep["n_vetoes"], "")]),
        ("Execution", "", [("slippage", slip_txt, "")]),
    ]
    cards = "".join(
        f'<section class=cardgroup><h3>{_esc(title)}</h3><div class=cards>'
        + "".join(
            f'<div class="card {size} {tone}"><div class=v>{v}</div>'
            f'<div class=k>{_esc(k)}</div></div>' for k, v, tone in items)
        + "</div></section>"
        for title, size, items in groups if items)

    def _pf_text(v):
        if v is None:
            return "n/a"
        return "inf" if v == float("inf") else f"{v:.2f}"

    # Per-strategy ratios get the same n= guard as the cards. A strategy row
    # reading "100% / inf" off one trade is the same misreading in a smaller
    # font.
    strat_rows = "".join(
        f"<tr><td>{_esc(n)}</td><td>{s['n_closed']}</td>"
        f"<td class={_ratio_cell('', s['n_closed'])[1]}>"
        f"{_ratio_cell('%.0f%%' % (s['win_rate'] * 100), s['n_closed'])[0]}</td>"
        f"<td class={_ratio_cell('', s['n_closed'])[1]}>"
        f"{_ratio_cell(_pf_text(s['profit_factor']), s['n_closed'])[0]}</td>"
        f"<td class={'win' if s['realized_pnl'] >= 0 else 'loss'}>"
        f"{_fmt_money(s['realized_pnl'])}</td></tr>"
        for n, s in sorted(per_strat.items()))
    strat_tbl = (("<div class=tblwrap><table><tr><th>Strategy</th>"
                  "<th>Closed</th><th>Win</th><th>PF</th><th>P&L</th></tr>"
                  + strat_rows + "</table></div>")
                 if strat_rows else "<p class=small>No closed trades yet.</p>")

    exits = ", ".join(f"{k}: {v}" for k, v in
                      sorted(rep["exit_reasons"].items())) or "none yet"

    # W6-A1: which news sources actually fed today's read. Published because
    # the interesting case is a source reading ZERO — for five days every RSS
    # feed was fetched and then discarded by the headline budget (W6-A0) and no
    # surface anywhere could have shown that. A named zero is the whole point.
    news_line = ""
    for _r in reversed(records):
        if _r.get("type") == "event" and _r.get("event") == "market_context":
            _d = str(_r.get("detail") or "")
            if "| sources: " in _d:
                news_line = _d.split("| sources: ", 1)[1][:300]
            break

    # Repete's voice: playful lines, real facts only (rendered per cycle).
    open_now = ledger.open_buys()
    n_symbols = len(cfg.get("symbols") or [])
    regime_now = None
    for _r in reversed(records):
        if _r.get("type") == "decision" and _r.get("regime"):
            regime_now = _r["regime"]
            break
    speech = [
        f"beep boop — {len(open_now)} position"
        f"{'s' if len(open_now) != 1 else ''} on the book"
        if open_now else "beep boop — book is flat, and that's a choice too",
        f"scanning {n_symbols} names for the next setup",
        f"the judge vetoed {rep['n_vetoes']} of my ideas — rude, but fair",
        f"rails blocked {rep['n_risk_rejections']} trades so I don't "
        f"have to be sorry later",
        "next decision at the 3:45 bell 🔔",
        "paper money, real discipline",
    ]
    if regime_now:
        speech.insert(2, f"regime says {regime_now} — I trade the math, "
                         f"not the mood")

    # Monthly scorecard vs S&P (2026-07-21): the benchmark goal, measured and
    # published month by month — wins and losses both.
    import scorecard
    card = scorecard.monthly_scorecard(
        records, spy_bars or [], scorecard.realized_pnl_by_month(records))
    month_rows = "".join(
        f"<tr><td>{_esc(m['month'])}</td>"
        f"<td class={'win' if m['bot_ret_pct'] >= 0 else 'loss'}>"
        f"{m['bot_ret_pct']:+.2f}%</td>"
        f"<td>{('%+.2f%%' % m['spy_ret_pct']) if m['spy_ret_pct'] is not None else 'n/a'}</td>"
        f"<td>{'✅ beat' if m['beat'] else '❌ trailed' if m['beat'] is False else '—'}</td>"
        f"<td>{m['max_dd_pct']:.2f}%</td></tr>"
        for m in card["months"])
    sm = card["summary"]
    month_tbl = (("<div class=tblwrap><table><tr><th>Month</th><th>Bot</th>"
                  "<th>S&amp;P (SPY)</th><th>vs S&amp;P</th><th>Max DD</th></tr>"
                  + month_rows + "</table></div>"
                  f"<p class=small>Beaten {sm['months_beaten']}/"
                  f"{sm['months_total']} months · cumulative bot "
                  f"{sm['cum_bot_pct']:+.2f}% vs SPY {sm['cum_spy_pct']:+.2f}%. "
                  "Goal: beat the S&amp;P on a rolling basis — measured every "
                  "month, promised never.</p>")
                 if month_rows else
                 "<p class=small>Monthly scorecard appears after the first "
                 "full month of equity history.</p>")

    # ---- volatile regions, rendered once and re-served as JSON -----------
    # Everything here can change between cycles. The page swaps these in place
    # on poll; anything not listed is static chrome and never moves.
    regions = {
        "tape": _tape(_ticker_chips(rep, open_now, total_pl, equity_now,
                                    regime_now, n_symbols, card, mark)),
        "hero": _hero(total_pl, start, equity_now, realized_only, speech),
        "cards": cards,
        "positions": _positions_rows(ledger.open_buys(), now, mark),
        "decisions": _decisions_rows(records),
        "plchart": pl_chart,
        "eqchart": eq_chart + chart_note,
        "bars": bars,
        "strat": strat_tbl,
        "months": month_tbl,
    }
    payload = {"generated_at": now.isoformat(), "regions": regions}
    payload["hash"] = hashlib.sha256(
        json.dumps(regions, sort_keys=True).encode()).hexdigest()[:16]
    data_path = os.path.join(os.path.dirname(out_path) or ".", DATA_PATH)
    with open(data_path, "w") as f:
        json.dump(payload, f)

    live_js = LIVE_JS % {"amber": STALE_AMBER_HOURS, "red": STALE_RED_HOURS,
                         "data": DATA_PATH}
    badge = (f'<span id=fresh class="fresh green" '
             f'data-gen="{now.isoformat()}" data-hash="{payload["hash"]}">'
             f'live</span>')

    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>trading-agent dashboard</title><style>{CSS}</style></head><body>
{_boot(total_pl, len(open_now), n_symbols)}
<div class=wrap>
<h1>trading-agent <span class=small>[PAPER] — generated
{now.strftime('%Y-%m-%d %H:%M UTC')}</span>
&nbsp; <a class=x href="journal.html">trade journal →</a>
&nbsp; <a class=x href="blog.html">blog →</a></h1>
<p class=small><span class=livedot></span>live paper account · rebuilt
after every cycle from the append-only ledger &nbsp;{badge}</p>
<div id=rgn-tape>{regions['tape']}</div>
<div id=rgn-hero>{regions['hero']}</div>
<div id=rgn-cards>{regions['cards']}</div>
<h2>💼 Open positions{_mark_age_note(mark_ts, now)}</h2>
<div id=rgn-positions>{regions['positions']}</div>
<h2>⚖️ Recent decisions (last {N_DECISIONS})</h2>
<details open><summary>every signal, the judge's verdict, and what
happened — filter with the chips</summary>
<div id=rgn-decisions>{regions['decisions']}</div></details>
<h2>📈 P/L over time</h2><div id=rgn-plchart>{regions['plchart']}</div>
<h2>💰 Equity</h2><div id=rgn-eqchart>{regions['eqchart']}</div>
<h2>🪙 Trade scoreboard</h2><div id=rgn-bars>{regions['bars']}</div>
<h2>🧭 Per-strategy</h2><div id=rgn-strat>{regions['strat']}</div>
<p class=small>Exits — {_esc(exits)}</p>
{f'<p class=small>News sources — {_esc(news_line)}</p>' if news_line else ''}
<h2>🗓️ Monthly vs S&amp;P</h2><div id=rgn-months>{regions['months']}</div>
<h2>🧠 Lesson book</h2>
<details open><summary>falsifiable hypotheses the bot is testing from its
own closed trades</summary>{_lessons_rows(states)}</details>
<p class=small>{_esc(calib)}</p>
<p class=small>Paper trading. Generated from memory/ledger.jsonl — the
append-only audit trail is the source of truth, this page is a view.
The bot narrates every trade and its reasoning on its own
<a class=x href="blog.html">blog</a> and
<a class=x href="journal.html">trade journal</a>.</p>
<p class=small>{_esc(disclaimer.DISCLAIMER)}</p>
</div><script>{JS}</script><script>{live_js}</script></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    print(f"wrote {render()}")
