#!/usr/bin/env python3
"""Container-native scheduler — the launchd replacement (2026-07-22).

launchd tied the whole product to one Mac staying awake. This runs the same
jobs at the same America/New_York times inside a container that never sleeps.
Dependency-light on purpose (stdlib only, matching project convention): a
one-minute tick, each job fired at most once per scheduled minute.

Every job runs in a subprocess, so one failure can never take down the
scheduler — the trading cycle keeps its own error handling.
"""
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s scheduler %(levelname)s: %(message)s")
log = logging.getLogger("scheduler")

# (name, weekdays, hour, minute, argv) — weekdays: 0=Mon … 6=Sun
# The content jobs (cycle, plan/review posts) chain scripts/publish_dashboard.sh
# so the container publishes dashboard.html/journal.html to GitHub Pages the same
# way the laptop's run_cycle.sh does. publish_dashboard.sh is idempotent and
# no-ops (exit 0) when no .site/.git checkout is mounted, so this degrades
# cleanly on a container without publishing configured. `&&` ties publish to a
# clean job completion; main.py keeps its own failure logging.
_PUBLISH = "sh scripts/publish_dashboard.sh"
# ET hours in which the book is re-marked, at :30. Starts at 10 because the
# 09:35 open cycle has just marked, ends at 15 because the 15:45 cycle marks
# again — six extra read-only broker calls per session, no gaps over an hour.
MARK_HOURS = range(10, 16)
# flatten-retry: every quarter hour across the session. Both must be defined
# BEFORE JOBS, which references them in its literal.
RETRY_HOURS = range(9, 16)
RETRY_MINUTES = (0, 15, 30, 45)
# swing-scan: every 30 min at :05/:35 across the session, matching
# com.repete.swingscan.plist entry for entry. The 9:05 firing is
# PRE-OPEN on purpose: Broker.market_open() fails closed, so it costs one
# clock read and proves the closed-market guard every trading day.
SCAN_HOURS = range(9, 16)
SCAN_MINUTES = (5, 35)
JOBS = [
    ("news-brain",   range(0, 5), None, 25, [PY, "src/market_context.py"]),
    ("plan-post",    range(0, 5), 9,    35,
     ["sh", "-c", f"{PY} src/daily_posts.py plan && {_PUBLISH}"]),
    # 09:35 ET open cycle (2026-07-23). Entries that were true at yesterday's
    # close used to wait until 15:45 today; this acts on them ~6 hours sooner.
    # --open-cycle drops today's still-forming bar, so signals come from the
    # last COMPLETED daily bar — which is precisely the backtester's model
    # (signal on close of bar i, fill at open of bar i+1). Same rails.
    ("open-cycle",   range(0, 5), 9,    35,
     ["sh", "-c", f"{PY} src/main.py --open-cycle && {_PUBLISH}"]),
    # 12:00 ET midday look. ALERTS ONLY — it reads the still-forming bar and
    # reports what is setting up, hours before the close. It cannot place an
    # order (src/opportunity_scan.py holds no order call path; a test pins it).
    # Trading decisions stay on completed bars, because partial-bar inputs have
    # never been through a gate (§19a declined exactly that).
    ("midday-scan",  range(0, 5), 12,   0,  [PY, "src/opportunity_scan.py"]),
    # Hourly at :30 through the session. MARKS ONLY — it reads the open book
    # and writes one positions_mark so the dashboard's value and unrealized ±%
    # track the market. It cannot trade (src/mark_positions.py holds no order
    # call path; a test pins it), and it publishes so the deployed page moves
    # too.
    #
    # Why it exists: until 2026-07-26 the ONLY writers of positions_mark were
    # the trading cycle and the post job, so between 09:35 and 15:45 the
    # dashboard showed morning prices for six hours, and over a weekend it
    # showed Friday's close — under a heading that gave a reader no reason to
    # doubt them. The live mark measured 20.9 hours old.
    ("mark-book",    range(0, 5), MARK_HOURS, 30,
     ["sh", "-c", f"{PY} src/mark_positions.py && {PY} src/dashboard.py "
                  f"&& {_PUBLISH}"]),
    ("cycle",        range(0, 5), 15,   45,
     ["sh", "-c", f"{PY} src/main.py && {_PUBLISH}"]),
    ("catch-up",     range(0, 5), 15,   55, [PY, "src/watchdog.py", "--catchup"]),
    ("watchdog",     range(0, 5), 16,   15, [PY, "src/watchdog.py"]),
    ("review-post",  range(0, 5), 16,   20,
     ["sh", "-c", f"{PY} src/daily_posts.py review && {_PUBLISH}"]),
    # Friday 17:30 ET, matching com.repete.learn.plist. These two
    # surfaces disagreed (launchd Friday 17:30 + review.py; container Sunday
    # 18:00, no review), so the weekly report existed only on the laptop.
    # weekdays are 0=Mon..6=Sun, so 4 = Friday.
    ("weekly-learn", [4],         17,   30,
     ["sh", "-c", f"{PY} src/learn.py --meta && {PY} src/review.py"]),
    # Phase D: state backup on WEEKDAYS after the cycle — range(0, 5) is Mon-Fri.
    # Said "nightly" until 2026-07-29 (W5-6); there is no weekend backup, which
    # is deliberate because the book does not move. Restore drill weekly:
    # a backup that has never been restored is a hope, not a backup.
    ("backup",       range(0, 5), 17,   0,  ["sh", "scripts/backup.sh"]),
    ("restore-drill", [5],        10,   0,  [PY, "scripts/restore_drill.py"]),
    # Weekend liveness heartbeat (2026-08-23). Every publish-chaining job above
    # is Mon-Fri, so the site's feed goes untouched from Friday 16:20 to Monday
    # 09:35 — 65 hours in which a healthy system and a publisher that died on
    # Friday afternoon are INDISTINGUISHABLE. The fleet console shows both as
    # "published 25h ago" with an amber dot, and no amount of squinting
    # separates them.
    #
    # This does not trade and does not touch the ledger. It RE-RENDERS and
    # publishes: publish_dashboard.sh diffs every file before committing, so
    # publishing without rendering first finds nothing changed and pushes
    # nothing, which would make this job a silent no-op — the failure it is
    # here to prevent, wearing the costume of the fix.
    #
    # The page it pushes still says Friday, honestly: `data_at` in the feed
    # carries the age of the DATA while `generated_at` carries the age of the
    # render. Restamping alone would just make a stale page claim to be fresh.
    ("weekend-publish", [5, 6],   12,   0,
     ["sh", "-c", f"{PY} src/dashboard.py && sh scripts/publish_dashboard.sh"]),
    # Log rotation (2026-08-06). EVERY day, unlike backup: what this guards is
    # a crash loop filling a disk, and that does not wait for a session. It
    # belongs here as well as on launchd because docker-compose bind-mounts
    # ./logs, so the container writes the same agent.log and agent.jsonl to a
    # real volume. cron.log has no container equivalent — scheduler.py logs to
    # stdout — and rotate_logs.sh simply skips files that are not there.
    ("logrotate",    range(0, 7), 17,   5,  ["sh", "scripts/rotate_logs.sh"]),
    # §47 random-entry decay monitor. Sunday 11:30 ET, matching
    # com.repete.decaycheck.plist — after the week's trading is closed
    # out and before the next week opens. ALERT-ONLY: it cannot halt trading
    # (tests/test_decaycheck.py walks the AST to keep it that way), so the
    # worst a spurious fire costs is one notification.
    ("decaycheck",   [6],         11,   30, [PY, "src/decaycheck.py", "--alert"]),
    # Retry a kill-switch flatten that did not complete (2026-08-02). Fires
    # every 15 minutes through the session; the WINDOW ITSELF lives in
    # flatten_recovery.within_retry_window, not here, so this scheduler and the
    # launchd plist cannot drift from each other or from the code. A run with
    # nothing pending is one substring check on a file that usually does not
    # exist, which is why it can afford to fire this often.
    ("flatten-retry", range(0, 5), RETRY_HOURS, RETRY_MINUTES,
     [PY, "src/flatten_recovery.py"]),
    # Intraday swing opportunity scan (2026-08-11, owner: act on opportunity,
    # not the clock). AT MOST one gated long per pass, and only when a live
    # quote sits inside a zone precomputed from COMPLETED daily bars — the
    # conditions live in strategies/swing_sectors.assess(), the pipeline in
    # src/swing_scan.py, and §19a stands (no forming-bar inputs anywhere).
    # Ships inert: swing_sectors is enabled: false until §62, so every pass
    # is a dry run that ledgers candidates and places nothing.
    ("swingscan", range(0, 5), SCAN_HOURS, SCAN_MINUTES,
     [PY, "src/swing_scan.py"]),
]
# news-brain runs hourly at :25 between these ET hours (market-day awareness)
NEWS_HOURS = range(9, 16)


