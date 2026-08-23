#!/bin/zsh
# Weekly restore drill — invoked by launchd (com.repete.restoredrill).
#
# A backup that has never been restored is a hope, not a backup. This unpacks
# the newest archive into a throwaway directory and verifies every stream
# parses and every record count matches the manifest. Read-only against live
# state. Non-zero exit on failure, which lands in logs/cron.log.
cd "$(dirname "$0")/.." || exit 1
echo "=== restore drill $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python scripts/restore_drill.py >> logs/cron.log 2>&1
