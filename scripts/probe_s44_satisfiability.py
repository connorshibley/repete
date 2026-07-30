#!/usr/bin/env python3
"""§44 SATISFIABILITY PROBE — can halved sizing move drawdown at all?

Run BEFORE registering s44a-d, and the reason is §28: that gate was written,
and then a probe showed the mechanism could not move the metric the clauses
scored, so it was NEVER REGISTERED. A pass mark that no configuration can reach
is not a strict test, it is a broken one, and finding that out after the
registration is worse than finding it out before.

THE SPECIFIC WORRY. §41's decay arm failed `maxdd_within pp 3.0` in three of
four periods, drawdown roughly doubling because deployment rose ~8x at unchanged
sizing. §44's hypothesis is that cutting per-trade sizing buys that deployment
back at acceptable drawdown. But `max_open_positions: 0` means the book is
uncapped, and the census showed signals are abundant once the latch lifts — so
the bot may simply open TWICE AS MANY HALF-SIZED POSITIONS, leaving deployment
and drawdown unchanged. If that happens the knob is inert for this purpose and
§44 must not be registered.

WHAT THIS PROBE IS ALLOWED TO LOOK AT: deployment, max drawdown, trade count —
the mechanism. It DELIBERATELY DOES NOT PRINT RETURN, because return is clause
(b) and seeing it before registration would be selecting the experiment on its
outcome. The suppression is enforced below, not merely intended.

CONTAMINATION, DECLARED: this runs on 2022-2026, so that period's clause (a) and
(d) inputs are known to me before registration. The spec's failure_modes must
say so, and the other three periods stay genuinely unseen for the new arm.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                       # noqa: E402
import judge_model as jm                                    # noqa: E402

SNAPSHOT = "data/snapshots/bars_wide_2022-01-01_2026-07-24.json.gz"

DECAY = {"risk.drawdown_decay.enabled": True,
         "risk.drawdown_decay.grace_bars": 10,
         "risk.drawdown_decay.halflife_bars": 20}

ARMS = [
    ("baseline", {}),
    ("decay", DECAY),
    ("decay_half", {**DECAY, "risk.risk_per_trade_pct": 4.0}),
]


def _apply(cfg: dict, sets: dict) -> dict:
    import copy
    out = copy.deepcopy(cfg)
    for dotted, val in sets.items():
        node = out
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out


def main() -> int:
    base = bt.load_config()
    base.setdefault("backtest", {}).setdefault("judge_model", {})["enabled"] = True
    cal = jm.load_calibration()
    print(f"JUDGE ON — n={cal['n_judged_buys']} veto {cal['veto_rate']:.1%} "
          f"downsize {cal['downsize_rate']:.1%} mean scale {cal['mean_scale']:.3f}")
    print(f"snapshot: {SNAPSHOT}\n")
    print("RETURN IS SUPPRESSED ON PURPOSE — it is clause (b) of an "
          "unregistered spec.\n")

    bars = bt.load_bars_file(SNAPSHOT)
    rows = []
    for name, sets in ARMS:
        t0 = time.monotonic()
        r = bt.simulate_ensemble(bars, _apply(base, sets), 100_000.0)
        rows.append((name, r.max_drawdown_pct, r.avg_deployment_pct,
                     r.n_trades, r.deployment_zero_pct,
                     time.monotonic() - t0))

    print(f"{'arm':<12} {'maxDD':>8} {'deploy':>8} {'trades':>8} "
          f"{'in cash':>8}  secs")
    for n, dd, dep, tr, cash, secs in rows:
        print(f"{n:<12} {dd:>7.2f}% {dep:>7.2f}% {tr:>8,} {cash:>7.1f}%  {secs:.0f}")

    base_dd = rows[0][1]
    decay_dd, half_dd = rows[1][1], rows[2][1]
    bar = base_dd + 3.0
    print(f"\nmaxdd_within bar (baseline + 3.0pp): {bar:.2f}%")
    print(f"  decay      {decay_dd:.2f}%  -> "
          f"{'within' if decay_dd <= bar else 'OVER'}")
    print(f"  decay_half {half_dd:.2f}%  -> "
          f"{'within' if half_dd <= bar else 'OVER'}")

    moved = abs(decay_dd - half_dd)
    print(f"\nSATISFIABILITY: halving sizing moved max drawdown by "
          f"{moved:.2f}pp ({decay_dd:.2f}% -> {half_dd:.2f}%)")
    if moved < 0.5:
        print("  VERDICT: the knob is INERT for this purpose. Do NOT register "
              "§44 in this form — the bot re-deployed the freed capital into "
              "more positions and the clause cannot discriminate. See §28.")
        return 1
    print("  VERDICT: the knob moves the metric the clause scores. §44 is "
          "registrable — which says the experiment is well-posed, NOT that it "
          "will pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
