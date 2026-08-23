#!/bin/zsh
# Weekly learning consolidation (evaluator backlog + counterfactuals + one
# meta-merge pass) — invoked by launchd (com.repete.learn).
cd "$(dirname "$0")/.." || exit 1
echo "=== learn $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python src/learn.py --meta >> logs/cron.log 2>&1
.venv/bin/python src/review.py >> logs/cron.log 2>&1
