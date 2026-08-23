#!/bin/zsh
# Retry a kill-switch flatten that did not complete.
# Invoked by launchd (com.repete.flattenretry) every 15 minutes during
# the session. Does NOTHING unless the kill switch left a pending marker in the
# HALT file, so the overwhelmingly common case costs one file check.
#
# It cannot open a position and it is not a new reason to sell: it only finishes
# a liquidation the daily-loss rail already started. Clearing HALT cancels it.
#
# Exit 1 means the attempt budget is spent and a human must close the book by
# hand — the alert says so too.
cd "$(dirname "$0")/.." || exit 1
out=$(.venv/bin/python src/flatten_recovery.py 2>&1)
rc=$?
# Only write to the log when something actually happened. This runs ~26 times a
# day; a "nothing to do" line each time would bury the ones that matter.
if [ -n "$out" ] && [ "$out" != "flatten recovery: not_pending" ]; then
  echo "=== flatten retry $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
  echo "$out" >> logs/cron.log
fi
exit $rc
