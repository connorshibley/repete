#!/bin/zsh
# Daily paper-trading cycle — invoked by launchd (com.repete.cycle).
# Runs after market close; weekends/holidays just log holds harmlessly.
cd "$(dirname "$0")/.." || exit 1
echo "=== cycle $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/main.py >> logs/cron.log 2>&1
"$(dirname "$0")/publish_dashboard.sh"
