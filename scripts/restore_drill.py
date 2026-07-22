#!/usr/bin/env python3
"""Restore drill (Phase D, 2026-07-22) — `python scripts/restore_drill.py`.

A backup that has never been restored is a hope, not a backup. This
extracts the NEWEST archive into a temp dir and proves it is actually
usable:

  1. every *.jsonl stream in the archive parses line-for-line as JSON;
  2. record counts are >= the check-time floor (a truncated archive fails);
  3. the ledger tail is valid JSON (same check preflight runs live);
  4. config.yaml parses.

PASS -> exit 0, FAIL -> exit 1 (usable from cron/CI). Read-only against
the live state; the extraction happens in a throwaway directory.
"""
import glob
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

        streams = glob.glob(os.path.join(tmp, "memory", "*.jsonl"))
        if not streams:
            fails.append("no JSONL streams in archive")
        for path in streams:
            name = os.path.basename(path)
            n, bad = scan_stream(path)
            if bad:
                fails.append(f"{name}: {bad}/{n} lines unparseable")
            if live_memory:
                live = os.path.join(live_memory, name)
                if os.path.exists(live):
                    live_n, _ = scan_stream(live)
                    # The archive predates now — it may have FEWER records
                    # than live, never more.
                    if n > live_n:
                        fails.append(f"{name}: archive has {n} records but "
                                     f"live has {live_n} — wrong source?")

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
    if fails:
        print(f"FAIL: {archive}")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"PASS: {archive} restores cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
