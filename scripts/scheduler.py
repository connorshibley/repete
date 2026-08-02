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
    # Friday 17:30 ET, matching com.trading-agent.learn.plist. These two
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
    # §47 random-entry decay monitor. Sunday 11:30 ET, matching
    # com.trading-agent.decaycheck.plist — after the week's trading is closed
    # out and before the next week opens. ALERT-ONLY: it cannot halt trading
    # (tests/test_decaycheck.py walks the AST to keep it that way), so the
    # worst a spurious fire costs is one notification.
    ("decaycheck",   [6],         11,   30, [PY, "src/decaycheck.py", "--alert"]),
]
# news-brain runs hourly at :25 between these ET hours (market-day awareness)
NEWS_HOURS = range(9, 16)


def due(job, now: datetime) -> bool:
    name, weekdays, hour, minute, _ = job
    if now.weekday() not in weekdays or now.minute != minute:
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
