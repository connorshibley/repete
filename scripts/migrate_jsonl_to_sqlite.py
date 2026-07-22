#!/usr/bin/env python3
"""One-way migration: JSONL event files -> the SQLite event store.

Copies every record, in order, then VERIFIES by reading both backends back
and comparing record-for-record. The JSONL files are never modified or
deleted — they remain the fallback until you flip storage.backend in
config.yaml and are satisfied.

    python scripts/migrate_jsonl_to_sqlite.py            # migrate + verify
    python scripts/migrate_jsonl_to_sqlite.py --verify   # verify only
"""
import argparse
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))

import store as store_mod  # noqa: E402


def migrate(jsonl_paths: list[str], db_path: str, verify_only: bool = False):
    ok = True
    for path in sorted(jsonl_paths):
        stream = store_mod.stream_name(path)
        src = store_mod.JsonlStore(path)
        dst = store_mod.SqliteStore(db_path, stream)
        source_records = src.read_all()

        if not verify_only:
            existing = len(dst.read_all())
            if existing:
                print(f"  {stream}: {existing} rows already present — skipping "
                      f"(append-only store; delete {db_path} to redo)")
            else:
                for rec in source_records:
                    dst.append(rec)

        got = dst.read_all()
        same = got == source_records
        ok = ok and same
        print(f"  {stream}: jsonl={len(source_records)} sqlite={len(got)} "
              f"{'MATCH' if same else 'MISMATCH'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory-dir", default="memory")
    ap.add_argument("--db", default="memory/agent.db")
    ap.add_argument("--verify", action="store_true", help="verify only")
    args = ap.parse_args()

    paths = glob.glob(os.path.join(args.memory_dir, "*.jsonl"))
    if not paths:
        print(f"no .jsonl files in {args.memory_dir}/")
        return 1
    print(f"{'Verifying' if args.verify else 'Migrating'} {len(paths)} "
          f"streams -> {args.db}")
    ok = migrate(paths, args.db, verify_only=args.verify)
    print("\nAll streams match." if ok else "\nMISMATCH — do not switch backends.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
