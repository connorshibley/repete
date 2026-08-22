#!/usr/bin/env python3
"""One-time: reconstruct research/trials.jsonl from the arm summaries that
research/verdicts.jsonl already holds.

    python scripts/backfill_trials.py            # dry run, prints what it would write
    python scripts/backfill_trials.py --write    # append, refusing to duplicate

Why a backfill is honest here and would not be elsewhere
--------------------------------------------------------
scripts/run_gate.py writes every arm's summary into the verdict row
(`"arms": {name: summary}`) and always has. So the data these rows are built
from is not reconstructed — it is the contemporaneous record, stored in the
wrong file. What this script adds is only the envelope (section, claim, K,
shas), every field of which is read from the same verdict row or from the
registration it points to. Nothing is estimated.

Every row carries `backfilled_from: "verdicts.jsonl"` so a reader can tell it
from a row the runner wrote at run time. That label is the difference between
a backfill and a fabrication.

Idempotent: keyed on (spec_id, ran_at, arm). Re-running appends nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import backtest as bt  # noqa: E402

VERDICTS = os.path.join(ROOT, "research", "verdicts.jsonl")
REGISTRATIONS = os.path.join(ROOT, "research", "registrations.jsonl")


def _rows(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def build() -> list[dict]:
    regs = {r["id"]: r for r in _rows(REGISTRATIONS)}
    out = []
    for v in _rows(VERDICTS):
        spec = (regs.get(v["id"]) or {}).get("spec") or {}
        ran_at = v.get("ran_at") or v.get("ts") or ""
        for arm, summary in (v.get("arms") or {}).items():
            out.append({
                "phase": "oos" if spec.get("split") else "single",
                "arm": arm,
                "section": v["id"],
                "spec_sha256": v.get("spec_sha256"),
                "claim": spec.get("claim", "UNKNOWN"),
                "bonferroni_k": spec.get("bonferroni_k", 1),
                "judge_model": bool(spec.get("judge_model")),
                "snapshot_sha256": (spec.get("snapshot") or {}).get("sha256"),
                "workers": v.get("workers"),
                "ran_at": ran_at,
                "backfilled_from": "verdicts.jsonl",
                **summary,
            })
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true")
    p.add_argument("--trials", default=os.path.join(ROOT, bt.DEFAULT_TRIALS_PATH))
    args = p.parse_args()

    rows = build()
    have = {(r.get("section"), r.get("ran_at"), r.get("arm"))
            for r in _rows(args.trials)}
    fresh = [r for r in rows if (r["section"], r["ran_at"], r["arm"]) not in have]
    print(f"arm summaries in verdicts : {len(rows)}")
    print(f"already in trial log      : {len(rows) - len(fresh)}")
    print(f"to write                  : {len(fresh)}")
    if not args.write:
        print("\n(dry run — pass --write to append)")
        return 0
    for r in fresh:
        bt.append_trial(args.trials, r)
    print(f"\nappended {len(fresh)} rows to {os.path.relpath(args.trials, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
