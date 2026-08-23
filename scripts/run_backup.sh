#!/bin/zsh
# Nightly state backup — invoked by launchd (com.repete.backup).
#
# This job existed only in scripts/scheduler.py (the container path) until
# 2026-07-27. The container has never run on this host, so despite the
# go-live checklist claiming "backups scheduled", exactly one backup existed
# and it was taken by hand. A backup that is scheduled somewhere nothing runs
# is not a backup.
#
# ALERT ON FAILURE (2026-08-06). Until today `backup.sh` was the last statement
# here, so its exit status became launchd's and went nowhere: a backup that
# started failing — a full disk, an iCloud permission change, a moved directory
# — would have been silent until someone read cron.log. The whole point of this
# job is the case where nobody is reading anything.
cd "$(dirname "$0")/.." || exit 1
echo "=== backup $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
sh scripts/backup.sh >> logs/cron.log 2>&1
STATUS=$?
if [ $STATUS -ne 0 ]; then
  echo "backup FAILED with status $STATUS" >> logs/cron.log
  .venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
import alerting
alerting.send('Repete backup failed',
              'scripts/backup.sh exited $STATUS. The off-host mirror may be '
              'stale. See logs/cron.log.')
" >> logs/cron.log 2>&1
fi
exit $STATUS
