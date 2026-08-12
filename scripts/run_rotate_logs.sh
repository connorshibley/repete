#!/bin/zsh
# Daily log rotation — invoked by launchd (com.trading-agent.logrotate).
#
# Every day, not just weekdays: a runaway has no market dependency. The whole
# point of this job is the crash loop that fills a disk on a Saturday.
#
# It writes its own header into cron.log, which is one of the files it rotates.
# That is fine and deliberate — copytruncate keeps the inode, so this script's
# own open descriptor survives its own rotation.
cd "$(dirname "$0")/.." || exit 1
echo "=== rotate logs $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
sh scripts/rotate_logs.sh >> logs/cron.log 2>&1
STATUS=$?
if [ $STATUS -ne 0 ]; then
  echo "log rotation FAILED with status $STATUS" >> logs/cron.log
  .venv/bin/python -c "
import sys
sys.path.insert(0, 'src')
import alerting
alerting.send('Repete log rotation failed',
              'scripts/rotate_logs.sh exited $STATUS. Logs are unbounded until '
              'this is fixed. See logs/cron.log.')
" >> logs/cron.log 2>&1
fi
exit $STATUS
