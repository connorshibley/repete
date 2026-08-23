#!/usr/bin/env python3
"""What the judge costs, summed from the ledger — never extrapolated.

    python scripts/inference_cost_report.py            # memory/ledger.jsonl
    python scripts/inference_cost_report.py --ledger X

The audit (2026-08-21) found zero inference-cost tracking: llm_client threw
the vendor's usage block away. From 2026-08-22 every judged decision carries
vendor_model, input/output tokens and cost_usd (from llm.pricing in
config.yaml). This sums them.

WHAT IT WILL NOT DO. Backfill. The 1,344 decisions before the fields existed
have no token counts — the responses are gone — and inventing an average to
multiply them by would be a confident number with no evidence under it. The
report states the date of the first priced row and counts from there. If a
priced row has cost_usd None, the model was not in the price table; that is
reported as "unpriced", never folded into a total as 0.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ledger", default=os.path.join(ROOT, "memory", "ledger.jsonl"))
    a = p.parse_args()

    dec = [r for r in rows(a.ledger) if r.get("type") == "decision"]
    priced = [r for r in dec if "vendor_model" in r and r.get("vendor_model")]
    unpriced = [r for r in priced if r.get("cost_usd") is None]
    costed = [r for r in priced if r.get("cost_usd") is not None]
    pre_schema = [r for r in dec if "vendor_model" not in r]

    print(f"decisions in ledger          : {len(dec)}")
    print(f"  before the fields existed  : {len(pre_schema)}   (no token counts; NOT estimated)")
    print(f"  judged, with vendor meta   : {len(priced)}")
    if not priced:
        print("\nNo judged decision carries vendor metadata yet. The first will be")
        print("written on the first successful judge call after 2026-08-22; until")
        print("then there is nothing honest to sum. Total: None")
        return 0

    start = min(r["ts"] for r in priced)[:10]
    print(f"  counting from              : {start}")
    print(f"  unpriced (model not in     : {len(unpriced)}   <- None, not $0; add to llm.pricing")
    print("   llm.pricing)")
    total = sum(r["cost_usd"] for r in costed)
    print(f"\nTOTAL (priced rows only)     : ${total:.4f} over {len(costed)} calls")
    if costed:
        print(f"  mean per judged decision   : ${total / len(costed):.5f}")
        toks_in = sum(r.get("input_tokens") or 0 for r in costed)
        toks_out = sum(r.get("output_tokens") or 0 for r in costed)
        print(f"  tokens in / out            : {toks_in:,} / {toks_out:,}")

    print("\nby served model:")
    bym = collections.defaultdict(lambda: [0, 0.0])
    for r in priced:
        bym[r["vendor_model"]][0] += 1
        if r.get("cost_usd") is not None:
            bym[r["vendor_model"]][1] += r["cost_usd"]
    for m, (n, c) in sorted(bym.items()):
        print(f"  {m:32s} {n:5d} calls  ${c:.4f}")
    fb = sum(1 for r in priced if r.get("vendor_fell_back"))
    if fb:
        print(f"\n  {fb} call(s) were served by llm.fallback_model, not the model requested.")

    print("\nby day:")
    byd = collections.defaultdict(float)
    for r in costed:
        byd[r["ts"][:10]] += r["cost_usd"]
    for d, c in sorted(byd.items())[-14:]:
        print(f"  {d}  ${c:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
