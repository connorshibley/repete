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

# Manifest: per-stream record counts AT BACKUP TIME. The restore drill compares
# the extracted archive against these, which is what makes a cleanly truncated
# archive detectable — counting alone cannot, since an archive legitimately has
# fewer records than live.
MANIFEST_DIR="$(mktemp -d)"
trap 'rm -rf "$MANIFEST_DIR"' EXIT
{
  printf '{"created":"%s","streams":{' "$STAMP"
  first=1
  for f in memory/*.jsonl; do
    [ -e "$f" ] || continue
    n=$(grep -c . "$f" 2>/dev/null || echo 0)
    [ $first -eq 1 ] || printf ','
    printf '"%s":%s' "$(basename "$f")" "$n"
    first=0
  done
  printf '}}'
} > "$MANIFEST_DIR/backup_manifest.json"

tar -czf "$DEST/agent-backup-$STAMP.tar.gz" $TARGETS \
    -C "$MANIFEST_DIR" backup_manifest.json
echo "wrote $DEST/agent-backup-$STAMP.tar.gz ($(du -h "$DEST/agent-backup-$STAMP.tar.gz" | cut -f1))"

# prune to the newest 14
ls -1t "$DEST"/agent-backup-*.tar.gz 2>/dev/null | tail -n +15 | while read -r old; do
  rm -f "$old"
  echo "pruned $old"
done
