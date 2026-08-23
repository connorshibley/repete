#!/bin/zsh
# Weekly random-entry decay check: has the live edge fallen to noise?
# Invoked by launchd (com.repete.decaycheck), Sundays — after the week's
# trading is done and before the next week starts.
#
# ALERT-ONLY. This never halts trading; it emails/pushes and exits. Exit 1 means
# the verdict was WORSE_THAN_RANDOM, exit 2 means the check could not run at all
# (which is NOT a pass — see src/decaycheck.py).
cd "$(dirname "$0")/.." || exit 1
echo "=== decay check $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/decaycheck.py --alert >> logs/cron.log 2>&1
rc=$?
if [ $rc -eq 2 ]; then
  echo "decay check COULD NOT RUN (exit 2) — not a pass" >> logs/cron.log
fi
exit 0   # never fail the launchd job on a verdict; the alert is the signal
