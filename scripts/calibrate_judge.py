#!/usr/bin/env python3
"""Derive the LLM judge's observed sizing behaviour from the live ledger.

    python scripts/calibrate_judge.py                 # print, write nothing
    python scripts/calibrate_judge.py --write         # update the calibration

Why this exists
---------------
`src/backtest.py` deliberately never imports `llm` (see its module docstring),
so the judge cannot be replayed over history — there is no way to ask what a
model would have said about a bar in 2024. But live, the judge downsizes a
majority of buys, and the simulator has never modelled that at all. §26 named
this as the live half of divergence #8:

    the backtest never applies the judge's verdict: there is no
    review["scale"] anywhere in backtest.py

Every OOS figure in `knowledge/backtest_candidates.md` was therefore produced by
a simulator sizing entries the live bot would have cut. This script measures the
cut, so `backtest.judge_model` can apply it instead of inventing a number.

What it deliberately does NOT do
--------------------------------
It does not condition on strategy or confidence. Both look tempting and both
are unsupportable at this sample: meanrev is n=7 and ma_crossover is n=3, and
106 of 146 decisions carry no confidence field at all. Fitting a per-strategy
haircut to seven observations would be exactly the overfit this project's gate
discipline exists to prevent. One pooled distribution, honestly labelled.

Read-only against the ledger (invariant #9). It never writes to memory/.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "memory", "ledger.jsonl")
OUT = os.path.join(ROOT, "knowledge", "judge_calibration.json")

# Below this many judged buys the distribution is noise dressed as data. The
# haircut model refuses to load a calibration that did not clear it.
MIN_SAMPLE = 50


def collect(ledger_path: str) -> tuple[list[dict], str]:
    """Every buy decision carrying a judge verdict, plus the ledger's sha."""
    h = hashlib.sha256()
    rows = []
    with open(ledger_path, "rb") as f:
        for raw in f:
            h.update(raw)
            if not raw.strip():
                continue
            r = json.loads(raw)
            if (r.get("type") == "decision" and r.get("action") == "buy"
                    and r.get("llm_review")):
                rows.append(r)
    return rows, h.hexdigest()[:16]


def calibrate(rows: list[dict]) -> dict:
    """Split the judge's behaviour into two independent effects.

    A veto and a downsize are different acts and the simulator must model them
    differently: a veto removes the trade, a downsize shrinks it. Folding the
    vetoes' `scale: 0.1` into the haircut histogram would understate the mean
    cut AND silently let vetoed trades through at 10% size — wrong twice.
    """
    vetoes = [r for r in rows if r["llm_review"].get("verdict") == "veto"]
    sized = [r for r in rows if r["llm_review"].get("verdict") != "veto"]

    scales = [r["llm_review"].get("scale") for r in sized]
    scales = [s for s in scales if isinstance(s, (int, float))]
    hist = collections.Counter(scales)
    mean = sum(scales) / len(scales) if scales else 1.0
    downsized = [s for s in scales if s < 1.0]

    ts = [r["ts"] for r in rows if r.get("ts")]
    span_days = 0
    if ts:
        span_days = (datetime.fromisoformat(max(ts))
                     - datetime.fromisoformat(min(ts))).days

    return {
        "n_judged_buys": len(rows),
        "n_sized": len(sized),
        "veto_rate": round(len(vetoes) / len(rows), 6) if rows else 0.0,
        "downsize_rate": round(len(downsized) / len(scales), 6) if scales else 0.0,
        "mean_scale": round(mean, 6),
        # Sorted so the JSON is stable across runs and diffs are readable.
        "scale_histogram": {str(k): hist[k] for k in sorted(hist)},
        "observed_span_days": span_days,
        "observed_from": min(ts)[:10] if ts else None,
        "observed_to": max(ts)[:10] if ts else None,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true",
                   help=f"write {os.path.relpath(OUT, ROOT)} (default: print only)")
    p.add_argument("--ledger", default=LEDGER)
    args = p.parse_args()

    if not os.path.exists(args.ledger):
        print(f"no ledger at {args.ledger}", file=sys.stderr)
        return 2

    rows, sha = collect(args.ledger)
    cal = calibrate(rows)
    cal["source_ledger_sha256_16"] = sha
    cal["min_sample"] = MIN_SAMPLE

    for k, v in cal.items():
        print(f"  {k}: {v}")

    if cal["n_judged_buys"] < MIN_SAMPLE:
        print(f"\nREFUSING: {cal['n_judged_buys']} judged buys is below the "
              f"{MIN_SAMPLE} minimum. Not enough to model.", file=sys.stderr)
        return 1

    # Stated here rather than in a commit message, because whoever reads a gate
    # result six months from now will have this file and not the commit.
    print(f"\n  NOTE: {cal['n_judged_buys']} decisions over "
          f"{cal['observed_span_days']} days are heavily SERIALLY CORRELATED —"
          f"\n  the same names re-judged daily in one market regime. The"
          f"\n  effective sample is far below {cal['n_judged_buys']}. Treat the"
          f"\n  mean scale as a usable central estimate and the tails as unmeasured.")

    if args.write:
        with open(OUT, "w") as f:
            json.dump(cal, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"\n  wrote {OUT}")
    else:
        print(f"\n  (dry run — pass --write to update "
              f"{os.path.relpath(OUT, ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
