"""X (Twitter) trade-recap posting via the official API v2 (tweepy).

Safety defaults:
  - dry_run: true in config prints the post instead of publishing.
  - Posts always disclose paper trading when disclose_paper is true.
  - Posting failures never interrupt trading — they're logged and skipped.

Every outbound post is also archived to memory/posts.jsonl (append-only) —
the source of truth for the public blog page. Archiving never breaks posting.
"""
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("x")


def _archive(text: str, link: str | None, status: str, cfg: dict):
    """Append the post to the archive; failures are logged, never raised."""
    try:
        path = cfg["x_posting"].get("posts_log_path", "memory/posts.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(),
                "text": text, "link": link, "status": status}) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("post archive failed: %s", e)


def _client():
    import tweepy
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def _template_post(trade: dict, cfg: dict) -> str:
    """Fallback recap when the LLM isn't available."""
    xc = cfg["x_posting"]
    mode = "[PAPER] " if xc.get("disclose_paper", True) else ""
    action = trade["action"].upper()
    text = (f"{mode}Bot {action} {trade.get('qty', '?')} {trade['symbol']} "
            f"@ ~${trade.get('entry_price', trade.get('exit_price', 0)):.2f}. "
            f"Why: {trade['strategy_reason']}")
    if "pnl_pct" in trade:
        text += f" | Result: {trade['pnl_pct']:+.2f}%"
    lesson = trade.get("lesson_hypothesis")
    if lesson:
        room = 275 - len(text) - len(" | Lesson: ")
        if room > 20:  # only when a meaningful chunk of the lesson fits
            text += f" | Lesson: {lesson[:room]}"
    tags = xc.get("hashtags", "")
    if tags and len(text) + len(tags) + 1 <= 275:
        text += f" {tags}"
    return text[:275]


def post_text(text: str, cfg: dict, link: str | None = None):
    """Publish (or dry-run print) one post. The single choke point every
    outbound post goes through: paper disclosure enforced, 275-char cap,
    failures logged and swallowed — posting must never break trading.
    `link` is appended after the cap: X wraps every URL to a fixed
    23-character t.co link, so the raw URL length doesn't count."""
    xc = cfg["x_posting"]
    if not xc["enabled"] or not text:
        return
    if xc.get("disclose_paper", True) and "paper" not in text.lower():
        text = "[PAPER] " + text
    if link:
        text = text[:275 - 24] + "\n" + link  # 23 (t.co) + newline
    else:
        text = text[:275]

    if xc.get("dry_run", True):
        log.info("X DRY RUN (not posted): %s", text)
        print(f"\n--- X post (dry run) ---\n{text}\n------------------------\n")
        _archive(text, link, "dry_run", cfg)
        return
    try:
        resp = _client().create_tweet(text=text)
        log.info("Posted to X: tweet id %s", resp.data["id"])
        _archive(text, link, "posted", cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("X post failed (%s) — continuing", e)
        _archive(text, link, "failed", cfg)


def post_recap(trade: dict, cfg: dict, llm_draft: str | None = None,
               link: str | None = None):
    if not cfg["x_posting"]["enabled"]:
        return
    post_text(llm_draft or _template_post(trade, cfg), cfg, link=link)
