#!/usr/bin/env python3
"""Score the shadow-judge comparison log against the real judge's calls.

Reads knowledge/llm_shadow_log.jsonl (one row per signal the shadow judge
saw — see src/llm_shadow.py) and prints the numbers that actually matter for
deciding whether a local model is safe to promote off shadow mode:

  - direction agreement: did live and shadow make the same call (approve /
    downsize / veto), ignoring the exact downsize amount?
  - MISSED VETOES: live vetoed, shadow did not. This is the number to gate
    on — invariant #2 says the judge may only shrink risk, so a shadow model
    that fails to catch a real veto is quietly taking on risk the live judge
    would have refused. Zero is the target; nonzero needs to be read by hand
    before this model goes anywhere near cfg["llm"]["provider"].
  - OVER-VETOES: shadow vetoed, live did not. Not dangerous (it's the model
    being MORE cautious, which invariant #2 allows), but worth knowing —
    a model that vetoes everything has 100% "safety" and zero usefulness.
  - format reliability: how often the shadow call returned a parseable
    verdict at all (shadow_ok), separate from whether that verdict agreed.
  - latency: p50/p95 shadow call time, for capacity planning once this runs
    inside a live cycle instead of shadow-only.

Usage:
    python scripts/score_llm_shadow.py
    python scripts/score_llm_shadow.py --path knowledge/llm_shadow_log.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load_rows(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        print(f"No log at {path} yet — llm_shadow.enabled is probably still "
              f"false, or no signals have reached the judge since it was "
              f"turned on. See docs/llm_shadow_setup.md.", file=sys.stderr)
        return []
    rows = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("event") == "llm_shadow_comparison":
                rows.append(rec)
    return rows


def score(rows: list[dict]) -> None:
    if not rows:
        print("Nothing to score.")
        return

    n = len(rows)
    ok = [r for r in rows if r.get("shadow_ok")]
    format_rate = len(ok) / n

    agree = sum(1 for r in ok if r["live_verdict"] == r["shadow_verdict"])
    agreement_rate = agree / len(ok) if ok else 0.0

    missed_vetoes = [r for r in ok
                     if r["live_verdict"] == "veto" and r["shadow_verdict"] != "veto"]
    over_vetoes = [r for r in ok
                  if r["shadow_verdict"] == "veto" and r["live_verdict"] != "veto"]

    latencies = [r["shadow_latency_seconds"] for r in rows
                if isinstance(r.get("shadow_latency_seconds"), (int, float))]

    print(f"=== Shadow judge score — {n} signal(s) seen ===\n")
    print(f"Format reliability : {len(ok)}/{n}  ({format_rate:.0%}) returned a "
          f"parseable verdict")
    print(f"Direction agreement: {agree}/{len(ok)}  ({agreement_rate:.0%}) "
          f"among parseable replies")
    print(f"MISSED VETOES       : {len(missed_vetoes)}   <- gate on this being 0")
    print(f"Over-vetoes         : {len(over_vetoes)}   (shadow more cautious "
          f"than live — not dangerous, just conservative)")
    if latencies:
        latencies_sorted = sorted(latencies)
        p50 = statistics.median(latencies_sorted)
        p95 = latencies_sorted[int(len(latencies_sorted) * 0.95)
                               if len(latencies_sorted) > 1 else 0]
        print(f"Latency             : p50={p50:.1f}s  p95={p95:.1f}s")

    if missed_vetoes:
        print("\n--- Missed vetoes (read these by hand) ---")
        for r in missed_vetoes:
            print(f"  {r['ts']}  {r['symbol']} {r['action']}  "
                  f"live={r['live_verdict']} shadow={r['shadow_verdict']}  "
                  f"live_reasoning={r['live_reasoning'][:100]!r}")

    not_ok = [r for r in rows if not r.get("shadow_ok")]
    if not_ok:
        print(f"\n--- {len(not_ok)} failed shadow call(s) (errors, not scored above) ---")
        for r in not_ok[:10]:
            print(f"  {r['ts']}  {r['symbol']}  {r.get('shadow_error')}")
        if len(not_ok) > 10:
            print(f"  ... and {len(not_ok) - 10} more")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default="knowledge/llm_shadow_log.jsonl")
    args = ap.parse_args()
    score(load_rows(args.path))
