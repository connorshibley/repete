#!/usr/bin/env python3
"""Restore drill (Phase D, 2026-07-22) — `python scripts/restore_drill.py`.

A backup that has never been restored is a hope, not a backup. This
extracts the NEWEST archive into a temp dir and proves it is actually
usable:

  1. every *.jsonl stream in the archive parses line-for-line as JSON;
  2. every stream matches the record count recorded in the archive's own
     backup_manifest.json — this is what catches a CLEANLY truncated archive
     (one whose remaining lines all parse). Counting against live state cannot:
     an archive legitimately holds fewer records than live. Archives written
     before manifests existed fall back to the weaker "not more than live" check
     and say so;
  3. every stream's sha256 matches the manifest (2026-08-06). Counts do not
     catch a corruption that preserves line count and JSON validity — one
     flipped byte inside a string value leaves both intact. The manifest had
     shipped for two weeks with no integrity field of any kind;
  4. every stream is a byte-PREFIX of its live counterpart (2026-08-06). These
     streams are append-only, so live may be LONGER but the shared span must be
     identical. That is strictly stronger than the count comparison, and it is
     what catches a rewritten history or a hand-edited stream — CLAUDE.md
     forbids editing lessons.jsonl by hand and nothing enforced it. A diff
     would be the wrong tool here for the reason in (2); a prefix test is the
     right one precisely because it tolerates live being ahead;
  5. the newest archive also exists OFF-HOST with an identical sha256
     (2026-08-06). Reading it forces iCloud to materialise a file evicted by
     Optimize Mac Storage, so this doubles as the sync check: "the off-host
     backup silently stopped" becomes a red drill instead of a silence;
  6. under storage.backend: sqlite the archive is validated via agent.db
     (a sqlite deployment has no .jsonl streams and must not fail for it);
  7. the ledger tail is valid JSON (same check preflight runs live);
  8. config.yaml parses.

Check 5 runs ONLY when an off-host directory resolves, and that is deliberate
rather than lax. A CI runner has no iCloud and never will, so a mandatory
version would be a step that can never pass — and a version that skipped in
silence would be one that can never fail. It is a property of THIS laptop, so
it belongs to the weekly launchd drill; the same reasoning ci.yml already
applies to check_secret_exposure.py. When it is skipped it says so out loud.

PASS -> exit 0, FAIL -> exit 1 (usable from cron/CI). Read-only against
the live state; the extraction happens in a throwaway directory.
"""
import glob
import hashlib
import json
import os
import sys
import tarfile
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def newest_backup(dest: str) -> str | None:
    archives = sorted(glob.glob(os.path.join(dest, "agent-backup-*.tar.gz")))
    return archives[-1] if archives else None


def scan_stream(path: str) -> tuple[int, int]:
    """(records, bad_lines) for one JSONL file."""
    n = bad = 0
    with open(path) as f:
        for ln in f:
            if not ln.strip():
                continue
            n += 1
            try:
                json.loads(ln)
            except ValueError:
                bad += 1
    return n, bad


