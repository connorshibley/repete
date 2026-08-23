#!/bin/zsh
# Morning plan (before noon local) or evening review (after) X post —
# invoked by launchd (com.repete.dailypost) at 09:35 and 16:20.
cd "$(dirname "$0")/.." || exit 1
if [ "$(date +%H)" -lt 12 ]; then MODE="--plan"; else MODE="--review"; fi
echo "=== daily_post $MODE $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/daily_posts.py $MODE >> logs/cron.log 2>&1
"$(dirname "$0")/publish_dashboard.sh"