def due(job, now: datetime) -> bool:
    name, weekdays, hour, minute, _ = job
    if now.weekday() not in weekdays:
        return False
    # `minute` accepts a collection as well as an int, exactly as `hour` below
    # already did (MARK_HOURS). Added for flatten-retry, which is the first job
    # that needs to fire more than once an hour.
    if isinstance(minute, int):
        if now.minute != minute:
            return False
    elif now.minute not in minute:
        return False
    if hour is None:                      # hourly window job (news-brain)
        return now.hour in NEWS_HOURS
    if isinstance(hour, int):
        return now.hour == hour
    return now.hour in hour               # explicit hour window (mark-book)


def run(job):
    name, *_, argv = job
    log.info("running %s: %s", name, " ".join(argv))
    try:
        r = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode != 0:
            log.error("%s exited %s: %s", name, r.returncode,
                      (r.stderr or "")[-800:])
        else:
            log.info("%s ok", name)
    except Exception as e:  # noqa: BLE001 — a job must never kill the scheduler
        log.error("%s failed to run: %s", name, e)


def main():
    log.info("scheduler up — %d jobs, timezone %s", len(JOBS), ET)
    fired: set[tuple[str, str]] = set()
    while True:
        now = datetime.now(ET)
        stamp = now.strftime("%Y-%m-%dT%H:%M")
        for job in JOBS:
            key = (job[0], stamp)
            if key not in fired and due(job, now):
                fired.add(key)
                run(job)
        if len(fired) > 500:              # bound the dedupe set
            fired = {k for k in fired if k[1] >= stamp[:10]}
        time.sleep(20)


if __name__ == "__main__":
    main()
