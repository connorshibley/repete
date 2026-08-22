"""Quarterly strategy re-validation (2026-07-21) — REPORT ONLY.

Model-risk management: a strategy that passed the enablement gate once is not
validated forever. This reruns each ENABLED strategy's CURRENT live params
(no grid re-search — re-picking params here would be data mining) through the
walk-forward on recent data and reports whether the gate still passes.

It NEVER auto-disables anything: the live_kill rail (risk.py) is the fast,
realized-trades path; this is the slow drift detector that feeds the owner.
Results print + append a `revalidation` ledger event.

Usage:
    python src/revalidate.py                  # fetches ~2y of bars (network)
    python src/revalidate.py --bars-file F    # offline, from a frozen snapshot
"""
import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backtest

log = logging.getLogger("revalidate")

# params that live in the strategy config but aren't simulate() knobs
_NON_PARAMS = {"enabled", "priority"}


def live_params(cfg: dict, name: str) -> dict:
    return {k: v for k, v in (cfg.get("strategies", {}).get(name) or {}).items()
            if k not in _NON_PARAMS}


def revalidate(cfg: dict, sym_bars: dict,
               trials_path: str = backtest.DEFAULT_TRIALS_PATH) -> dict:
    """strategy -> {gate_pass, reasons, oos} for every enabled strategy."""
    out = {}
    for name, scfg in (cfg.get("strategies") or {}).items():
        if not scfg.get("enabled"):
            continue
        params = live_params(cfg, name)
        wf = backtest.walk_forward(sym_bars, cfg, [params], split=0.7,
                                   trials_path=trials_path,
                                   strategy_name=name)
        ok, reasons = backtest.enablement_gate(wf["oos_result"])
        out[name] = {"gate_pass": ok, "reasons": reasons,
                     "oos": {k: wf["oos_result"].get(k)
                             for k in ("total_return_pct", "profit_factor",
                                       "n_trades", "max_drawdown_pct")}}
    return out


def main():
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bars-file", help="frozen snapshot; skips all network")
    ap.add_argument("--days", type=int, default=730)
    args = ap.parse_args()

    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    if args.bars_file:
        sym_bars = backtest.load_bars_file(args.bars_file)
    else:
        from dotenv import load_dotenv
        load_dotenv()
        start = (date.today() - timedelta(days=args.days)).isoformat()
        sym_bars = backtest.fetch_bars(cfg["symbols"], start,
                                       date.today().isoformat())

    results = revalidate(cfg, sym_bars)
    print("=" * 62)
    print("STRATEGY RE-VALIDATION (report-only — live_kill is the fast path)")
    print("=" * 62)
    for name, r in results.items():
        o = r["oos"]
        print(f"{name}: {'GATE STILL PASSES' if r['gate_pass'] else 'GATE FAILS'}"
              f" | OOS {o['total_return_pct']:+.2f}% PF {o['profit_factor']}"
              f" n={o['n_trades']} dd {o['max_drawdown_pct']:.1f}%")
        for reason in r["reasons"]:
            print(f"    - {reason}")

    try:  # best-effort ledger note; a report must never crash on it
        from ledger import Ledger
        Ledger(cfg["memory"]["ledger_path"]).log_event(
            "revalidation",
            json.dumps({n: {"gate_pass": r["gate_pass"], **r["oos"]}
                        for n, r in results.items()}))
    except Exception as e:  # noqa: BLE001
        log.warning("revalidation ledger event failed: %s", e)


if __name__ == "__main__":
    main()
