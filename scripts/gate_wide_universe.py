#!/usr/bin/env python3
"""§32 — the WIDE-UNIVERSE CROSS-SECTIONAL FACTOR gate. An EDGE claim.

Pre-registered in `knowledge/backtest_candidates.md` §32 on 2026-07-27 and
committed BEFORE this file existed. Read the registration first: the six arms,
the shared risk block, the pass mark and the honest prior (EDGE claims 0 for 6)
are fixed there and are not renegotiable from here.

What is different about this gate
---------------------------------
Every section from §14 to §31 moved a RULE over the same 38 mega-caps, and
`:611` reports no OOS edge distinguishable from zero across all five strategies.
§32 moves the UNIVERSE instead: 500 names with a 92-point spread in trailing
returns, which is the dispersion cross-sectional ranking needs and which 38
mega-caps never had.

Two things this runner enforces that a hand-run heredoc would not
-----------------------------------------------------------------
1. The snapshot hash is checked before anything is scored. §1–§13 are
   permanently unreproducible because their snapshot was never committed.
2. Every arm gets the SAME risk block. If the baseline ran at the shipped
   `risk_per_trade_pct: 8.0` while candidates ran at 2.0, this would measure a
   sizing change wearing a factor's clothes.

    python scripts/gate_wide_universe.py \
        --bars data/snapshots/bars_wide_2020-01-01_2026-07-10.json.gz
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

EXPECTED_SHA = {
    "bars_wide_2020-01-01_2026-07-10.json.gz":
        "825116a6d66e4d991a1928c3a5b9aed653966f52547e91b6b4afec78e24413a7",
}

# Identical for every arm. See the §32 registration table for why each value
# moved and what it costs: this measures SIGNAL QUALITY, not deployability at
# the shipped rails.
SHARED_RISK = {
    "risk_per_trade_pct": 2.0,
    "max_position_pct": 2.5,
    "max_portfolio_heat_pct": 20.0,
    "max_trades_per_day": 50,
}

_XSMOM = {"enabled": True, "priority": 1, "skip_bars": 21,
          "exit_below_fraction": 0.5}
_LOWVOL = {"enabled": True, "priority": 2, "vol_period": 60,
           "exit_above_fraction": 0.5}

# (name, strategy-override dict). Baseline first. `None` means "leave the
# shipped strategies block alone" — the incumbent ensemble, wide universe.
ARMS = [
    ("baseline", None),
    ("xsmom-12-1-10", {"xsmom": {**_XSMOM, "rank_lookback_bars": 231,
                                 "buy_top_fraction": 0.10}}),
    ("xsmom-12-1-20", {"xsmom": {**_XSMOM, "rank_lookback_bars": 231,
                                 "buy_top_fraction": 0.20}}),
    ("xsmom-6-1-10", {"xsmom": {**_XSMOM, "rank_lookback_bars": 126,
                                "buy_top_fraction": 0.10}}),
    ("lowvol-60-10", {"lowvol": {**_LOWVOL, "buy_bottom_fraction": 0.10}}),
    ("both-10", {"xsmom": {**_XSMOM, "rank_lookback_bars": 231,
                           "buy_top_fraction": 0.10},
                 "lowvol": {**_LOWVOL, "buy_bottom_fraction": 0.10}}),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshot(path: str) -> None:
    name = os.path.basename(path)
    want = EXPECTED_SHA.get(name)
    got = sha256_file(path)
    if want is None:
        print(f"  !! {name}: not the registered snapshot — this verdict will "
              f"NOT be comparable with §32 as registered", file=sys.stderr)
        return
    if got != want:
        raise SystemExit(f"SNAPSHOT DRIFT: {name}\n  registered {want[:16]}…\n"
                         f"  actual     {got[:16]}…\nRefusing to score §32 on "
                         f"data that is not what the registration named.")
    print(f"  verified {name}  sha256 {got[:16]}…")


def cfg_for(base: dict, overrides: dict | None) -> dict:
    """Shared risk block always; strategies replaced only for candidate arms."""
    cfg = copy.deepcopy(base)
    cfg["risk"].update(SHARED_RISK)
    if overrides is not None:
        # Replace, not merge: a candidate arm runs ONLY its own strategies, so
        # a leftover ma_crossover cannot quietly supply the trades that make a
        # factor arm look like it works.
        cfg["strategies"] = copy.deepcopy(overrides)
    return cfg


def fmt(tag: str, r, secs: float | None = None) -> str:
    t = f"  {secs:5.1f}s" if secs is not None else ""
    return (f"  {tag:<26} ret {r.total_return_pct:+7.2f}%  "
            f"PF {r.profit_factor:5.3f}  maxDD {r.max_drawdown_pct:5.2f}%  "
            f"trades {r.n_trades:5d}  symbols {r.n_symbols_traded:3d}  "
            f"deploy {r.avg_deployment_pct:6.2f}%{t}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--split", type=float, default=0.7)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--trials", default=bt.DEFAULT_TRIALS_PATH)
    args = p.parse_args()

    print(f"§32 wide-universe cross-sectional factors | {len(ARMS)} arms | "
          f"ensemble simulator")
    print("claim type: EDGE — per-trade expectancy, not exposure or reach\n")
    print("-- SNAPSHOT VERIFICATION (before anything is scored) --")
    verify_snapshot(args.bars)

    base_cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    cut = {s: int(len(b) * args.split) for s, b in sym_bars.items()}
    is_bars = {s: b[:cut[s]] for s, b in sym_bars.items()}
    oos_bars = {s: b[cut[s]:] for s, b in sym_bars.items()}
    print(f"\n  {len(sym_bars)} symbols | split {args.split:.0%} | "
          f"shared risk block {SHARED_RISK}")

    print("\n-- IN-SAMPLE (selection happens here, and ONLY here) --")
    is_res = {}
    for name, over in ARMS:
        t0 = time.monotonic()
        r = bt.simulate_ensemble(is_bars, cfg_for(base_cfg, over), args.cash)
        is_res[name] = r
        bt.append_trial(args.trials, {"phase": "in_sample", "arm": name,
                                      "section": "32_wide_universe",
                                      **r.summary()})
        print(fmt(name, r, time.monotonic() - t0), flush=True)

    cands = [n for n, _ in ARMS if n != "baseline"]
    winner = max(cands, key=lambda n: (is_res[n].profit_factor,
                                       is_res[n].total_return_pct))
    print(f"\nIS winner among candidates: {winner} "
          f"(selected on profit factor — the EDGE measure)")

    print("\n-- OUT-OF-SAMPLE (reported once) --")
    oos_res = {}
    for name, over in ARMS:
        t0 = time.monotonic()
        r = bt.simulate_ensemble(oos_bars, cfg_for(base_cfg, over), args.cash)
        oos_res[name] = r
        bt.append_trial(args.trials, {"phase": "oos", "arm": name,
                                      "section": "32_wide_universe",
                                      **r.summary()})
        tag = name + (" [BASE]" if name == "baseline"
                      else " [SELECTED]" if name == winner else " [not-sel]")
        print(fmt(tag, r, time.monotonic() - t0), flush=True)

    base, cand = oos_res["baseline"], oos_res[winner]

    print("\n-- PRE-REGISTERED CHECKS (selected arm vs baseline, OOS) --")
    gate_ok, gate_reasons = bt.enablement_gate(cand.summary())
    checks = {
        "(a) clears enablement_gate() in full": gate_ok,
        "(b) PF strictly > baseline": cand.profit_factor > base.profit_factor,
        "(c) maxDD <= baseline + 1.0pp":
            cand.max_drawdown_pct <= base.max_drawdown_pct + 1.0,
        "(d) trades >= 30": cand.n_trades >= 30,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    for why in gate_reasons:
        print(f"        enablement_gate: {why}")

    print("\n-- SIGNIFICANCE (EDGE test: significantly BETTER, K=6) --")
    b_pnl, c_pnl = sig.trade_pnls(base), sig.trade_pnls(cand)
    if not b_pnl or not c_pnl:
        print("  SKIPPED — an arm produced no closed trades")
        stat_ok = False
    else:
        comp = sig.compare(b_pnl, c_pnl, n_comparisons=len(ARMS),
                           resamples=args.resamples)
        print("  " + comp.describe())
        stat_ok = comp.significant
        print(f"  clause (e) significantly better: "
              f"{'PASS' if stat_ok else 'FAIL'}")

    det_ok = all(checks.values())
    print(f"\nVERDICT: deterministic {'PASS' if det_ok else 'FAIL'} | "
          f"edge-significance {'PASS' if stat_ok else 'FAIL'}")
    if det_ok and stat_ok:
        print("  -> PROVISIONAL PASS. Registered constraint: survivorship "
              "inflates this, and\n     the arms ran on a risk block that is "
              "NOT the shipped one. Nothing is enabled\n     until it is "
              "confirmed on an untouched split at shippable settings.")
    else:
        print("  -> REJECT. The incumbent stands and nothing is enabled.")

    print("\n-- DIAGNOSTICS (informative, decide nothing) --")
    print("  IS -> OOS profit factor, per arm (a large drop is the "
          "survivorship/overfit signature):")
    for name, _ in ARMS:
        i, o = is_res[name], oos_res[name]
        print(f"    {name:<16} IS {i.profit_factor:5.3f} -> OOS "
              f"{o.profit_factor:5.3f}   ({o.profit_factor - i.profit_factor:+.3f})")
    print(f"\n  OOS buy-and-hold on this universe: "
          f"{base.buy_hold_return_pct:+.2f}% — every arm's return must be read "
          f"against it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
