#!/usr/bin/env python3
"""§27 — the POSITION BUDGET gate. A CAPACITY claim, run on the ensemble.

Why this is a new script and not `gate_compare.py`
--------------------------------------------------
`gate_compare.py` predates §20. It drives `bt.simulate()` — ONE strategy at a
time — which §20 established is the wrong instrument: the live bot runs four
strategies competing for one cash balance, one slot ceiling and one daily trade
cap. It also hardcodes the EDGE test (`comp.significant`) and overrides
*strategy* params, while §27 moves *risk* params. Four mismatches; a new runner
is honest, bending the old one would not be.

It is COMMITTED rather than run as a throwaway heredoc on purpose: §25's runner
was ad-hoc, which means that gate cannot be re-executed exactly. Same class of
mistake as the snapshot METHOD NOTE 3 records.

What it measures
----------------
The effective position budget is set by TWO coupled knobs, each binding a
different strategy:

  * `risk.risk_per_trade_pct`   -> the notional path (tsmom, ma_crossover)
  * `risk.max_order_value_usd`  -> clamps the risk_sizing path (meanrev), which
                                   is why §11 found risk_pct 1.0/2.0/5.0
                                   byte-identical there

Moving one changes one strategy. So an arm moves BOTH in lockstep, which keeps
this a single-parameter grid rather than a 2-D parameter search.

    python scripts/gate_budget.py \
        --bars data/snapshots/bars_2020-01-01_2026-07-10.json.gz
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402
import significance as sig                                # noqa: E402

# SUPERSEDED by §29 (2026-07-26). None means the harness is live and its
# baseline arm must equal the shipped config; a string names what replaced it.
#
# ARMS below is deliberately NOT updated to match the new config. It is a
# PRE-REGISTERED record of what §27 committed to before it ran, and editing it
# after the fact to keep a test green would falsify the register that gives
# every verdict in backtest_candidates.md its weight. §27's rejection stands on
# these numbers, measured against a $2,000 clamp and 1%/trade sizing — a bot
# that no longer exists.
#
# Re-running this file today measures a phantom: §29 replaced both levers it
# tests (max_order_value_usd -> 0/disabled, risk_per_trade_pct -> 8.0).
SUPERSEDED_BY = "§29 (2026-07-26) — counts uncapped, order clamp removed"

# (name, risk_per_trade_pct, max_order_value_usd). Baseline first.
# Discrete and pre-registered — no continuous knob to slide toward a result.
ARMS = [
    ("baseline", 1.0, 2000),
    ("b2.0", 2.0, 2000),
    ("c2.5", 2.5, 2500),
    ("d3.0", 3.0, 3000),
    ("e4.0", 4.0, 4000),
]

# Names the audit found unreachable at the baseline budget (§26). Clause (e)
# is about the COUNT; these are printed so a passing count can be checked
# against the names it is supposed to have unlocked.
WATCH = ("LLY", "GS")


def cfg_for(base: dict, rpt: float, cap: int) -> dict:
    cfg = copy.deepcopy(base)
    cfg["risk"]["risk_per_trade_pct"] = rpt
    cfg["risk"]["max_order_value_usd"] = cap
    return cfg


def fmt(tag: str, r) -> str:
    return (f"  {tag:<24} ret {r.total_return_pct:+6.2f}%  "
            f"PF {r.profit_factor:5.3f}  maxDD {r.max_drawdown_pct:5.2f}%  "
            f"trades {r.n_trades:4d}  symbols {r.n_symbols_traded:3d}  "
            f"deploy {r.avg_deployment_pct:5.2f}%  heat-blk {r.n_heat_blocked:3d}")


def monotone(values: list) -> bool:
    """§23's check: does the result vary SMOOTHLY with the parameter?

    Two conditions, both required. The first draft of this function tested only
    unimodality and was therefore useless — a lone spike like
    `[1.0, 1.1, 9.0, 1.2, 1.0]` is perfectly unimodal, and that shape is
    precisely the fitted-parameter signature §23 was caught by. Recorded here
    because a decorative guard is worse than none: it looks like a control.

    1. **Unimodal.** Rises to the peak, falls after it, no zigzag. A series that
       reverses twice is not describing one underlying effect.

    2. **The winner is not a SPIKE.** Its lead over its better neighbour must
       not exceed the full spread of the remaining arms. If one arm beats its
       immediate neighbours by more than every other arm differs from every
       other, the parameter is not driving a smooth effect — that arm is
       fitting noise.

    Condition 2 needs a scale to compare against, and the spread of the other
    arms is used rather than a hand-picked constant precisely so this check has
    no tunable of its own. A guard with a fitted threshold would be the same
    mistake one level up.

    A peak at either END is always accepted: the true optimum may lie outside
    the tested grid, which is a reason to widen the grid, not evidence of
    overfitting.
    """
    if len(values) < 3:
        return True
    peak = values.index(max(values))
    if peak in (0, len(values) - 1):
        return True

    unimodal = (all(values[i] <= values[i + 1] for i in range(peak))
                and all(values[i] >= values[i + 1]
                        for i in range(peak, len(values) - 1)))
    if not unimodal:
        return False

    others = values[:peak] + values[peak + 1:]
    lead = values[peak] - max(values[peak - 1], values[peak + 1])
    return lead <= (max(others) - min(others))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--split", type=float, default=0.7)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--trials", default=bt.DEFAULT_TRIALS_PATH)
    args = p.parse_args()

    base_cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    cut = {s: int(len(b) * args.split) for s, b in sym_bars.items()}
    is_bars = {s: b[:cut[s]] for s, b in sym_bars.items()}
    oos_bars = {s: b[cut[s]:] for s, b in sym_bars.items()}

    print(f"§27 position budget | {len(ARMS)} arms | {len(sym_bars)} symbols | "
          f"split {args.split:.0%} | ensemble simulator")
    print(f"bars: {args.bars}")
    print("claim type: CAPACITY (METHOD NOTE 5) — reach, not per-trade edge\n")

    print("-- IN-SAMPLE (selection happens here, and ONLY here) --")
    is_res = {}
    for name, rpt, cap in ARMS:
        r = bt.simulate_ensemble(is_bars, cfg_for(base_cfg, rpt, cap), args.cash)
        is_res[name] = r
        bt.append_trial(args.trials, {"phase": "in_sample", "arm": name,
                                      "section": "27_budget", **r.summary()})
        print(fmt(name, r))

    # Capacity claim -> select IS on REACH first, return only as the tiebreak.
    # Selecting on return would quietly turn this back into an edge hunt.
    cands = [n for n, _, _ in ARMS if n != "baseline"]
    winner = max(cands, key=lambda n: (is_res[n].n_symbols_traded,
                                       is_res[n].total_return_pct))
    print(f"\nIS winner among candidates: {winner} "
          f"(selected on reach, return as tiebreak)")

    print("\n-- OUT-OF-SAMPLE --")
    oos_res = {}
    for name, rpt, cap in ARMS:
        r = bt.simulate_ensemble(oos_bars, cfg_for(base_cfg, rpt, cap), args.cash)
        oos_res[name] = r
        bt.append_trial(args.trials, {"phase": "oos", "arm": name,
                                      "section": "27_budget", **r.summary()})
        tag = name + (" [BASE]" if name == "baseline"
                      else " [SELECTED]" if name == winner else " [not-selected]")
        print(fmt(tag, r))

    base, cand = oos_res["baseline"], oos_res[winner]

    print("\n-- REACHABILITY (clause e — the actual capacity claim) --")
    gained = sorted(cand.symbols_traded - base.symbols_traded)
    lost = sorted(base.symbols_traded - cand.symbols_traded)
    print(f"  baseline {base.n_symbols_traded} symbols -> "
          f"{winner} {cand.n_symbols_traded} symbols")
    print(f"  gained ({len(gained)}): {gained or '-'}")
    print(f"  lost   ({len(lost)}): {lost or '-'}")
    for w in WATCH:
        print(f"  {w}: baseline {'YES' if w in base.symbols_traded else 'no ':<3}"
              f" -> {winner} {'YES' if w in cand.symbols_traded else 'no'}")

    print("\n-- PRE-REGISTERED CHECKS (selected arm vs baseline, OOS) --")
    checks = {
        "(a) return >= baseline - 0.25pp":
            cand.total_return_pct >= base.total_return_pct - 0.25,
        "(b1) PF >= 1.30":  cand.profit_factor >= 1.30,
        "(b2) PF >= baseline - 0.10":
            cand.profit_factor >= base.profit_factor - 0.10,
        "(c1) maxDD <= baseline x1.5":
            cand.max_drawdown_pct <= base.max_drawdown_pct * 1.5,
        "(c2) maxDD <= 3.0pp absolute": cand.max_drawdown_pct <= 3.0,
        "(d) trades >= 15": cand.n_trades >= 15,
        "(e) reach strictly increases":
            cand.n_symbols_traded > base.n_symbols_traded,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    print("\n-- SIGNIFICANCE (capacity test: NOT significantly worse) --")
    b_pnl, c_pnl = sig.trade_pnls(base), sig.trade_pnls(cand)
    if not b_pnl or not c_pnl:
        print("  SKIPPED — an arm produced no closed trades")
        stat_ok = False
    else:
        comp = sig.compare(b_pnl, c_pnl, n_comparisons=len(ARMS),
                           resamples=args.resamples)
        print("  " + comp.describe())
        stat_ok = comp.not_worse
        print(f"  capacity clause (f) ci_high > 0: "
              f"{'PASS' if stat_ok else 'FAIL'}")

    print("\n-- MONOTONICITY (§23) --")
    rets = [oos_res[n].total_return_pct for n, _, _ in ARMS]
    reach = [oos_res[n].n_symbols_traded for n, _, _ in ARMS]
    print(f"  return by arm: {[f'{v:+.2f}' for v in rets]}")
    print(f"  reach  by arm: {reach}")
    mono = monotone(rets)
    print(f"  [{'PASS' if mono else 'FAIL'}] return varies smoothly "
          f"(a lone interior peak is a fitted parameter)")

    det_ok = all(checks.values())
    print(f"\nVERDICT: deterministic {'PASS' if det_ok else 'FAIL'} | "
          f"capacity-significance {'PASS' if stat_ok else 'FAIL'} | "
          f"monotonicity {'PASS' if mono else 'FAIL'}")
    if det_ok and stat_ok and mono:
        print("  -> ADOPT is defensible on this snapshot (still not live evidence).")
    elif not mono:
        print("  -> REJECT: non-monotone winner is reported as an artifact "
              "regardless of its numbers (§23).")
    else:
        print("  -> REJECT.")

    print("\nNOTE, stated at registration: this gate STRUCTURALLY "
          "UNDER-MEASURES the fix.\n  simulate_ensemble has no judge, so the "
          "7 names lost live on a downsize\n  (53% of buys) are invisible here. "
          "It sees only LLY/GS. A marginal result\n  corresponds to a larger "
          "live benefit — recorded before the run so it\n  cannot be used "
          "afterwards to argue a failed gate into a pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
