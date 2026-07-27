#!/usr/bin/env python3
"""§35 — one pre-specified hypothesis, no selection.

Pre-registered in `knowledge/backtest_candidates.md` §35 on 2026-07-27, before
this file existed.

What makes this runner different from every other gate here
-----------------------------------------------------------
**There is no selection step.** §33b and §34 established that choosing the
in-sample winner carries no information — single-split, fold-majority and an
oracle holding the future are all indistinguishable from random on this data.
So this script has no `winner = max(...)` line, no in-sample phase, and no
family to choose from. Two configs run once on one period and the numbers are
whatever they are.

If the hypothesis fails there is no second arm to fall back on. That is the
design, not an oversight: a verdict that cannot be produced by choosing cannot
be an artifact of choosing.

K = 8 in the significance test, not 1. Only one comparison is made, but this is
the eighth EDGE claim in this repository and family-wise error accumulates
across a research programme even when the hypotheses differ.

    python scripts/gate_s35_momentum.py \\
        --bars data/snapshots/bars_wide_2014-01-01_2019-12-31.json.gz
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402
import significance as sig                                # noqa: E402

EXPECTED_SHA = (
    "bars_wide_2014-01-01_2019-12-31.json.gz",
    "dca8dc85b8770f74697262750b767e796635f5b481c54efb475b12d9687916cd",
)

# Identical to §32's, so a difference here cannot be a sizing difference.
SHARED_RISK = {
    "risk_per_trade_pct": 2.0,
    "max_position_pct": 2.5,
    "max_portfolio_heat_pct": 20.0,
    "max_trades_per_day": 50,
}

# Jegadeesh & Titman (1993). Published thirty-three years ago; not tuned here.
HYPOTHESIS = {
    "xsmom": {"enabled": True, "priority": 1,
              "rank_lookback_bars": 231, "skip_bars": 21,
              "buy_top_fraction": 0.10, "exit_below_fraction": 0.5},
}

# The cumulative EDGE-claim count entering §35, per the registration.
K_FAMILYWISE = 8


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(path: str) -> None:
    name, want = EXPECTED_SHA
    got = sha256_file(path)
    if os.path.basename(path) != name:
        print(f"  !! {os.path.basename(path)} is not the registered snapshot — "
              f"this verdict is not §35", file=sys.stderr)
        return
    if got != want:
        raise SystemExit(f"SNAPSHOT DRIFT: {name}\n  registered {want[:16]}…\n"
                         f"  actual     {got[:16]}…\nRefusing to score §35.")
    print(f"  verified {name}  sha256 {got[:16]}…")


def cfg_for(base: dict, overrides: dict | None) -> dict:
    cfg = copy.deepcopy(base)
    cfg["risk"].update(SHARED_RISK)
    if overrides is not None:
        cfg["strategies"] = copy.deepcopy(overrides)
    return cfg


def fmt(tag: str, r, secs: float) -> str:
    return (f"  {tag:<22} ret {r.total_return_pct:+7.2f}%  "
            f"PF {r.profit_factor:5.3f}  maxDD {r.max_drawdown_pct:5.2f}%  "
            f"trades {r.n_trades:5d}  symbols {r.n_symbols_traded:3d}  "
            f"deploy {r.avg_deployment_pct:6.2f}%  ({secs:.0f}s)")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--trials", default="memory/backtest_trials.jsonl")
    args = p.parse_args()

    print("§35 pre-specified momentum | claim type: EDGE | NO SELECTION STEP")
    print("one hypothesis, fixed before this file existed, tested once\n")
    verify(args.bars)

    base_cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    print(f"\n  {len(sym_bars)} symbols | whole period, no IS/OOS split "
          f"(nothing is selected, so nothing needs splitting)\n")

    results = {}
    for name, over in (("baseline", None), ("xsmom-12-1", HYPOTHESIS)):
        t0 = time.monotonic()
        r = bt.simulate_ensemble(sym_bars, cfg_for(base_cfg, over), args.cash)
        results[name] = r
        bt.append_trial(args.trials, {"phase": "single", "arm": name,
                                      "section": "35_prespecified",
                                      **r.summary()})
        print(fmt(name, r, time.monotonic() - t0), flush=True)

    base, cand = results["baseline"], results["xsmom-12-1"]
    print(f"\n  buy-and-hold on this universe: "
          f"{base.buy_hold_return_pct:+.2f}%  "
          f"(maxDD {base.buy_hold_max_drawdown_pct:.1f}%)")

    print("\n-- PRE-REGISTERED CHECKS --")
    gate_ok, reasons = bt.enablement_gate(cand.summary())
    checks = {
        "(a) clears enablement_gate() in full": gate_ok,
        "(b) PF strictly > baseline": cand.profit_factor > base.profit_factor,
        "(c) maxDD <= baseline + 1.0pp":
            cand.max_drawdown_pct <= base.max_drawdown_pct + 1.0,
        "(d) trades >= 30": cand.n_trades >= 30,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    for why in reasons:
        print(f"        enablement_gate: {why}")

    print(f"\n-- SIGNIFICANCE (EDGE test, K={K_FAMILYWISE} family-wise) --")
    b_pnl, c_pnl = sig.trade_pnls(base), sig.trade_pnls(cand)
    if not b_pnl or not c_pnl:
        print("  SKIPPED — an arm produced no closed trades")
        stat_ok = False
    else:
        comp = sig.compare(b_pnl, c_pnl, n_comparisons=K_FAMILYWISE,
                           resamples=args.resamples)
        print("  " + comp.describe())
        stat_ok = comp.significant
        print(f"  clause (e) significantly better: "
              f"{'PASS' if stat_ok else 'FAIL'}")

    ok = all(checks.values()) and stat_ok
    print(f"\nVERDICT: deterministic {'PASS' if all(checks.values()) else 'FAIL'}"
          f" | significance {'PASS' if stat_ok else 'FAIL'}")
    if ok:
        print("  -> PROVISIONAL PASS. Registered constraints stand: twelve years\n"
              "     of survivorship bias run in this candidate's favour, and the\n"
              "     period contains no COVID crash and no 2022 bear. NOT enabled,\n"
              "     and no closer to the 30-trade live gate.")
    else:
        print("  -> REJECT. Nothing is enabled and there is no second arm to try;\n"
              "     that was the design.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
