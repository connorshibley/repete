#!/bin/sh
# One-command deploy for the VPS / docker-compose path (Phase E ops, 2026-07-23).
#
#   sh deploy/deploy.sh            build, start, verify
#   sh deploy/deploy.sh --publisher   also start the subscriber site
#
# Refuses to run half-configured rather than starting a bot that cannot trade.
set -eu

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
PROFILE=""
[ "${1:-}" = "--publisher" ] && PROFILE="--profile publisher"

say() { printf '\n== %s\n' "$1"; }
fail() { printf 'DEPLOY ABORTED: %s\n' "$1" >&2; exit 1; }

say "preflight"
[ -f .env ] || fail ".env missing — cp .env.example .env and fill it in (see deploy/SECRETS.md)"
for key in ALPACA_API_KEY ALPACA_SECRET_KEY; do
  grep -qE "^${key}=.+" .env || fail "$key is empty in .env — the agent cannot trade without it"
done
# Loud, not fatal: the interlock is config + env, and this only reports.
if grep -qE '^\s*mode:\s*live' config.yaml; then
  printf 'WARNING: config.yaml says mode: live. Live also needs\n'
  printf '         LIVE_TRADING_CONFIRMED=YES. Walk docs/go_live_checklist.md first.\n'
fi
chmod 600 .env 2>/dev/null || true

say "state directories (must outlive every redeploy)"
mkdir -p memory logs backups
printf '  memory/  %s files\n' "$(ls -1 memory 2>/dev/null | wc -l | tr -d ' ')"
if [ ! -s memory/ledger.jsonl ] && [ ! -s memory/agent.db ]; then
  printf '  NOTE: no existing ledger found. If you meant to carry the track\n'
  printf '        record over from another host, copy memory/ FIRST — see\n'
  printf '        "Migrating your existing state" in deploy/README.md.\n'
fi

say "build"
# Stamp the running commit into the image. Without it the deployed bot cannot
# say which build is trading, and §26 divergence #7 (production 57 commits
# stale for three days, unnoticed) stays undetectable from inside a container.
GIT_SHA="$(git rev-parse HEAD 2>/dev/null || echo '')"
export GIT_SHA
if [ -n "$GIT_SHA" ]; then
  printf '  stamping build %s\n' "$(printf '%s' "$GIT_SHA" | cut -c1-12)"
  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    printf '  WARNING: working tree is dirty — the image will be stamped with a\n'
    printf '           commit it does not actually match.\n'
  fi
else
  printf '  NOTE: no git sha available; the drift guard degrades to config-drift\n'
  printf '        only (which still catches a stale config.yaml).\n'
fi
# Runs the container as this host's own uid/gid instead of the image's
# baked-in agent (10001) -- lets it read a host-mounted deploy_key without
# that key needing to be world-readable. docker-compose.yml defaults to
# 10001:10001 (the image's original user) when these are unset, so a host
# that doesn't need this (no deploy_key mount) is unaffected.
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
export HOST_UID HOST_GID
docker compose build

say "start"
# shellcheck disable=SC2086
docker compose $PROFILE up -d

say "verify"
sleep 5
docker compose ps
printf '\n-- scheduler boot line --\n'
docker compose logs --tail 20 agent | grep -i "scheduler up" \
  || fail "scheduler did not report 'scheduler up' — check: docker compose logs agent"
printf '\n-- health --\n'
docker compose run --rm agent python src/health.py || true

cat <<'EOF'

Deployed. What to check next:
  * after the first weekday 15:45 ET cycle, memory/heartbeat should be fresh
    (a stale heartbeat is what "silently dead" looks like — see HEARTBEAT.md)
  * after 17:00 ET, backups/ should hold a dated archive
  * prove it restores:  docker compose run --rm agent python scripts/restore_drill.py

To stop trading immediately at any point:   touch HALT
EOF
