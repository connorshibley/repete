#!/bin/zsh
# Dead-man watchdog: alerts if today's trading cycle didn't run or HALT is
# engaged — invoked by launchd (com.trading-agent.watchdog) after the cycle.
cd "$(dirname "$0")/.." || exit 1
echo "=== watchdog $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/watchdog.py >> logs/cron.log 2>&1
