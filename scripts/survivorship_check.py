#!/usr/bin/env python3
"""How survivorship-inflated is a snapshot's own benchmark? — §48, 2026-08-03

Every wide snapshot is built from TODAY's index membership and back-projected.
So every name in it survived to 2026 by construction: the companies that went
bankrupt, got delisted or were acquired at a loss are simply absent. That has
been listed as a failure mode on every gate since §41 and was always stated as
a direction ("this flatters any long strategy"), never as a size.

This measures the size, and it needs no external data — the snapshot contains
its own control. SPY is IN the universe, and an index ETF's price already
reflects constituent changes: names that failed left the index and took their
losses with them. So

    equal-weight buy-and-hold across the 500 names   (survivorship-poisoned)
  - SPY buy-and-hold over the identical bars         (survivorship-free)
  = the inflation, in percentage points

Run:  python scripts/survivorship_check.py

Why it matters, concretely. §48's clause `beats_exposure_matched` scores an arm
against the snapshot's OWN buy-and-hold. That is a fair comparison in one sense
— both sides are equally poisoned — but it means a "beats the market" verdict
is measured against a market that returned +138% over 2000-2006 while the real
index returned +8.7%. Reading either number without this one attached is how a
survivorship artifact gets promoted to an edge.

It is a MEASUREMENT and produces no verdict. It cannot support or reject any
claim and changes no config value.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys

SNAPSHOT_DIR = "data/snapshots"
BENCHMARK = "SPY"


def total_return_pct(bars: list) -> float | None:
    """First close to last close, as a percentage. None if unusable."""
    if not bars or len(bars) < 2:
        return None
    try:
        first = float(bars[0]["close"])
        last = float(bars[-1]["close"])
    except (KeyError, TypeError, ValueError):
        return None
    return (last / first - 1) * 100 if first > 0 else None


def measure(path: str, benchmark: str = BENCHMARK) -> dict:
    with gzip.open(path) as f:
        data = json.load(f)
    rets = {}
    for sym, bars in data.items():
        r = total_return_pct(bars)
        if r is not None:
            rets[sym] = r
    if not rets:
        return {"path": path, "n": 0, "error": "no usable series"}
    bench = rets.get(benchmark)
    out = {
        "path": os.path.basename(path),
        "n": len(rets),
        "benchmark": benchmark,
        "benchmark_return_pct": round(bench, 2) if bench is not None else None,
        "equal_weight_return_pct": round(statistics.fmean(rets.values()), 2),
        "median_return_pct": round(statistics.median(rets.values()), 2),
    }
    # The headline. Positive means the snapshot's own benchmark overstates the
    # market by this many percentage points.
    out["inflation_pp"] = (round(out["equal_weight_return_pct"] - bench, 2)
                           if bench is not None else None)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default=SNAPSHOT_DIR)
    ap.add_argument("--benchmark", default=BENCHMARK)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = sorted(os.path.join(args.dir, n) for n in os.listdir(args.dir)
                   if n.startswith("bars_wide_") and n.endswith(".json.gz"))
    rows = [measure(p, args.benchmark) for p in paths]

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'snapshot':44} {args.benchmark:>9} {'equal-wt':>10} "
          f"{'median':>9} {'inflation':>11} {'n':>5}")
    for r in rows:
        if r.get("error"):
            print(f"{r['path']:44} {r['error']}")
            continue
        b = r["benchmark_return_pct"]
        print(f"{r['path']:44} {b:>8.2f}% {r['equal_weight_return_pct']:>9.2f}% "
              f"{r['median_return_pct']:>8.2f}% {r['inflation_pp']:>+10.2f}pp "
              f"{r['n']:>5}")
    print("\ninflation = equal-weight universe minus the index ETF over the same")
    print("bars. It is the amount by which a gate's own buy-and-hold benchmark")
    print("overstates the market it claims to represent. See §48.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
