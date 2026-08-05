"""One-time catch-up: post already-open trades that never made it to X.

Some positions were opened while X posting was down (dead LLM key, then
depleted X credits), so they were never tweeted or journaled. This reads
those executed buys from the ledger and pushes each through the SAME public
path a live buy uses — a 500-word journal entry (src/journal.py) plus a recap
tweet (src/x_poster.py) — with text that is explicit about WHEN the trade was
opened, so nothing reads as a fresh trade firing today.

    python src/backfill_posts.py --dry-run   # preview (default, prints only)
    python src/backfill_posts.py --live       # actually post to X

Idempotent: a trade already present in the journal is skipped, so a re-run
can't double-post. Cosmetic/operational only — never touches trading state.
"""
import argparse
import calendar
import json
import logging
import os
import sys
import time

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import journal
import x_poster
from ledger import Ledger
from strategies.base import ENTRY_ACTIONS

log = logging.getLogger("backfill")

POST_SPACING_SEC = 3  # gap between live posts — avoid a burst of identical calls


def _month_abbr(iso_date: str) -> str:
    """'2026-07-16' -> 'Jul 16' (honest 'when' label for the post)."""
    try:
        y, m, d = (int(x) for x in iso_date.split("-"))
        return f"{calendar.month_abbr[m]} {d}"
    except (ValueError, IndexError):
        return iso_date


def pending_buys(records: list[dict], already: set[str]) -> list[dict]:
    """Executed ENTRY decisions, oldest first, not already POSTED to X.

    ENTRY_ACTIONS, not `== "buy"`: a short entry is a trade this bot made, and
    a backfill that silently skipped it would leave a hole in the public record
    that looks like a quiet day rather than an omission. The NAME is unchanged
    for the same reason ledger.open_buys() kept its own — renaming it here
    while its sibling waits for a follow-up would leave two conventions."""
    out = []
    for r in records:
        if (r.get("type") == "decision" and r.get("action") in ENTRY_ACTIONS
                and r.get("executed") and r.get("trade_id") not in already):
            out.append(r)
    out.sort(key=lambda r: r.get("ts", ""))
    return out


def posted_ids(cfg: dict) -> set[str]:
    """trade_ids already successfully posted (parsed from the journal-link
    fragment of status=='posted' archive rows). Idempotency keys off POSTED,
    not journaled — a dry-run preview journals but must not block the live
    post; a duplicate journal entry is likewise avoided in run()."""
    path = cfg.get("x_posting", {}).get("posts_log_path", "memory/posts.jsonl")
    out = set()
    try:
        with open(path) as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                link = row.get("link") or ""
                if row.get("status") == "posted" and "#" in link:
                    out.add(link.rsplit("#", 1)[1])
    except OSError:
        pass
    return out


def journaled_ids(cfg: dict) -> set[str]:
    path = cfg.get("x_posting", {}).get("journal_path", journal.DEFAULT_PATH)
    return {e.get("trade_id") for e in journal._load(path) if e.get("trade_id")}


def backfill_recap(rec: dict) -> dict:
    """Reconstruct the buy recap main.py builds, tagged with the open date."""
    return {
        "trade_id": rec["trade_id"],
        "symbol": rec["symbol"],
        "action": "buy",
        "qty": rec.get("qty"),
        "entry_price": rec.get("entry_price"),
        "strategy": rec.get("strategy"),
        "strategy_reason": rec.get("strategy_reason"),
        "indicators": rec.get("indicators"),
        "llm_review": rec.get("llm_review"),
        "order": rec.get("order"),
        "regime": rec.get("regime"),
        "opened_date": (rec.get("ts") or "")[:10],
        "backfill_note": "Opened earlier while posting was offline; "
                         "catch-up entry — position still open.",
    }


def backfill_text(rec: dict) -> str:
    """Deterministic, honest recap tweet. [PAPER]/cap/link handled downstream."""
    when = _month_abbr((rec.get("ts") or "")[:10])
    price = rec.get("entry_price")
    px = f" @ ~${price:.2f}" if isinstance(price, (int, float)) else ""
    reason = (rec.get("strategy_reason") or "").rstrip(". ")
    head = (f"Catch-up: opened {rec['symbol']} ({rec.get('strategy', 'n/a')}) "
            f"on {when}{px}. Position still open.")
    if reason:
        head += f" {reason}."
    return head[:250]  # leave room for the journal link the poster appends


def run(cfg: dict, live: bool = False) -> list[dict]:
    """Journal + post every pending buy. Returns the recaps acted on."""
    if not live:  # preview mode forces dry_run regardless of config
        cfg = {**cfg, "x_posting": {**cfg["x_posting"], "dry_run": True}}
    ledger = Ledger(cfg["memory"]["ledger_path"])
    pending = pending_buys(ledger.all_records(), posted_ids(cfg))
    already_journaled = journaled_ids(cfg)
    base = cfg.get("x_posting", {}).get("journal_url_base")
    log.info("backfill: %d pending buy(s) (%s)", len(pending),
             "LIVE" if live else "dry-run")

    done = []
    for i, rec in enumerate(pending):
        recap = backfill_recap(rec)
        try:
            # Reuse an existing journal entry (e.g. written by a prior
            # dry-run) instead of appending a duplicate.
            if rec["trade_id"] not in already_journaled:
                journal.add_entry(recap, cfg)
                journal.render(cfg)
            link = (f"{base}#{rec['trade_id']}" if base else None)
            x_poster.post_recap(recap, cfg, backfill_text(rec), link=link)
            ledger.log_event("backfill_post",
                             f"{recap['symbol']} {recap['trade_id']}")
            done.append(recap)
        except Exception as e:  # noqa: BLE001 — one failure never aborts the rest
            log.warning("backfill failed for %s: %s",
                        rec.get("symbol"), e)
        if live and i < len(pending) - 1:
            time.sleep(POST_SPACING_SEC)

    try:  # refresh the public pages once at the end
        import blog
        import dashboard
        dashboard.render(cfg)
        blog.render(cfg)
    except Exception as e:  # noqa: BLE001 — cosmetic
        log.warning("dashboard/blog render failed: %s", e)
    return done


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("logs/agent.log", mode="a")])
    p = argparse.ArgumentParser(description="Backfill open trades to X + journal")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="preview only (default)")
    g.add_argument("--live", action="store_true", help="actually post to X")
    args = p.parse_args()

    load_dotenv()
    with open("config.yaml") as f:
        _cfg = yaml.safe_load(f)
    acted = run(_cfg, live=args.live)
    print(f"\n{'POSTED' if args.live else 'previewed'} {len(acted)} backfill "
          f"recap(s).")
