#!/usr/bin/env python3
"""§33 — does walk-forward selection PREDICT? A METHOD claim.

Pre-registered in `knowledge/backtest_candidates.md` §33 on 2026-07-27, before
this file existed. Read it first: the folds, the three metrics and the pass mark
are fixed there, as is the remedy if this fails.

What this tests
---------------
Not a strategy. THE PROCEDURE every section from §14 onward has relied on:

    select the best arm in-sample, report its out-of-sample number

§32 measured Spearman −0.543 between IS and OOS profit factor across six arms.
At n=6 that is not significant in either direction — which is the problem. If
selection carries no information then every rejection still stands (the bar was
not cleared regardless), but any PASS would be a coin flip wearing a method's
clothes, and hunting more arms only buys more chances to find one.

This runner reuses §32's arms verbatim, imported from `gate_wide_universe.py`
rather than copied. Two definitions of "the arms" drifting apart would make the
method test measure a different family than the section it is auditing.

    python scripts/gate_fold_stability.py \\
        --bars data/snapshots/bars_wide_2020-01-01_2026-07-10.json.gz
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402

# Expanding origin: (is_end, oos_end) as bar indices. Expanding rather than
# rolling because it matches deployment — the bot always has all history up to
# today, never a sliding window that forgets 2020.
FOLDS = [(600, 800), (800, 1000), (1000, 1200), (1200, 1400), (1400, None)]


def _load_arms():
    """§32's arms, imported not copied."""
    path = os.path.join(os.path.dirname(__file__), "gate_wide_universe.py")
    spec = importlib.util.spec_from_file_location("gate_wide_universe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def spearman(a: dict, b: dict) -> float:
    """Rank correlation over the same keys. n is 6 here — small, and the
    registration says so rather than dressing it up."""
    keys = list(a)
    ra = {s: i for i, s in enumerate(sorted(keys, key=lambda k: a[k]))}
    rb = {s: i for i, s in enumerate(sorted(keys, key=lambda k: b[k]))}
    n = len(keys)
    d2 = sum((ra[s] - rb[s]) ** 2 for s in keys)
    return 1 - 6 * d2 / (n * (n * n - 1))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--trials", default="memory/backtest_trials.jsonl")
    args = p.parse_args()

    gate = _load_arms()
    arms = gate.ARMS
    cands = [n for n, _ in arms if n != "baseline"]

    print("§33 fold stability | claim type: METHOD")
    print("testing the SELECTION PROCEDURE, not a strategy. Nothing here can "
          "enable anything.\n")
    gate.verify_snapshot(args.bars)

    base_cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    n_bars = max(len(b) for b in sym_bars.values())
    print(f"\n  {len(sym_bars)} symbols | {n_bars} bars | {len(FOLDS)} "
          f"expanding-origin folds | {len(arms)} arms "
          f"= {len(FOLDS) * len(arms) * 2} simulations\n")

    spearmans, lifts, hits = [], [], []
    for fi, (is_end, oos_end) in enumerate(FOLDS, 1):
        stop = oos_end if oos_end is not None else n_bars
        is_bars = {s: b[:is_end] for s, b in sym_bars.items()}
        oos_bars = {s: b[is_end:stop] for s, b in sym_bars.items()}
        print(f"-- FOLD {fi}: IS bars 0–{is_end}, OOS bars {is_end}–{stop} --",
              flush=True)

        is_pf, oos_pf = {}, {}
        for name, over in arms:
            t0 = time.monotonic()
            cfg = gate.cfg_for(base_cfg, over)
            r_is = bt.simulate_ensemble(is_bars, cfg, args.cash)
            r_oos = bt.simulate_ensemble(oos_bars, cfg, args.cash)
            is_pf[name], oos_pf[name] = r_is.profit_factor, r_oos.profit_factor
            for phase, r in (("in_sample", r_is), ("oos", r_oos)):
                bt.append_trial(args.trials,
                                {"phase": phase, "arm": name, "fold": fi,
                                 "section": "33_fold_stability", **r.summary()})
            print(f"    {name:<16} IS PF {r_is.profit_factor:6.3f}  "
                  f"OOS PF {r_oos.profit_factor:6.3f}  "
                  f"({time.monotonic() - t0:4.0f}s)", flush=True)

        rho = spearman(is_pf, oos_pf)
        picked = max(cands, key=lambda n: is_pf[n])
        cand_oos = [oos_pf[n] for n in cands]
        lift = oos_pf[picked] - statistics.mean(cand_oos)
        rank = sorted(cand_oos, reverse=True).index(oos_pf[picked]) + 1
        hit = rank <= len(cands) / 2
        spearmans.append(rho)
        lifts.append(lift)
        hits.append(hit)
        print(f"    -> selected {picked}; OOS rank {rank}/{len(cands)}; "
              f"spearman {rho:+.3f}; lift {lift:+.3f}; "
              f"{'HIT' if hit else 'MISS'}\n", flush=True)

    mean_rho = statistics.mean(spearmans)
    mean_lift = statistics.mean(lifts)
    n_hits = sum(hits)

    print("-- PRE-REGISTERED PASS MARK --")
    checks = {
        f"(a) mean spearman > 0  [{mean_rho:+.3f}]": mean_rho > 0,
        f"(b) mean selection lift > 0  [{mean_lift:+.3f}]": mean_lift > 0,
        f"(c) hit rate >= 4/5  [{n_hits}/{len(FOLDS)}]": n_hits >= 4,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"\n  per-fold spearman: {[f'{r:+.3f}' for r in spearmans]}")
    print(f"  per-fold lift:     {[f'{v:+.3f}' for v in lifts]}")

    ok = all(checks.values())
    print(f"\nVERDICT: the walk-forward selection procedure is "
          f"{'VALIDATED' if ok else 'NOT VALIDATED'} on this data.")
    if ok:
        print("  -> §14–§32 verdicts stand as written; the selection step "
              "carries information.")
    else:
        print("  -> Single-split selection is RETIRED. The registered remedy "
              "is fold-majority\n     selection (top half in >=60% of folds, "
              "pooled out-of-fold reporting),\n     pre-registered separately "
              "as §34. Prior REJECTIONS are unaffected: the bar\n     was not "
              "cleared regardless of what the selector did.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
