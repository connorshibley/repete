#!/bin/zsh
# Intraday swing opportunity scan — invoked by launchd
# (com.trading-agent.swingscan) every 30 min, 09:35-15:35 ET weekdays.
# Fails CLOSED on holidays/half-days via Broker.market_open, so wall-clock
# firings outside a session log one line and exit. A missed or skipped pass
# is harmless by design: there are twelve more that day, and the daily 15:45
# cycle remains the guaranteed path for swing entries at the close — which
# is why the watchdog does NOT monitor this job (alerting on skipped scans
# would be noise; the cycle it backs up is what the watchdog guards).
cd "$(dirname "$0")/.." || exit 1
echo "=== swing_scan $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/swing_scan.py >> logs/cron.log 2>&1
