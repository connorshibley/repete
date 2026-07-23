#!/usr/bin/env python3
"""Run a pre-registered gate section: baseline vs candidate arms, honestly.

The protocol this enforces
--------------------------
1. Every arm runs IN-SAMPLE.
2. The winner is picked **on in-sample only**. Choosing by OOS is the exact
   mistake METHOD NOTE 2 documents — it turns the holdout into a selection set.
3. Baseline and the IS winner then run OUT-OF-SAMPLE.
4. The OOS difference goes through a Bonferroni-corrected block bootstrap
   (`src/significance.py`) with K = number of arms tried.

Every arm's OOS number is printed too, but flagged NOT-SELECTED — visibility
without licence to cherry-pick. All arms land in the trials log either way.

    python scripts/gate_compare.py --strategy meanrev \
        --bars data/snapshots/bars_2020-01-01_2026-07-10.json.gz \
        --arm "baseline:" \
        --arm "hold5:max_hold_days=5" \
        --arm "hold10:max_hold_days=10"

Arm syntax: NAME:key=value[,key=value...]. An empty value list is the baseline
(live config as-is). Values parse as int, then float, then bool, else string.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402
import significance as sig                                # noqa: E402


def parse_arm(spec: str) -> tuple[str, dict]:
    name, _, rest = spec.partition(":")
    params: dict = {}
    for piece in filter(None, (p.strip() for p in rest.split(","))):
        key, _, raw = piece.partition("=")
        params[key.strip()] = coerce(raw.strip())
    return name.strip(), params


def coerce(raw: str):
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def fmt(tag: str, r) -> str:
    return (f"  {tag:<22} ret {r.total_return_pct:+6.2f}%  "
            f"PF {r.profit_factor:5.3f}  maxDD {r.max_drawdown_pct:5.2f}%  "
            f"trades {r.n_trades:4d}  win {r.win_rate:.0%}  "
            f"deploy {r.avg_deployment_pct:5.2f}%")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--strategy", required=True)
    p.add_argument("--bars", required=True)
    p.add_argument("--arm", action="append", required=True,
                   help="NAME:key=val,... (repeatable; include a baseline arm)")
    p.add_argument("--split", type=float, default=0.7)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--baseline", default="baseline",
                   help="name of the arm to treat as the incumbent")
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--trials", default="memory/backtest_trials.jsonl")
    args = p.parse_args()

    cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    arms = [parse_arm(a) for a in args.arm]
    names = [n for n, _ in arms]
    if args.baseline not in names:
        p.error(f"no arm named {args.baseline!r}; got {names}")

    cut = {s: int(len(b) * args.split) for s, b in sym_bars.items()}
    is_bars = {s: b[:cut[s]] for s, b in sym_bars.items()}
    oos_bars = {s: b[cut[s]:] for s, b in sym_bars.items()}

    print(f"gate section: {args.strategy} | {len(arms)} arms | "
          f"{len(sym_bars)} symbols | split {args.split:.0%}")
    print(f"bars: {args.bars}")

    print("\n-- IN-SAMPLE (selection happens here, and ONLY here) --")
    is_res = {}
    for name, params in arms:
        r = bt.simulate(is_bars, cfg, params, args.cash, args.strategy)
        is_res[name] = r
        bt.append_trial(args.trials, {"phase": "in_sample", "arm": name,
                                      "section": "gate_compare", **r.summary()})
        print(fmt(name, r))

    candidates = [n for n in names if n != args.baseline]
    winner = max(candidates,
                 key=lambda n: (is_res[n].total_return_pct, is_res[n].profit_factor))
    print(f"\nIS winner among candidates: {winner}")

    print("\n-- OUT-OF-SAMPLE --")
    oos_res = {}
    for name, params in arms:
        r = bt.simulate(oos_bars, cfg, params, args.cash, args.strategy)
        oos_res[name] = r
        bt.append_trial(args.trials, {"phase": "oos", "arm": name,
                                      "section": "gate_compare", **r.summary()})
        tag = name
        if name == args.baseline:
            tag += " [BASE]"
        elif name == winner:
            tag += " [SELECTED]"
        else:
            tag += " [not-selected]"
        print(fmt(tag, r))

    base, cand = oos_res[args.baseline], oos_res[winner]
    print("\n-- PRE-REGISTERED CHECKS (selected arm vs baseline, OOS) --")
    checks = {
        "return >= baseline": cand.total_return_pct >= base.total_return_pct,
        "PF >= 1.30 (standing bar)": cand.profit_factor >= 1.30,
        "PF >= baseline - 0.15": cand.profit_factor >= base.profit_factor - 0.15,
        "maxDD <= baseline x1.5": cand.max_drawdown_pct <= base.max_drawdown_pct * 1.5,
        "maxDD <= 3.0pp absolute": cand.max_drawdown_pct <= 3.0,
        "trades >= 15": cand.n_trades >= 15,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    print("\n-- SIGNIFICANCE (block bootstrap, Bonferroni K = #arms) --")
    b_pnl, c_pnl = sig.trade_pnls(base), sig.trade_pnls(cand)
    if not b_pnl or not c_pnl:
        print("  SKIPPED — an arm produced no closed trades")
        stat_ok = False
    else:
        comp = sig.compare(b_pnl, c_pnl, n_comparisons=len(arms),
                           resamples=args.resamples)
        print("  " + comp.describe())
        stat_ok = comp.significant

    det_ok = all(checks.values())
    print(f"\nVERDICT: deterministic {'PASS' if det_ok else 'FAIL'} | "
          f"significance {'PASS' if stat_ok else 'NOT SHOWN'}")
    if det_ok and stat_ok:
        print("  -> ADOPT is defensible on this snapshot (still not live evidence).")
    elif det_ok:
        print("  -> Rules pass but the edge is inside the noise band. Adopting "
              "this is a judgement call, not a result. Prefer the incumbent.")
    else:
        print("  -> REJECT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
