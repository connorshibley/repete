#!/usr/bin/env python3
"""How much evidence is behind each strategy's headline number?

Profit factor and total return answer "did it make money on this data". They do
not answer "on how many independent events does that rest" — and those two
questions can give opposite rankings. This report asks the second one.

Three columns do the work:

  top3%       share of gross profit from the 3 best trades. High means the
              result rests on a handful of events, whatever the trade count says.
  PF ex-top3  profit factor with those 3 removed. If PF falls from 2.4 to 1.1,
              the strategy is three trades wearing a trench coat.
  median      the middle trade. A negative median with a positive total says
              the average is carried by the tail — true of real trend systems,
              and equally true of a fluke.

Then a moving-block bootstrap CI on mean P&L per trade (src/significance.py),
at both the uncorrected level and Bonferroni-corrected for the number of
strategies compared.

Fat tails are not automatically a defect: breakout and momentum systems are
*supposed* to win rarely and large, so concentration is expected there and only
alarming for a strategy claiming a high win rate. What concentration always
tells you is how confident the number deserves to make you.

    python scripts/edge_report.py --bars data/snapshots/bars_...json.gz
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402
import significance as sig                                # noqa: E402
import strategies                                         # noqa: E402


def concentration(pnls: list) -> dict:
    """Share of gross profit held by the biggest winners, and the profit factor
    that survives removing them."""
    ranked = sorted(pnls, reverse=True)
    gross_win = sum(p for p in ranked if p > 0)
    if not gross_win:
        return {"top1": 0.0, "top3": 0.0, "pf_ex_top3": 0.0,
                "median": ranked[len(ranked) // 2] if ranked else 0.0}
    rest = ranked[3:]
    win = sum(p for p in rest if p > 0)
    loss = -sum(p for p in rest if p < 0)
    return {
        "top1": ranked[0] / gross_win * 100,
        "top3": sum(ranked[:3]) / gross_win * 100,
        "pf_ex_top3": (win / loss) if loss else float("inf"),
        "median": ranked[len(ranked) // 2],
    }


def one_sample_ci(pnls: list, alpha: float, resamples: int, seed: int) -> tuple:
    """Bootstrap CI on the mean, plus the share of replicates above zero."""
    rng = random.Random(seed)
    means = sorted(sig._resample_mean(pnls, rng) for _ in range(resamples))
    lo = means[int(math.floor(alpha / 2 * resamples))]
    hi = means[min(int(math.ceil((1 - alpha / 2) * resamples)) - 1, resamples - 1)]
    return lo, hi, sum(1 for m in means if m > 0) / len(means)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--split", type=float, default=0.7)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--seed", type=int, default=20260723)
    p.add_argument("--strategies", nargs="+", default=None,
                   help="default: every registered strategy")
    args = p.parse_args()

    cfg = bt.load_config()
    names = args.strategies or sorted(strategies.REGISTRY)
    sym_bars = bt.load_bars_file(args.bars)
    cut = {s: int(len(b) * args.split) for s, b in sym_bars.items()}
    oos = {s: b[cut[s]:] for s, b in sym_bars.items()}

    print(f"OOS edge report — {len(sym_bars)} symbols, split {args.split:.0%}")
    print(f"bars: {args.bars}\n")

    rows = []
    for name in names:
        r = bt.simulate(oos, cfg, {}, args.cash, name)
        pnls = [t.pnl for t in r.trades]
        if not pnls:
            print(f"{name:<14} no closed trades")
            continue
        rows.append((name, r, pnls, concentration(pnls)))

    print(f"{'strategy':<14}{'trades':>7}{'PF':>7}{'top1%':>7}{'top3%':>7}"
          f"{'PFex3':>7}{'median$':>9}")
    for name, r, pnls, c in rows:
        print(f"{name:<14}{len(pnls):>7}{r.profit_factor:>7.3f}{c['top1']:>6.1f}%"
              f"{c['top3']:>6.1f}%{c['pf_ex_top3']:>7.3f}{c['median']:>9.2f}")

    k = max(1, len(rows))
    print(f"\nmean P&L per trade, block bootstrap ({args.resamples} resamples)")
    print(f"{'strategy':<14}{'mean$':>9}   {'95% CI (uncorrected)':<26}"
          f"  {'Bonferroni K=' + str(k):<24} P(>0)")
    for name, r, pnls, _ in rows:
        mean = sum(pnls) / len(pnls)
        lo1, hi1, pgt = one_sample_ci(pnls, 0.05, args.resamples, args.seed)
        lok, hik, _ = one_sample_ci(pnls, 0.05 / k, args.resamples, args.seed)
        u = f"[{lo1:+8.2f}, {hi1:+8.2f}]"
        b = f"[{lok:+8.2f}, {hik:+8.2f}]"
        flag = "EDGE" if lok > 0 else ("edge*" if lo1 > 0 else "-")
        print(f"{name:<14}{mean:>9.2f}   {u:<26}  {b:<24} {pgt:>5.1%}  {flag}")

    print("\nEDGE  = distinguishable from zero after correcting for the number "
          "of strategies compared.\nedge* = only at the uncorrected level; "
          "treat as a hypothesis.\n-     = not distinguishable from zero.")
    print("\nNone of this is live evidence. The OOS window has been mined "
          "repeatedly (METHOD NOTE 2);\nthese intervals describe THIS dataset, "
          "not the future. The live forward record is the holdout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
