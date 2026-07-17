"""Public trade journal — a ~500-word write-up for every trade event.

Each executed entry and every close gets a journal entry: the opportunity the
strategy saw, the logic behind trading it, the judge's view, and the risk
plan. Entries are appended to memory/journal.jsonl (append-only, like every
store here) and rendered into a self-contained journal.html that the publish
step pushes to the public dashboard site; trade tweets link to the entry
anchor. Fable 5 writes the prose; a plain template covers LLM outages.
Journal writing is cosmetic — it can never block or crash trading.
"""
import html
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm

log = logging.getLogger("journal")

DEFAULT_PATH = "memory/journal.jsonl"
OUT_PATH = "journal.html"

CSS = """
body{font-family:Georgia,'Times New Roman',serif;margin:0;background:#f5f6f8;
     color:#1c1e21;line-height:1.65}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px 60px}
h1{font-family:-apple-system,Segoe UI,sans-serif;font-size:22px}
.small{color:#5f6673;font-size:13px;font-family:-apple-system,sans-serif}
article{background:#fff;border:1px solid #e2e5ea;border-radius:8px;
        padding:20px 24px;margin:18px 0}
article h2{font-size:17px;margin:0 0 2px}
article .meta{color:#5f6673;font-size:12px;
              font-family:-apple-system,sans-serif;margin-bottom:12px}
article .body{white-space:pre-wrap;font-size:15px}
a{color:#2463eb;text-decoration:none}a:hover{text-decoration:underline}
.badge{display:inline-block;padding:1px 8px;border-radius:9px;font-size:11px;
       font-family:-apple-system,sans-serif;background:#e8eaf0}
.buy{background:#d9f0e0}.close{background:#fdeec8}
"""


def _template_entry(trade: dict) -> str:
    """Honest fallback write-up when the LLM is unavailable."""
    action = trade.get("action", "trade").upper()
    rev = trade.get("llm_review") or {}
    parts = [
        f"**This is a paper trade** — no real money is involved.",
        f"\n### The setup\nThe `{trade.get('strategy', 'ma_crossover')}` "
        f"strategy signaled {action} {trade.get('symbol')} at "
        f"~${trade.get('entry_price') or trade.get('exit_price') or 0:.2f}. "
        f"Its stated reason: {trade.get('strategy_reason', 'n/a')}.",
    ]
    if trade.get("indicators"):
        parts.append("\n### The numbers\n" + "\n".join(
            f"- {k}: {v}" for k, v in list(trade["indicators"].items())[:8]))
    if rev.get("reasoning"):
        parts.append(f"\n### The judge's view\nVerdict: "
                     f"{rev.get('verdict', 'n/a')} "
                     f"(scale {rev.get('scale', 1.0)}). "
                     f"{rev['reasoning']}")
    stop = (trade.get("order") or {}).get("stop_price")
    if stop:
        parts.append(f"\n### The risk plan\nProtective stop at ${stop:.2f}, "
                     "set deterministically from ATR at entry — the trade is "
                     "wrong if price gets there.")
    if "pnl_pct" in trade:
        parts.append(f"\n### The outcome\nClosed at "
                     f"${trade.get('exit_price', 0):.2f} for "
                     f"{trade['pnl_pct']:+.2f}% "
                     f"({trade.get('exit_reason', 'exit')}). One trade proves "
                     "little either way — the ledger keeps score over months.")
    parts.append("\n*(Template entry — the AI writer was unavailable; "
                 "the full audit trail lives in the ledger.)*")
    return "\n".join(parts)


def add_entry(trade: dict, cfg: dict,
              path: str | None = None) -> dict | None:
    """Write one journal entry for a trade event. Never raises."""
    try:
        path = path or cfg.get("x_posting", {}).get("journal_path",
                                                    DEFAULT_PATH)
        text = llm.write_journal_entry(trade, cfg) or _template_entry(trade)
        kind = "close" if "pnl_pct" in trade else trade.get("action", "trade")
        entry = {
            "trade_id": trade.get("trade_id", ""),
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": trade.get("symbol", ""),
            "kind": kind,
            "title": (f"{kind.upper()} {trade.get('symbol', '')} — "
                      f"{trade.get('strategy', 'ma_crossover')}"),
            "text": text,
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry
    except Exception as e:  # noqa: BLE001 — journal must never block trading
        log.warning("journal entry failed: %s", e)
        return None


def _load(path: str) -> list[dict]:
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def render(cfg: dict, out_path: str = OUT_PATH,
           path: str | None = None) -> str:
    """Render journal.html (newest first, one anchor per trade_id)."""
    path = path or cfg.get("x_posting", {}).get("journal_path", DEFAULT_PATH)
    entries = _load(path)
    articles = []
    for e in reversed(entries):
        badge = "close" if e["kind"] == "close" else "buy"
        articles.append(
            f'<article id="{html.escape(e["trade_id"])}">'
            f'<h2>{html.escape(e["title"])} '
            f'<span class="badge {badge}">{html.escape(e["kind"])}</span></h2>'
            f'<div class=meta>{html.escape(e["ts"][:16].replace("T", " "))} '
            f'UTC · trade {html.escape(e["trade_id"])}</div>'
            f'<div class=body>{html.escape(e["text"])}</div></article>')
    body = "".join(articles) or ("<p class=small>No trades journaled yet — "
                                 "entries appear as the bot trades.</p>")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>trading-agent journal</title><style>{CSS}</style></head><body>
<div class=wrap>
<h1>trade journal <span class=small>[PAPER] — generated {now}</span></h1>
<p class=small>Every trade this bot makes, explained: the opportunity, the
strategy's logic, the AI judge's view, and the risk plan. Paper trading —
nothing here is financial advice.
<a href="index.html">← dashboard</a> ·
<a href="https://x.com/Repete2026">@Repete2026 on X ↗</a></p>
{body}
</div></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path
