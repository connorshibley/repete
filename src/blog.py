"""Bot blog — the permanent public archive of everything the bot posts.

X posts are ephemeral and capped at 280 chars; this page keeps the full
daily narrative forever: each day's market context (what the bot understood
that morning), then every post it actually published — morning plan, trade
recaps, evening review — with journal links preserved. Rendered from
memory/posts.jsonl (written by x_poster's choke point, so nothing is ever
missed) plus the ledger's market_context events.
"""
import html
import json
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("blog")

OUT_PATH = "blog.html"
ET = ZoneInfo("America/New_York")

CSS = """
body{font-family:-apple-system,Segoe UI,sans-serif;margin:0;background:#f5f6f8;
     color:#1c1e21;line-height:1.6}
.wrap{max-width:720px;margin:0 auto;padding:24px 16px 60px}
h1{font-size:22px}
h2{font-size:16px;border-bottom:1px solid #d8dbe0;padding-bottom:4px;
   margin:30px 0 8px}
.small{color:#5f6673;font-size:13px}
.ctx{background:#eef0f4;border-radius:8px;padding:10px 14px;font-style:italic;
     font-size:14px;color:#434851;margin:10px 0}
.post{background:#fff;border:1px solid #e2e5ea;border-radius:8px;
      padding:12px 16px;margin:10px 0;font-size:15px;white-space:pre-wrap}
.post .t{color:#5f6673;font-size:11px;margin-bottom:4px;font-style:normal}
a{color:#2463eb;text-decoration:none}a:hover{text-decoration:underline}
"""


def _load_posts(cfg: dict) -> list[dict]:
    path = cfg.get("x_posting", {}).get("posts_log_path", "memory/posts.jsonl")
    try:
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []


def _context_by_day(cfg: dict) -> dict:
    """ET date -> first market_context summary of that day (from the ledger)."""
    out: dict = {}
    try:
        from ledger import Ledger
        for r in Ledger(cfg["memory"]["ledger_path"]).all_records():
            if r.get("type") == "event" and r.get("event") == "market_context":
                day = (datetime.fromisoformat(r["ts"])
                       .astimezone(ET).date().isoformat())
                out.setdefault(day, r.get("detail", ""))
    except Exception as e:  # noqa: BLE001
        log.warning("blog: market context lookup failed: %s", e)
    return out


def render(cfg: dict, out_path: str = OUT_PATH) -> str:
    posts = [p for p in _load_posts(cfg) if p.get("status") == "posted"]
    ctx_by_day = _context_by_day(cfg)

    days: dict = {}
    for p in posts:
        ts = datetime.fromisoformat(p["ts"]).astimezone(ET)
        days.setdefault(ts.date().isoformat(), []).append((ts, p))

    sections = []
    for day in sorted(days, reverse=True):
        parts = [f"<h2>{day}</h2>"]
        ctx = ctx_by_day.get(day)
        if ctx:
            parts.append(f'<div class=ctx>Morning read: '
                         f'{html.escape(ctx)}</div>')
        for ts, p in days[day]:
            body = html.escape(p["text"])
            if p.get("link"):
                url = html.escape(p["link"])
                anchor = f'<a href="{url}">full write-up →</a>'
                if url in body:
                    body = body.replace(url, anchor)
                else:
                    body += f"\n{anchor}"
            parts.append(f'<div class=post><div class=t>'
                         f'{ts.strftime("%-I:%M %p ET")}</div>{body}</div>')
        sections.append("".join(parts))

    body = "".join(sections) or ("<p class=small>No posts yet — the blog "
                                 "fills in as the bot trades and narrates "
                                 "its day.</p>")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>trading-agent blog</title><style>{CSS}</style></head><body>
<div class=wrap>
<h1>bot blog <span class=small>[PAPER] — generated {now}</span></h1>
<p class=small>Every post this bot publishes, archived by day with the
market context it was working from. Paper trading; nothing here is financial
advice. <a href="index.html">dashboard</a> ·
<a href="journal.html">trade journal</a> ·
<a href="https://x.com/Repete2026">@Repete2026 on X ↗</a></p>
{body}
</div></body></html>"""
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    import yaml
    with open("config.yaml") as f:
        print("wrote", render(yaml.safe_load(f)))
