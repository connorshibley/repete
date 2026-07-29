#!/bin/zsh
# Publish dashboard.html + journal.html to the public GitHub Pages repo
# (.site/ checkout). Called by run_cycle.sh and run_daily_post.sh after they
# regenerate content. ALWAYS exits 0 — a publish failure must never break a
# trading or posting job.
cd "$(dirname "$0")/.." || exit 0
[ -d .site/.git ] || exit 0

# REFUSE TO PUBLISH AN ARTIFACT THAT DISAGREES WITH THE RECORD.
# This script's only test was `cmp` — "does it differ from what's live?" — and
# difference is not improvement. On 2026-07-28 the repo-root blog.html held 0
# posts against 33 in memory/posts.jsonl and journal.html held 1 of 19, both
# written by a pytest run whose CWD was the repo root. cmp said "differs", and
# without this check the next cycle would have pushed them over an eleven-day
# public archive. src/sitepaths.py fixed the cause; this is the independent
# second line, because the failure is invisible to git (the artifacts are
# gitignored, so deploycheck cannot see them either).
# Still exits 0: a refusal must never break a trading or posting job.
PY="$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)"
if ! "$PY" src/artifactcheck.py >/dev/null 2>>logs/cron.log; then
  echo "site publish REFUSED $(date -u +%H:%MZ) — artifact disagrees with the record" \
    >> logs/cron.log
  exit 0
fi

changed=0
if [ -f dashboard.html ] && ! cmp -s dashboard.html .site/index.html; then
  cp dashboard.html .site/index.html
  git -C .site add index.html
  changed=1
fi
# dashboard_data.json is what the published page POLLS. Without it the live
# badge on the deployed site drifts amber then red while the HTML beside it is
# perfectly current — the page would look broken precisely BECAUSE the
# freshness indicator works. It has to ship on the same commit as index.html.
if [ -f dashboard_data.json ] && ! cmp -s dashboard_data.json .site/dashboard_data.json; then
  cp dashboard_data.json .site/dashboard_data.json
  git -C .site add dashboard_data.json
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
