"""X (Twitter) trade-recap posting via the official API v2 (tweepy).

Safety defaults:
  - dry_run: true in config prints the post instead of publishing.
  - Posts always disclose paper trading when disclose_paper is true.
  - Posting failures never interrupt trading — they're logged and skipped.
"""
import logging
import os

log = logging.getLogger("x")


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


def post_recap(trade: dict, cfg: dict, llm_draft: str | None = None):
    xc = cfg["x_posting"]
    if not xc["enabled"]:
        return
    text = llm_draft or _template_post(trade, cfg)
    if xc.get("disclose_paper", True) and "paper" not in text.lower():
        text = ("[PAPER] " + text)[:275]

    if xc.get("dry_run", True):
        log.info("X DRY RUN (not posted): %s", text)
        print(f"\n--- X post (dry run) ---\n{text}\n------------------------\n")
        return
    try:
        resp = _client().create_tweet(text=text)
        log.info("Posted to X: tweet id %s", resp.data["id"])
    except Exception as e:  # noqa: BLE001 — posting must never break trading
        log.warning("X post failed (%s) — continuing", e)
