#!/usr/bin/env python3
"""Freeze a pre-registration before it is run.
`python scripts/register_gate.py <spec-id>`

Appends `{id, spec_sha256, registered_at, spec}` to research/registrations.jsonl.
That row is the goalpost. `run_gate.py` will not score a spec whose hash does
not match it.

The full spec is stored, not just its hash, for two reasons: a mismatch can then
report WHICH field moved rather than only that something did, and the
registration alone is enough to re-execute the claim years later.

Re-registering
--------------
Allowed while a claim has no verdict — thinking changes, and a spec edited
before any data is seen is just authoring. It is REFUSED once a verdict exists
for that id, because at that point the result is known and editing the pass mark
is the exact failure this whole apparatus exists to prevent. §33 RUN 1 printed
VALIDATED and was an artifact; it stayed in the record instead of being tidied
away, and this is the mechanical version of that rule.

Every re-registration appends a new row. Nothing is ever rewritten, so the
history of what was promised stays readable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gatespec as gs                                       # noqa: E402

SPEC_DIR = "research/specs"
VERDICTS = "research/verdicts.jsonl"


def spec_path(spec_id: str) -> str:
    return os.path.join(SPEC_DIR, f"{spec_id}.yaml")


def has_verdict(spec_id: str, path: str = VERDICTS) -> bool:
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return any(json.loads(l)["id"] == spec_id for l in f if l.strip())


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("spec_id")
    p.add_argument("--registrations", default=gs.REGISTRATIONS)
    p.add_argument("--verdicts", default=VERDICTS)
    args = p.parse_args()

    spec = gs.load(spec_path(args.spec_id))
    if spec["id"] != args.spec_id:
        raise SystemExit(f"spec id {spec['id']!r} does not match filename "
                         f"{args.spec_id!r}")

    digest = gs.canonical_sha256(spec)
    prior = gs.registrations(args.registrations).get(args.spec_id)

    if prior and prior["spec_sha256"] == digest:
        print(f"{args.spec_id}: already registered, unchanged "
              f"({digest[:16]}…, {prior['registered_at'][:19]})")
        return 0

    if prior:
        if has_verdict(args.spec_id, args.verdicts):
            changed = gs.diff_fields(prior["spec"], spec)
            raise SystemExit(
                f"REFUSING to re-register {args.spec_id}: a verdict already "
                f"exists for it.\n\nChanged since registration:\n  "
                + "\n  ".join(changed or ["(structure differs)"])
                + "\n\nThe result is known, so editing the claim now is moving "
                  "the goalpost.\nRegister a NEW id and state in its prior what "
                  "the earlier run showed.")
        print(f"{args.spec_id}: re-registering (no verdict yet, so this is "
              f"still authoring)")
        for line in gs.diff_fields(prior["spec"], spec):
            print(f"    {line}")

    os.makedirs(os.path.dirname(args.registrations) or ".", exist_ok=True)
    rec = {"id": args.spec_id, "spec_sha256": digest,
           "registered_at": datetime.now(timezone.utc).isoformat(),
           "spec": spec}
    with open(args.registrations, "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")

    print(f"registered {args.spec_id}  sha256 {digest[:16]}…")
    print(f"  claim  : {spec['claim']}  |  arms: "
          f"{', '.join(a['name'] for a in spec['arms'])}")
    print(f"  prior  : {spec['prior'].strip().splitlines()[0][:72]}")
    print(f"  K      : {spec.get('bonferroni_k', 1)}")
    print("\nThe pass mark is now frozen. Run it with:")
    print(f"  python scripts/run_gate.py {args.spec_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
