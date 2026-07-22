#!/bin/zsh
# Publish dashboard.html + journal.html to the public GitHub Pages repo
# (.site/ checkout). Called by run_cycle.sh and run_daily_post.sh after they
# regenerate content. ALWAYS exits 0 — a publish failure must never break a
# trading or posting job.
cd "$(dirname "$0")/.." || exit 0
[ -d .site/.git ] || exit 0

changed=0
# index.html = the cream/orange landing page; the dark terminal lives at
# dash.html (structure change 2026-07-21).
if [ -f landing.html ] && ! cmp -s landing.html .site/index.html; then
  cp landing.html .site/index.html
  git -C .site add index.html
  changed=1
fi
if [ -f dashboard.html ] && ! cmp -s dashboard.html .site/dash.html; then
  cp dashboard.html .site/dash.html
  git -C .site add dash.html
  changed=1
fi
if [ -f journal.html ] && ! cmp -s journal.html .site/journal.html; then
  cp journal.html .site/journal.html
  git -C .site add journal.html
  changed=1
fi
if [ -f blog.html ] && ! cmp -s blog.html .site/blog.html; then
  cp blog.html .site/blog.html
  git -C .site add blog.html
  changed=1
fi

if [ "$changed" -eq 1 ]; then
  git -C .site commit -q -m "site update $(date -u +%Y-%m-%dT%H:%MZ)" \
    && git -C .site push -q \
    && echo "site published $(date -u +%H:%MZ)" >> logs/cron.log \
    || echo "site publish FAILED $(date -u +%H:%MZ)" >> logs/cron.log
fi
exit 0
