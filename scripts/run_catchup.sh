#!/bin/zsh
# Late catch-up: if the 15:45 cycle was missed (machine asleep) and the
# market is still open, run it now — invoked by launchd at 15:55 weekdays
# (com.trading-agent.catchup). No-op when the cycle already ran today.
cd "$(dirname "$0")/.." || exit 1
echo "=== catchup $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/watchdog.py --catchup >> logs/cron.log 2>&1
