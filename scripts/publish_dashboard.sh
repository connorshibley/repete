#!/bin/zsh
# Publish dashboard.html to the public GitHub Pages repo (.site/ checkout).
# Called by run_cycle.sh and run_daily_post.sh after they regenerate the
# dashboard. ALWAYS exits 0 — a publish failure must never break a trading
# or posting job.
cd "$(dirname "$0")/.." || exit 0
[ -f dashboard.html ] || exit 0
[ -d .site/.git ] || exit 0

if ! cmp -s dashboard.html .site/index.html; then
  cp dashboard.html .site/index.html
  git -C .site add index.html
  git -C .site commit -q -m "dashboard update $(date -u +%Y-%m-%dT%H:%MZ)" \
    && git -C .site push -q \
    && echo "dashboard published $(date -u +%H:%MZ)" >> logs/cron.log \
    || echo "dashboard publish FAILED $(date -u +%H:%MZ)" >> logs/cron.log
fi
exit 0