def sha256_of(path: str) -> str:
    """Content hash, streamed — archives are small but memory/ need not be."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def offhost_dir() -> str | None:
    """Where the mirror lives, resolved exactly as scripts/backup.sh resolves it.

    Two readers, one rule. If these ever disagree the drill would validate a
    directory the backup does not write to — a green check on the wrong file,
    which is the failure mode this repo keeps finding in its own controls.
    """
    env = os.environ.get("REPETE_OFFHOST_DIR")
    if env is not None:                      # explicit, "" means disabled
        return env or None
    icloud = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs")
    if os.path.isdir(icloud):
        return os.path.join(icloud, "repete-backups")
    return None


def check_offhost(archive: str, dest: str | None) -> tuple[list[str], str]:
    """(failures, human note). Skipped when no off-host directory resolves."""
    if not dest:
        return [], ("off-host mirror: SKIPPED (no directory resolves here — "
                    "expected on CI, NOT expected on the laptop)")
    mirror = os.path.join(dest, os.path.basename(archive))
    if not os.path.exists(mirror):
        return ([f"off-host copy missing: {mirror} — the mirror has stopped, "
                 f"or iCloud is not syncing"], "")
    # Hashing reads every byte, which forces iCloud to download a file that
    # Optimize Mac Storage has evicted to a placeholder. A stat() would not.
    if sha256_of(mirror) != sha256_of(archive):
        return ([f"off-host copy differs from local: {mirror}"], "")
    return [], f"off-host mirror: verified {mirror}"


def drill(archive: str, live_memory: str | None = None) -> list[str]:
    """All failures found (empty = PASS)."""
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with tarfile.open(archive) as tf:
                tf.extractall(tmp, filter="data")
        except Exception as e:  # noqa: BLE001
            return [f"archive does not extract: {e}"]

        cfg_path = os.path.join(tmp, "config.yaml")
        if not os.path.exists(cfg_path):
            fails.append("config.yaml missing from archive")
        else:
            try:
                with open(cfg_path) as f:
                    yaml.safe_load(f)
            except Exception as e:  # noqa: BLE001
                fails.append(f"config.yaml does not parse: {e}")

        # Manifest written by backup.sh: the authoritative per-stream counts at
        # backup time. Comparing against it is the only way to detect a cleanly
        # truncated archive.
        manifest = {}
        want_hash = {}
        mpath = os.path.join(tmp, "backup_manifest.json")
        if os.path.exists(mpath):
            try:
                with open(mpath) as f:
                    doc = json.load(f) or {}
                manifest = doc.get("streams") or {}
                # Absent on archives written before 2026-08-06. Missing hashes
                # are skipped, not failed — an old archive is still a usable
                # backup and refusing it would delete the only history we have.
                want_hash = doc.get("sha256") or {}
            except (ValueError, OSError) as e:
                fails.append(f"backup_manifest.json does not parse: {e}")

        streams = glob.glob(os.path.join(tmp, "memory", "*.jsonl"))
        db_path = os.path.join(tmp, "memory", "agent.db")
        if not streams and not os.path.exists(db_path):
            fails.append("archive contains neither JSONL streams nor agent.db")
        for path in streams:
            name = os.path.basename(path)
            n, bad = scan_stream(path)
            if bad:
                fails.append(f"{name}: {bad}/{n} lines unparseable")
            # Two independent checks — they catch different faults, so both run.
            # Manifest: the archive vs what it recorded at write time (catches a
            # cleanly truncated archive).
            if name in manifest and n != manifest[name]:
                fails.append(
                    f"{name}: archive holds {n} records but the manifest "
                    f"recorded {manifest[name]} — archive is "
                    f"{'truncated' if n < manifest[name] else 'inconsistent'}")
            # Content hash: the only check that sees a corruption which left
            # the line count and the JSON intact.
            if name in want_hash:
                got = sha256_of(path)
                if got != want_hash[name]:
                    fails.append(
                        f"{name}: content hash {got[:12]} does not match the "
                        f"manifest's {want_hash[name][:12]} — the archive was "
                        f"altered after it was written")
            # Live: the archive predates now, so MORE records than live means
            # it came from somewhere else (catches a wrong-source archive).
            if live_memory:
                live = os.path.join(live_memory, name)
                if os.path.exists(live):
                    live_n, _ = scan_stream(live)
                    if n > live_n:
                        fails.append(f"{name}: archive has {n} records but "
                                     f"live has {live_n} — wrong source?")
                    # Append-only means the archive must be a byte-PREFIX of
                    # live: live may have grown, but the span they share cannot
                    # have changed. Divergence means history was rewritten —
                    # by corruption, by a hand-edit, or by a restore from the
                    # wrong machine. Counts cannot see any of those.
                    with open(path, "rb") as af, open(live, "rb") as lf:
                        a_bytes = af.read()
                        l_bytes = lf.read()
                    if not l_bytes.startswith(a_bytes):
                        at = next((i for i, (x, y) in
                                   enumerate(zip(a_bytes, l_bytes)) if x != y),
                                  min(len(a_bytes), len(l_bytes)))
                        fails.append(
                            f"{name}: archive is not a prefix of live — they "
                            f"diverge at byte {at} of {len(a_bytes)}. These "
                            f"streams are append-only, so live should only "
                            f"ever have grown")

        # sqlite deployments keep the audit trail in agent.db, not .jsonl.
        if os.path.exists(db_path):
            try:
                import sqlite3
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                n_ev = conn.execute("SELECT count(*) FROM events").fetchone()[0]
                conn.close()
                if n_ev == 0:
                    fails.append("agent.db in archive has zero events")
            except Exception as e:  # noqa: BLE001
                fails.append(f"agent.db in archive is unusable: {e}")

        ledger = os.path.join(tmp, "memory", "ledger.jsonl")
        if os.path.exists(ledger):
            with open(ledger, "rb") as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            if lines:
                try:
                    json.loads(lines[-1])
                except ValueError:
                    fails.append("ledger tail in archive is not valid JSON")
    return fails


def main() -> int:
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "backups")
    archive = newest_backup(dest)
    if not archive:
        print(f"FAIL: no backups found in {dest} — run scripts/backup.sh")
        return 1
    fails = drill(archive, live_memory=os.path.join(ROOT, "memory"))
    off_fails, note = check_offhost(archive, offhost_dir())
    fails += off_fails
    if fails:
        print(f"FAIL: {archive}")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"PASS: {archive} restores cleanly")
    if note:
        print(f"  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
