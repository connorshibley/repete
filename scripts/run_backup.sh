#!/bin/zsh
# Nightly state backup — invoked by launchd (com.trading-agent.backup).
#
# This job existed only in scripts/scheduler.py (the container path) until
# 2026-07-27. The container has never run on this host, so despite the
# go-live checklist claiming "backups scheduled", exactly one backup existed
# and it was taken by hand. A backup that is scheduled somewhere nothing runs
# is not a backup.
cd "$(dirname "$0")/.." || exit 1
echo "=== backup $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
sh scripts/backup.sh >> logs/cron.log 2>&1
