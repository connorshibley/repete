#!/bin/zsh
# Hourly market-context refresh (Haiku distillation) — invoked by launchd
# (com.trading-agent.newsbrain) at :25 past each hour, 9:25-15:25 weekdays.
cd "$(dirname "$0")/.." || exit 1
echo "=== news_refresh $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/market_context.py >> logs/cron.log 2>&1
