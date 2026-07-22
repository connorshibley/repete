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
JOBS = [
    ("news-brain",   range(0, 5), None, 25, [PY, "src/market_context.py"]),
    ("plan-post",    range(0, 5), 9,    35, [PY, "src/daily_posts.py", "plan"]),
    ("cycle",        range(0, 5), 15,   45, [PY, "src/main.py"]),
    ("catch-up",     range(0, 5), 15,   55, [PY, "src/watchdog.py", "--catchup"]),
    ("watchdog",     range(0, 5), 16,   15, [PY, "src/watchdog.py"]),
    ("review-post",  range(0, 5), 16,   20, [PY, "src/daily_posts.py", "review"]),
    ("weekly-learn", [6],         18,   0,  [PY, "src/learn.py", "--meta"]),
    # Phase D: state backup nightly after the cycle; restore drill weekly —
    # a backup that has never been restored is a hope, not a backup.
    ("backup",       range(0, 5), 17,   0,  ["sh", "scripts/backup.sh"]),
    ("restore-drill", [5],        10,   0,  [PY, "scripts/restore_drill.py"]),
]
# news-brain runs hourly at :25 between these ET hours (market-day awareness)
NEWS_HOURS = range(9, 16)


def due(job, now: datetime) -> bool:
    name, weekdays, hour, minute, _ = job
    if now.weekday() not in weekdays or now.minute != minute:
        return False
    if hour is None:                      # hourly window job (news-brain)
        return now.hour in NEWS_HOURS
    return now.hour == hour


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
