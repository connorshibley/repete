#!/bin/sh
# Backup the agent's state (Phase D, 2026-07-22).
#
# Everything that cannot be regenerated goes in: the append-only streams in
# memory/, the config, and the publisher's subscriber DB. .env is NOT backed
# up on purpose — secrets live in the keychain/console of each vendor and a
# backup archive must be safe to copy around.
#
#   scripts/backup.sh [dest_dir]     (default: backups/)
#
# Keeps the newest 14 archives. Verify restorability with:
#   python scripts/restore_drill.py
set -eu
# AGENT_ROOT override exists for the offline test fixture only.
cd "${AGENT_ROOT:-$(dirname "$0")/..}"

DEST="${1:-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

TARGETS="memory config.yaml"
[ -d publisher_data ] && TARGETS="$TARGETS publisher_data"

tar -czf "$DEST/agent-backup-$STAMP.tar.gz" $TARGETS
echo "wrote $DEST/agent-backup-$STAMP.tar.gz ($(du -h "$DEST/agent-backup-$STAMP.tar.gz" | cut -f1))"

# prune to the newest 14
ls -1t "$DEST"/agent-backup-*.tar.gz 2>/dev/null | tail -n +15 | while read -r old; do
  rm -f "$old"
  echo "pruned $old"
done
