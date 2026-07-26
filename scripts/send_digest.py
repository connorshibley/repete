#!/usr/bin/env python3
"""Send (or dry-run) one daily digest to every active subscriber.

    python scripts/send_digest.py --dry-run --fixture /tmp/qa   # safe rehearsal
    python scripts/send_digest.py                               # the real run

There is deliberately NO --send-for-real flag. Whether mail actually leaves is
decided by configuration and environment — `publisher.digest.enabled`,
`publisher.email.dry_run`, and `RESEND_API_KEY` — never by an argument, because
an argument is something a person pastes out of a runbook at 2am. `--dry-run`
exists and can only make a run SAFER; it cannot arm one.

Not wired into scripts/scheduler.py. The agent container cannot run this: its
Dockerfile never copies publisher/, and it has no publisher_data mount. The
scheduling shape is written down in PRODUCT.md for the commit that brings a
verified sending domain and a non-empty subscriber list.

Read-only against agent state (invariant #9).
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def build(args) -> tuple[dict, object, object]:
    """cfg + SubscriberDB + ReadOnlyLedger, all pointed at the same place.

    `--fixture` moves the ledger, journal, data_dir and subscriber DB together
    in one step. Repointing them individually is how you end up reading a
    fixture ledger while opening an empty database and mailing nobody.
    """
    from publisher import config as pconfig
    from publisher.readonly import ReadOnlyLedger, agent_paths
    from publisher.subscribers import SubscriberDB

    cfg = pconfig.load(args.root)
    if args.fixture:
        fx = os.path.abspath(args.fixture)
        cfg["memory"]["ledger_path"] = os.path.join(fx, "ledger.jsonl")
        cfg.setdefault("x_posting", {})["journal_path"] = os.path.join(
            fx, "journal.jsonl")
        cfg["publisher"]["data_dir"] = os.path.join(fx, "publisher_data")
    if args.dry_run:
        cfg["publisher"]["email"]["dry_run"] = True

    db_path = os.path.join(cfg["publisher"]["data_dir"], "pub.db")
    if not os.path.exists(db_path):
        # SubscriberDB CREATES whatever path it is handed, so a wrong path is
        # not an error — it is a brand-new empty database, zero recipients, and
        # an exit code of 0 that reads as success. Say so out loud.
        print(f"WARNING: no subscriber database at {db_path} — one will be "
              f"created empty, and this run will reach nobody.", file=sys.stderr)
    return (cfg, SubscriberDB(db_path),
            ReadOnlyLedger(agent_paths(cfg)["ledger"], cfg))


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="force the outbox path; can only make a run safer")
    p.add_argument("--to", metavar="EMAIL",
                   help="smoke-test a single ACTIVE subscriber")
    p.add_argument("--force", action="store_true",
                   help="ignore the enable flag and the once-per-day guard")
    p.add_argument("--fixture", metavar="DIR",
                   help="run against a QA fixture instead of live state")
    p.add_argument("--root", default=ROOT, help="repo root holding config.yaml")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    from publisher import broadcast

    cfg, db, ledger = build(args)
    run = broadcast.send_daily_digest(cfg, db, ledger, only=args.to,
                                      force=args.force)

    if run["skipped_reason"]:
        print(f"SKIPPED — {run['skipped_reason']}")
        return 0

    tiers = " ".join(f"{t}={n}" for t, n in sorted(run["by_tier"].items()))
    print(f"recipients={run['recipients']} ({tiers}) "
          f"queued={run['queued']} sent={run['sent']} "
          f"failed={run['failed']} dry_run={run['dry_run']}")
    if run["skipped_tiers"]:
        print(f"  skipped tiers (nothing to report): {run['skipped_tiers']}")
    for f in run["failures"]:
        print(f"  FAILED {f['to']}: {f['status']}", file=sys.stderr)
    if args.verbose:
        print(f"  audit: {os.path.join(cfg['publisher']['data_dir'], 'digest_runs.jsonl')}")
    return run["failed"]


if __name__ == "__main__":
    raise SystemExit(main())
