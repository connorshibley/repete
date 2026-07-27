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


def _ledger_degradation(detail: str, cfg: dict):
    """Make a posting failure VISIBLE, not merely logged.

    Until 2026-07-27 a failed post wrote a `log.warning` and an archive row
    reading `"failed"` — and nothing else. No ledger event, so the watchdog
    could not see it, `evidence.py` did not count it, and the degradation SLO
    was blind to it.

    The cost was measured, not imagined: the four `X_*` values in `.env` were
    emptied some time after Friday 2026-07-24, and on 2026-07-27 the bot failed
    to post **ten** times — a recap of every trade it made that day — while
    reporting a clean cycle. The owner found out by asking. That is not a
    monitoring strategy.

    Best-effort by design, the same shape as `watchdog.main()`'s ledger write:
    an outbound-post problem must never become a trading problem, so a failure
    to RECORD the failure is logged and swallowed.
    """
    try:
        from ledger import Ledger
        path = (cfg.get("memory") or {}).get("ledger_path",
                                             "memory/ledger.jsonl")
        Ledger(path).log_event("degradation", detail[:500])
    except Exception as e:  # noqa: BLE001
        log.warning("could not ledger the X posting failure: %s", e)


CREDENTIALS = ("X_API_KEY", "X_API_SECRET",
               "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")


def missing_credentials() -> list[str]:
    """Which X credentials are absent OR present-but-empty.

    Empty is the case that actually happened. On 2026-07-27 all four names were
    still in `.env` with nothing after the `=`, so `os.environ[...]` returned
    `""` rather than raising, tweepy was handed blank keys, and every post
    failed with a generic auth error. "The variable is set" and "the variable
    has a value" are different facts — the same distinction preflight now draws
    for the Anthropic key.
    """
    return [k for k in CREDENTIALS if not (os.environ.get(k) or "").strip()]


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
    if xc.get("disclose_paper", True) and not text.startswith("[PAPER]"):
        text = "[PAPER] " + text  # the literal tag, always — prose mentions
        # of "paper" don't count as the machine-readable disclosure
    if link:
        text = text[:275 - 24] + "\n" + link  # 23 (t.co) + newline
    else:
        text = text[:275]

    if xc.get("dry_run", True):
        log.info("X DRY RUN (not posted): %s", text)
        print(f"\n--- X post (dry run) ---\n{text}\n------------------------\n")
        _archive(text, link, "dry_run", cfg)
        return
    absent = missing_credentials()
    if absent:
        # Distinct from a publish failure, and it needs a different fix. Said
        # precisely so the ledger names the four variables rather than echoing
        # a generic auth error 10 times.
        log.warning("X post skipped — %s empty or unset", ", ".join(absent))
        _archive(text, link, "no_credentials", cfg)
        _ledger_degradation(
            f"x_post: not published — {', '.join(absent)} empty or unset in "
            f"the environment. Posting is configured ON (x_posting.enabled, "
            f"dry_run: false), so this is a misconfiguration, not a choice.",
            cfg)
        return
    try:
        resp = _client().create_tweet(text=text)
        log.info("Posted to X: tweet id %s", resp.data["id"])
        _archive(text, link, "posted", cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("X post failed (%s) — continuing", e)
        _archive(text, link, "failed", cfg)
        _ledger_degradation(f"x_post: publish failed ({type(e).__name__}: "
                            f"{str(e)[:160]}) — the post was NOT published", cfg)


def post_recap(trade: dict, cfg: dict, llm_draft: str | None = None,
               link: str | None = None):
    if not cfg["x_posting"]["enabled"]:
        return
    post_text(llm_draft or _template_post(trade, cfg), cfg, link=link)
