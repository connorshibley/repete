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

# B2 stores SHA1 natively, so that is the hash the remote can be asked for.
# Same macOS/Linux split as above — `sha1sum` is coreutils and is NOT on a Mac,
# and this script runs on both.
_sha1() {
  if command -v sha1sum >/dev/null 2>&1; then
    sha1sum "$1" | cut -d' ' -f1
  else
    shasum -a 1 "$1" | cut -d' ' -f1
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

# ---- off-host mirror, remote (2026-08-23) ----------------------------------
# The Bizon has ONE disk. `backups/` there is on the same NVMe as memory/, so
# it protects against `rm -rf memory` and against nothing else. This branch is
# the only one that survives the disk.
#
# It runs inside the container, which is why rclone and gnupg are installed in
# the image rather than on the host.
#
# Encrypt FIRST, upload second. The archive holds memory/ (the entire ledger,
# every decision and every judge prompt) and config.yaml (every strategy
# parameter and rail threshold). No credentials — checked — but not material to
# hand a third party in the clear either.
REMOTE="${REPETE_MIRROR_REMOTE:-}"
MIRRORED_REMOTE=""
if [ -n "$REMOTE" ]; then
  if [ -z "${REPETE_BACKUP_PASSPHRASE:-}" ]; then
    echo "ERROR: REPETE_MIRROR_REMOTE is set but REPETE_BACKUP_PASSPHRASE is not." >&2
    echo "       Refusing to upload the ledger unencrypted. Set both or neither." >&2
    exit 1
  fi
  command -v rclone >/dev/null 2>&1 || { echo "ERROR: rclone not found in PATH" >&2; exit 1; }
  command -v gpg    >/dev/null 2>&1 || { echo "ERROR: gpg not found in PATH" >&2; exit 1; }

  ENC="$ARCHIVE.gpg"
  printf '%s' "$REPETE_BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
      --passphrase-fd 0 --pinentry-mode loopback \
      --symmetric --cipher-algo AES256 --output "$ENC" "$ARCHIVE" || {
    echo "ERROR: gpg encryption failed; nothing uploaded" >&2
    exit 1
  }

  ENC_NAME=$(basename "$ENC")
  rclone copyto "$ENC" "$REMOTE/$ENC_NAME" --no-traverse 2>&1 || {
    echo "ERROR: rclone upload to $REMOTE failed" >&2
    rm -f "$ENC"
    exit 1
  }

  # VERIFY against the hash the REMOTE reports, not rclone's exit status. B2
  # stores SHA1 natively, so this is a real end-to-end check of the bytes that
  # landed. An EMPTY hash is a FAILURE, not a pass — a check that treats "I
  # could not tell" as "fine" is the shape of every silent-success bug in this
  # repo.
  want=$(_sha1 "$ENC")
  got=$(rclone hashsum sha1 "$REMOTE/$ENC_NAME" --no-traverse 2>/dev/null | cut -d" " -f1)
  if [ -z "$got" ]; then
    echo "ERROR: the remote returned no hash for $ENC_NAME — cannot verify, so" >&2
    echo "       this is being treated as a failed mirror, not a passed one." >&2
    rm -f "$ENC"
    exit 1
  fi
  if [ "$want" != "$got" ]; then
    echo "ERROR: remote hash $got != local $want for $ENC_NAME" >&2
    rm -f "$ENC"
    exit 1
  fi
  echo "mirrored $REMOTE/$ENC_NAME (sha1 verified)"
  MIRRORED_REMOTE="$REMOTE/$ENC_NAME"
  rm -f "$ENC"

  # Keep more off-host than locally, same reasoning as the filesystem branch.
  rclone lsf "$REMOTE" --include "agent-backup-*.tar.gz.gpg" 2>/dev/null \
    | sort -r | tail -n +31 | while read -r old_obj; do
      [ -n "$old_obj" ] || continue
      rclone deletefile "$REMOTE/$old_obj" 2>/dev/null && echo "pruned off-host $old_obj"
    done
fi

# ---- off-host mirror, filesystem -------------------------------------------
ICLOUD="$HOME/Library/Mobile Documents/com~apple~CloudDocs/repete-backups"
if [ "${REPETE_OFFHOST_DIR+set}" = set ]; then
  OFFHOST="$REPETE_OFFHOST_DIR"          # explicit, including "" for disabled
elif [ -d "$HOME/Library/Mobile Documents/com~apple~CloudDocs" ]; then
  OFFHOST="$ICLOUD"
else
  OFFHOST=""
  # Only a problem if NOTHING mirrored. Warning on a working remote setup is
  # how a warning becomes background noise — divergence #22 was exactly that.
  if [ -z "$MIRRORED_REMOTE" ]; then
    echo "warning: no off-host mirror configured; $DEST is on the same disk as" >&2
    echo "         the thing it protects. Set REPETE_MIRROR_REMOTE (rclone" >&2
    echo "         remote, encrypted) or REPETE_OFFHOST_DIR (filesystem)." >&2
  fi
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

# ---- the receipt -----------------------------------------------------------
# Written ONLY after a mirror verified. health.py reads its age and degrades
# when a weekday mirror was due and did not happen.
#
# The point is that this file cannot claim a success that did not occur: every
# failure path above `exit 1`s before reaching here, so the receipt's existence
# IS the verification. The lesson is borrowed at cost — a capture hook in this
# operator's setup logged 171 consecutive "successes" that wrote nothing,
# because its outcome field could not express its own failure mode.
if [ -n "$MIRRORED_REMOTE" ] || [ -n "$OFFHOST" ]; then
  RECEIPT="${REPETE_MIRROR_RECEIPT:-memory/offhost_mirror.json}"
  mkdir -p "$(dirname "$RECEIPT")"
  if [ -n "$MIRRORED_REMOTE" ]; then
    _dest="$MIRRORED_REMOTE"; _kind="remote"
  else
    _dest="$MIRROR"; _kind="filesystem"
  fi
  cat > "$RECEIPT" <<JSON
{
  "ts": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "kind": "$_kind",
  "destination": "$_dest",
  "archive": "$(basename "$ARCHIVE")",
  "sha256": "$(_sha256 "$ARCHIVE")",
  "verified": true
}
JSON
  echo "receipt $RECEIPT"
fi
