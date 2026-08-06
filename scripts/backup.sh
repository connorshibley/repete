#!/bin/sh
# Backup the agent's state (Phase D, 2026-07-22; off-host mirror 2026-08-06).
#
# Everything that cannot be regenerated goes in: the append-only streams in
# memory/, the config, and the publisher's subscriber DB. .env is NOT backed
# up on purpose — secrets live in the keychain/console of each vendor and a
# backup archive must be safe to copy around.
#
#   scripts/backup.sh [dest_dir]     (default: backups/)
#
# Keeps the newest 14 locally, 30 off-host. Verify restorability with:
#   python scripts/restore_drill.py
#
# THE OFF-HOST MIRROR (2026-08-06)
# --------------------------------
# Until today every archive lived on the same APFS volume as the thing it was
# protecting, which guards against `rm -rf memory/` and against nothing else.
# `memory/ledger.jsonl` is the ONLY copy of the live record — the closed trades
# the decay monitor is counting toward n=20. Losing that disk resets a clock
# that no amount of work winds forward, because the only input is time.
#
# repete2 reached this conclusion first and its script says it better
# (`~/bots/repete2/scripts/backup.sh:21-24`): "A local `backups/` directory does
# not fix that either — it dies with the same disk. The default here is iCloud
# Drive when it exists, because it is the only durable target this machine is
# known to have."
#
# One difference here: this MIRRORS rather than relocates. `backups/` stays the
# primary, because the runbook's restore recipe, the CI smoke step, the compose
# volume and every existing test name that path. Off-host is an ADDITION, so
# nothing that worked yesterday stops working today.
#
# REPETE_OFFHOST_DIR overrides the destination; set it to the empty string to
# skip the mirror. The test suite sets it, so no unit test can ever write into
# real iCloud.
set -eu
# AGENT_ROOT override exists for the offline test fixture only.
cd "${AGENT_ROOT:-$(dirname "$0")/..}"

# macOS `tar` otherwise writes an AppleDouble sidecar (`._ledger.jsonl`, 163
# bytes) beside every file carrying an extended attribute. Python's
# `extractall(filter="data")` drops them, so the drill never saw them — but the
# runbook's manual restore uses system `tar`, which would materialise eight of
# them straight into live memory/.
COPYFILE_DISABLE=1
export COPYFILE_DISABLE

DEST="${1:-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

TARGETS="memory config.yaml"
[ -d publisher_data ] && TARGETS="$TARGETS publisher_data"

# sha256sum on Linux/CI, shasum on macOS. Both exist on this laptop; only the
# first is guaranteed on the Ubuntu runner.
_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# Manifest: per-stream record counts AND content hashes AT BACKUP TIME. The
# restore drill compares the extracted archive against these.
#
# Counts catch a cleanly truncated archive — counting against live cannot, since
# an archive legitimately has fewer records than live. Counts do NOT catch a
# corruption that preserves line count and JSON validity: one flipped byte
# inside a string value leaves the count identical and every line parseable.
# That is what the hashes are for. Added 2026-08-06 after noticing the manifest
# had shipped for two weeks with no integrity field of any kind.
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
  printf '},"sha256":{'
  first=1
  for f in memory/*.jsonl; do
    [ -e "$f" ] || continue
    [ $first -eq 1 ] || printf ','
    printf '"%s":"%s"' "$(basename "$f")" "$(_sha256 "$f")"
    first=0
  done
  printf '}}'
} > "$MANIFEST_DIR/backup_manifest.json"

ARCHIVE="$DEST/agent-backup-$STAMP.tar.gz"
tar -czf "$ARCHIVE" $TARGETS -C "$MANIFEST_DIR" backup_manifest.json
echo "wrote $ARCHIVE ($(du -h "$ARCHIVE" | cut -f1))"

# prune to the newest 14
ls -1t "$DEST"/agent-backup-*.tar.gz 2>/dev/null | tail -n +15 | while read -r old; do
  rm -f "$old"
  echo "pruned $old"
done

# ---- off-host mirror -------------------------------------------------------
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/trading-agent-backups"
if [ "${REPETE_OFFHOST_DIR+set}" = set ]; then
  OFFHOST="$REPETE_OFFHOST_DIR"          # explicit, including "" for disabled
elif [ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]; then
  OFFHOST="$ICLOUD"
else
  OFFHOST=""
  echo "warning: no iCloud Drive found; $DEST is on the same disk as the thing" >&2
  echo "         it protects. Set REPETE_OFFHOST_DIR to a real target." >&2
fi

if [ -n "$OFFHOST" ]; then
  mkdir -p "$OFFHOST"
  MIRROR="$OFFHOST/$(basename "$ARCHIVE")"
  cp "$ARCHIVE" "$MIRROR" || {
    echo "ERROR: off-host copy to $OFFHOST failed" >&2
    exit 1
  }
  # Verify the copy rather than trusting cp's exit status. An interrupted
  # iCloud write can leave a short file behind, and a truncated backup that
  # reports success is worse than no backup at all.
  if [ "$(_sha256 "$ARCHIVE")" != "$(_sha256 "$MIRROR")" ]; then
    echo "ERROR: off-host copy differs from the local archive: $MIRROR" >&2
    exit 1
  fi
  echo "mirrored $MIRROR"
  # Keep more off-host than locally: it is the durable copy, and that storage
  # is not the one under pressure.
  ls -1t "$OFFHOST"/agent-backup-*.tar.gz 2>/dev/null | tail -n +31 | while read -r old; do
    rm -f "$old"
    echo "pruned off-host $old"
  done
fi
