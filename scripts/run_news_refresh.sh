#!/bin/zsh
# Hourly market-context refresh (Haiku distillation) — invoked by launchd
# (com.repete.newsbrain) at :25 past each hour, 9:25-15:25 weekdays.
cd "$(dirname "$0")/.." || exit 1
echo "=== news_refresh $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/market_context.py >> logs/cron.log 2>&1
# Late plan post if the 9:35 launchd slot was missed (Mac asleep/off);
# exits instantly on every other hour. Never blocks the refresh result.
.venv/bin/python src/daily_posts.py --catchup >> logs/cron.log 2>&1
exit 0
