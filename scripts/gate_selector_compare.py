#!/usr/bin/env python3
"""§34 — is fold-majority selection any better than what it replaced?

Pre-registered in `knowledge/backtest_candidates.md` §34 on 2026-07-27, before
this file existed. The three selectors, the causality rule, the pass mark and
the limits are fixed there.

Why this runs no simulations
----------------------------
§33b already measured every arm's IS and OOS profit factor in every fold and
wrote them to the trials log. §34 is a question about ESTIMATORS over those
measurements, not about the market, so re-running 60 simulations would add
nothing but a chance for the numbers to drift.

The causality rule
------------------
To select for fold k, only folds 1..k-1 may be used. Leave-one-fold-out would
let fold 3's selection see folds 4 and 5 — the future — and would flatter the
replacement exactly where it matters. Fold 1 has no history and is not scored,
leaving four evaluations.

The oracle (non-causal) variant is computed and printed as a diagnostic upper
bound. It decides nothing.

    python scripts/gate_selector_compare.py
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt  # noqa: E402

SECTION = "33_fold_stability"
BASELINE = "baseline"


def load_matrix(trials_path: str):
    """{(fold, arm): {"is": pf, "oos": pf}} from the corrected §33b run.

    Later records win: the invalid Run 1 wrote the same keys, and its numbers
    must not leak into §34. Run 1's rows are earlier in the file by
    construction, so last-write-wins takes the corrected values.
    """
    latest = {}
    with open(trials_path) as f:
        for line in f:
            if SECTION not in line:
                continue
            r = json.loads(line)
            if "fold" not in r:
                continue
            latest[(r["fold"], r["arm"], r["phase"])] = r["profit_factor"]
    folds = sorted({k[0] for k in latest})
    arms = sorted({k[1] for k in latest})
    return latest, folds, arms


def top_half(pfs: dict, arm: str) -> bool:
    ordered = sorted(pfs, key=pfs.get, reverse=True)
    return ordered.index(arm) < len(ordered) / 2


def select_single_split(latest, fold, cands):
    """The retired method: best in-sample profit factor in this fold."""
    return max(cands, key=lambda a: latest[(fold, a, "in_sample")])


def select_fold_majority(latest, history_folds, cands, threshold=0.6):
    """Top half in >= `threshold` of the given folds; tie-break on mean OOS PF.

    Returns None when no arm qualifies — a real outcome, not an error. A method
    that cannot choose is better than one that chooses arbitrarily, and the
    caller reports it rather than silently falling back.
    """
    if not history_folds:
        return None
    qualified = []
    for a in cands:
        hits = sum(1 for f in history_folds
                   if top_half({c: latest[(f, c, "oos")] for c in cands}, a))
        if hits / len(history_folds) >= threshold:
            qualified.append(a)
    if not qualified:
        return None
    return max(qualified, key=lambda a: statistics.mean(
        latest[(f, a, "oos")] for f in history_folds))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--trials", default=bt.DEFAULT_TRIALS_PATH)
    args = p.parse_args()

    latest, folds, arms = load_matrix(args.trials)
    cands = [a for a in arms if a != BASELINE]
    print("§34 selector comparison | claim type: METHOD | no simulations")
    print(f"  {len(folds)} folds x {len(arms)} arms, read from {args.trials}\n")

    rows = []
    for k in folds:
        oos = {a: latest[(k, a, "oos")] for a in cands}
        rnd = statistics.mean(oos.values())
        s1 = select_single_split(latest, k, cands)
        past = [f for f in folds if f < k]
        s2 = select_fold_majority(latest, past, cands)
        future_too = [f for f in folds if f != k]
        s2o = select_fold_majority(latest, future_too, cands)
        rows.append({"fold": k, "random": rnd, "s1": s1, "s2": s2, "s2o": s2o,
                     "oos": oos, "scored": bool(past)})
        print(f"-- fold {k} {'(not scored — no history)' if not past else ''}")
        print(f"     random expectation      PF {rnd:5.3f}")
        print(f"     S1 single-split         {s1:<16} PF {oos[s1]:5.3f}")
        print(f"     S2 fold-majority causal {str(s2):<16} "
              f"PF {oos[s2]:5.3f}" if s2 else
              "     S2 fold-majority causal  — no arm qualified")
        print(f"     (oracle, diagnostic)    {str(s2o):<16} "
              f"PF {oos[s2o]:5.3f}" if s2o else
              "     (oracle, diagnostic)     — no arm qualified")

    scored = [r for r in rows if r["scored"]]
    def summarise(key):
        picks = [(r, r[key]) for r in scored if r[key] is not None]
        if not picks:
            return None, None, 0
        pf = [r["oos"][a] for r, a in picks]
        hits = sum(1 for r, a in picks if top_half(r["oos"], a))
        return statistics.mean(pf), hits, len(picks)

    m1, h1, n1 = summarise("s1")
    m2, h2, n2 = summarise("s2")
    mo, ho, no = summarise("s2o")
    mr = statistics.mean(r["random"] for r in scored)

    print(f"\n-- SCORED ON {len(scored)} HELD-OUT FOLDS (fold 1 excluded: no history) --")
    print(f"  S3 random expectation      mean PF {mr:5.3f}")
    print(f"  S1 single-split            mean PF {m1:5.3f}   hits {h1}/{n1}")
    print(f"  S2 fold-majority (causal)  mean PF "
          f"{m2:5.3f}   hits {h2}/{n2}" if m2 is not None
          else "  S2 fold-majority (causal)  never selected an arm")
    print(f"  .. oracle variant          mean PF "
          f"{mo:5.3f}   hits {ho}/{no}   [DIAGNOSTIC ONLY]" if mo is not None
          else "  .. oracle variant          never selected an arm")

    print("\n-- PRE-REGISTERED PASS MARK --")
    checks = {
        "(a) fold-majority mean PF > random":
            m2 is not None and m2 > mr,
        "(b) fold-majority hit rate >= 3/4":
            m2 is not None and h2 >= 3,
        "(c) beats single-split on BOTH":
            m2 is not None and m2 > m1 and h2 > h1,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    ok = all(checks.values())
    print(f"\nVERDICT: fold-majority selection "
          f"{'REPLACES' if ok else 'DOES NOT REPLACE'} single-split.")
    if ok:
        print("  -> PROVISIONAL. Four evaluations on the same measurements that\n"
              "     produced §33b. A licence to test it on fresh data, NOT\n"
              "     permission to trust it or enable anything under it.")
    else:
        print("  -> This repository has no validated way to choose between\n"
              "     strategies. That is the honest state, and every future EDGE\n"
              "     claim has to carry it as a stated limitation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
