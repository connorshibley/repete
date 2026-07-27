#!/usr/bin/env python3
"""§31 — the CROSS-ASSET RISK gate. An EDGE claim, run on the ensemble.

Pre-registered in `knowledge/backtest_candidates.md` §31 on 2026-07-27, and
committed BEFORE this file existed. Read the registration first; the pass mark,
the arms and the honest prior (EDGE claims 0 for 5) are all fixed there and are
not renegotiable from here.

What it measures
----------------
Every strategy in this repo is a price-only rule over 38 heavily arbitraged
symbols, and `backtest_candidates.md:611` reports no OOS per-trade edge
distinguishable from zero across all five. §31 asks whether an input the price
series does not contain changes that: block new entries while high-yield credit
(HYG) is weakening against investment grade (LQD).

Cross-asset PRICES rather than FRED macro on purpose — FRED serves revised
values, and backtesting against revisions is lookahead that would invalidate
this gate quietly rather than fail it loudly.

Why a new runner
----------------
`gate_budget.py` is §27's, moves risk params, and hardcodes the CAPACITY test
(`comp.not_worse`). §31 moves an entry filter and is an EDGE claim, so it needs
`comp.significant`. Bending the old runner would falsify §27's register; a new
committed file keeps both gates re-executable exactly. Same reason §25's ad-hoc
heredoc is recorded as a process failure.

    python scripts/gate_cross_asset.py \
        --bars data/snapshots/bars_2020-01-01_2026-07-10.json.gz \
        --credit data/snapshots/credit_2020-01-01_2026-07-10.json.gz
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                     # noqa: E402
import significance as sig                                # noqa: E402

# Hashes recorded in the §31 registration. Verified before the run, because a
# gate scored on a file that has drifted is a gate that proves nothing — and
# §1–§13 are permanently unreproducible for exactly this reason.
EXPECTED_SHA = {
    "bars_2020-01-01_2026-07-10.json.gz":
        "6abb20b596b8900c",          # prefix only; full hash in MANIFEST.json
    "credit_2020-01-01_2026-07-10.json.gz":
        "9b8ac92c77137a819e62339f50e35b34f1b85e9e2be4f126c7dedd7ccb457621",
}

# (name, credit_sma_period). Baseline first. Discrete and pre-registered —
# there is no continuous knob here to slide toward a result.
ARMS = [
    ("baseline", 0),        # 0 disables the gate entirely
    ("sma50", 50),
    ("sma100", 100),
]


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_snapshots(bars_path: str, credit_path: str) -> None:
    """Refuse to run against a drifted snapshot. Loud, not advisory."""
    for path in (bars_path, credit_path):
        name = os.path.basename(path)
        want = EXPECTED_SHA.get(name)
        got = sha256_file(path)
        if want is None:
            print(f"  !! {name}: not in the registered set — verdict will not "
                  f"be comparable with §31 as registered", file=sys.stderr)
            continue
        if not got.startswith(want):
            raise SystemExit(
                f"SNAPSHOT DRIFT: {name}\n  registered {want}…\n  actual     "
                f"{got[:len(want)]}…\nRefusing to score §31 on data that is not "
                f"what the registration named.")
        print(f"  verified {name}  sha256 {got[:16]}…")


def cfg_for(base: dict, period: int) -> dict:
    cfg = copy.deepcopy(base)
    cfg["risk"]["credit_sma_period"] = period
    return cfg


def fmt(tag: str, r) -> str:
    return (f"  {tag:<24} ret {r.total_return_pct:+6.2f}%  "
            f"PF {r.profit_factor:5.3f}  maxDD {r.max_drawdown_pct:5.2f}%  "
            f"trades {r.n_trades:4d}  symbols {r.n_symbols_traded:3d}  "
            f"deploy {r.avg_deployment_pct:5.2f}%")


def slice_credit(credit: dict, upto_ts: str | None, from_ts: str | None) -> dict:
    """Credit bars restricted to a window.

    The IS/OOS split cuts the PRICE bars; the credit series has to be cut the
    same way or the OOS run would start with a 100-bar SMA already warmed by
    in-sample data. That is not lookahead in the trading sense — the live bot
    would have had that history — but it makes the two halves incomparable,
    which is worse for a gate whose whole job is the comparison.

    Warm-up is handled honestly instead: the OOS slice keeps `period` bars of
    lead-in before the first OOS price bar, so arm sma100 is not silently
    disabled for its first 100 sessions while sma50 is not.
    """
    out = {}
    for sym, rows in credit.items():
        sel = rows
        if from_ts is not None:
            sel = [b for b in sel if b["ts"] >= from_ts]
        if upto_ts is not None:
            sel = [b for b in sel if b["ts"] <= upto_ts]
        out[sym] = sel
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bars", required=True)
    p.add_argument("--credit", required=True)
    p.add_argument("--split", type=float, default=0.7)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--resamples", type=int, default=sig.DEFAULT_RESAMPLES)
    p.add_argument("--trials", default="memory/backtest_trials.jsonl")
    args = p.parse_args()

    print("§31 cross-asset risk gate | 3 arms | ensemble simulator")
    print("claim type: EDGE — per-trade expectancy, not exposure\n")
    print("-- SNAPSHOT VERIFICATION (before anything is scored) --")
    verify_snapshots(args.bars, args.credit)

    base_cfg = bt.load_config()
    sym_bars = bt.load_bars_file(args.bars)
    credit = bt.load_bars_file(args.credit)

    cut = {s: int(len(b) * args.split) for s, b in sym_bars.items()}
    is_bars = {s: b[:cut[s]] for s, b in sym_bars.items()}
    oos_bars = {s: b[cut[s]:] for s, b in sym_bars.items()}

    # Credit windows matched to the price windows, with lead-in for the OOS
    # half so the longest SMA is warm at the first OOS bar (see slice_credit).
    is_end = max(b[-1]["ts"] for b in is_bars.values())
    oos_start = min(b[0]["ts"] for b in oos_bars.values())
    max_period = max(period for _, period in ARMS)
    hyg = credit.get("HYG") or []
    lead_idx = max(0, len([b for b in hyg if b["ts"] < oos_start]) - max_period)
    lead_ts = hyg[lead_idx]["ts"] if hyg else None
    is_credit = slice_credit(credit, is_end, None)
    oos_credit = slice_credit(credit, None, lead_ts)

    print(f"\n  {len(sym_bars)} symbols | split {args.split:.0%} | "
          f"IS ends {is_end[:10]} | OOS starts {oos_start[:10]}")
    print(f"  credit lead-in for OOS: from {(lead_ts or 'n/a')[:10]} "
          f"({max_period} bars, so sma100 is warm at the first OOS bar)")

    print("\n-- IN-SAMPLE (selection happens here, and ONLY here) --")
    is_res = {}
    for name, period in ARMS:
        r = bt.simulate_ensemble(is_bars, cfg_for(base_cfg, period), args.cash,
                                 credit=is_credit)
        is_res[name] = r
        bt.append_trial(args.trials, {"phase": "in_sample", "arm": name,
                                      "section": "31_cross_asset", **r.summary()})
        print(fmt(name, r))

    # EDGE claim -> select IS on PROFIT FACTOR, which is the per-trade
    # expectancy measure. Selecting on total return would let an arm win by
    # holding more, which is the capacity result this gate is not claiming.
    cands = [n for n, _ in ARMS if n != "baseline"]
    winner = max(cands, key=lambda n: (is_res[n].profit_factor,
                                       is_res[n].total_return_pct))
    print(f"\nIS winner among candidates: {winner} "
          f"(selected on profit factor — the EDGE measure)")

    print("\n-- OUT-OF-SAMPLE (reported once) --")
    oos_res = {}
    for name, period in ARMS:
        r = bt.simulate_ensemble(oos_bars, cfg_for(base_cfg, period), args.cash,
                                 credit=oos_credit)
        oos_res[name] = r
        bt.append_trial(args.trials, {"phase": "oos", "arm": name,
                                      "section": "31_cross_asset", **r.summary()})
        tag = name + (" [BASE]" if name == "baseline"
                      else " [SELECTED]" if name == winner else " [not-selected]")
        print(fmt(tag, r))

    base, cand = oos_res["baseline"], oos_res[winner]

    print("\n-- PRE-REGISTERED CHECKS (selected arm vs baseline, OOS) --")
    gate_ok, gate_reasons = bt.enablement_gate(cand.summary())
    checks = {
        "(a) clears enablement_gate() in full": gate_ok,
        "(b) PF strictly > baseline":
            cand.profit_factor > base.profit_factor,
        "(c) maxDD <= baseline + 1.0pp":
            cand.max_drawdown_pct <= base.max_drawdown_pct + 1.0,
        "(d1) trades >= 15": cand.n_trades >= 15,
        "(d2) trades >= 60% of baseline":
            cand.n_trades >= 0.6 * base.n_trades,
    }
    for label, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    for why in gate_reasons:
        print(f"        enablement_gate: {why}")

    print("\n-- SIGNIFICANCE (EDGE test: significantly BETTER, K=3) --")
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
        print("  -> ADOPT is defensible on this snapshot (still not live "
              "evidence, and still one snapshot).")
    else:
        print("  -> REJECT. The incumbent stands and the gate is NOT enabled.")

    print("\n-- DIAGNOSTICS (informative, decide nothing) --")
    for name, _ in ARMS:
        r = oos_res[name]
        print(f"  {name:<10} trades {r.n_trades:4d}  "
              f"symbols {r.n_symbols_traded:3d}  "
              f"deploy {r.avg_deployment_pct:5.2f}%")
    print(f"  trades removed by the filter: "
          f"{base.n_trades - cand.n_trades} of {base.n_trades} "
          f"({(base.n_trades - cand.n_trades) / max(base.n_trades, 1):.0%})")
    print("\n  Registered before the run: if the filter removes trades roughly "
          "at random\n  with respect to outcome, PF stays flat while the count "
          "drops — clauses (b)\n  and (d) fail together, and that pattern is a "
          "REJECT, not a near miss.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
