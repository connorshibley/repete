#!/usr/bin/env python3
"""What does the CURRENT judge do with real signals, against the fitted one?

WHY THIS EXISTS. The judge moved to a self-hosted model on 2026-08-28 (§78,
divergence #25) and nothing measured it. The first question was not "is it
good" — it was "will it let anything through at all". `on_unavailable: block`
had already produced one bot that could not enter a trade; a judge that vetoes
everything produces the same bot for a different reason, and the config would
look correct either way.

WHAT THIS IS NOT. Not an agreement test. The prompts the previous judge saw
were never stored — the sidecar landed 2026-08-22, a day after that judge went
down — so no replay can recover them and no per-signal comparison is possible.
This replays real PAST signals under TODAY's context and reports the verdict
distribution. Two different judges, two different context vintages, same
signals. Read it as a characterization of disposition, never as agreement.

WHAT IT WRITES. Nothing. No ledger row, no judgment row, no order. It calls
llm.review_signal, which is pure with respect to storage.

    python scripts/judge_disposition.py --limit 20
    python scripts/judge_disposition.py --limit 40 --json
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

VERDICTS = ("approve", "downsize", "veto")


def load_reference(root: str) -> dict | None:
    """The fitted distribution to compare against, or None if absent.

    Read from knowledge/judge_calibration.json rather than pasted in as
    constants. A fitted rate copied into this file would keep printing after a
    refit moved it, and a comparison against a number that is no longer the
    reference is worse than no comparison — it reads as current.

    tests/test_judge_disposition.py greps this file for those values, so the
    rule is enforced rather than merely stated. (It first fired on this very
    docstring, which quoted one of them as an example.)
    """
    path = os.path.join(root, "knowledge", "judge_calibration.json")
    try:
        with open(path) as f:
            cal = json.load(f)
    except (OSError, ValueError):
        return None
    return {
        "veto_rate": cal.get("veto_rate"),
        "downsize_rate": cal.get("downsize_rate"),
        "mean_scale": cal.get("mean_scale"),
        "n": cal.get("n_judged_buys"),
        "fitted_context_version": cal.get("fitted_context_version"),
        "observed_from": cal.get("observed_from"),
        "observed_to": cal.get("observed_to"),
    }


def summarize(results: list[dict], reference: dict | None = None) -> dict:
    """Rates over the calls that actually reached a judge.

    `degraded` is counted and EXCLUDED from every rate, never folded in as a
    non-veto. A degradation is "no judge saw this", and counting it as an
    implicit approval is exactly the absent-vs-zero collapse this repo refuses
    everywhere else — it would understate the veto rate, which is the one
    number the caller is here to read.

    With no successful call the rates are None, not 0.0. "Nothing got through"
    and "nothing vetoed" are opposite findings and must never share a value.
    """
    degraded = [r for r in results if r.get("degraded")]
    judged = [r for r in results if not r.get("degraded")]
    counts = collections.Counter(r.get("verdict") for r in judged)
    n = len(judged)

    out = {
        "n_judged": n,
        "n_degraded": len(degraded),
        "counts": {v: counts.get(v, 0) for v in VERDICTS},
        "unknown_verdicts": {k: c for k, c in counts.items()
                             if k not in VERDICTS},
        "veto_rate": None, "downsize_rate": None, "approve_rate": None,
        "mean_scale_non_veto": None, "expected_exposure": None,
        "latency_p50": None,
    }
    if not n:
        return out

    out["veto_rate"] = counts.get("veto", 0) / n
    out["downsize_rate"] = counts.get("downsize", 0) / n
    out["approve_rate"] = counts.get("approve", 0) / n

    kept = [float(r["scale"]) for r in judged
            if r.get("verdict") != "veto"
            and isinstance(r.get("scale"), (int, float))]
    if kept:
        out["mean_scale_non_veto"] = statistics.mean(kept)
        # What actually reaches the market per signal: the chance of surviving
        # the judge times the size it survives at. Comparing veto rates alone
        # misses that a judge can be permissive and still deploy less.
        out["expected_exposure"] = (len(kept) / n) * statistics.mean(kept)

    lats = [r["seconds"] for r in results if isinstance(r.get("seconds"), (int, float))]
    if lats:
        out["latency_p50"] = statistics.median(lats)
    if reference:
        out["reference"] = reference
    return out


def spread(summaries: list[dict], key: str) -> dict:
    """min/median/max of one rate across repeated passes over the SAME signals.

    THE JUDGE IS NOT DETERMINISTIC. Two passes over the identical 20 signals on
    2026-08-28 returned veto_rate 0.300 and 0.150 — one of them also produced
    the run's only `approve`. A single pass reported as "the veto rate" is a
    draw presented as a distribution, and at n=20 the draw moves by more than
    the gap this measurement was made to size.

    So the instrument reports a range or it reports nothing useful. Values that
    are None (a pass where nothing was judged) are dropped rather than counted
    as zero, and a key with no usable values comes back all-None.
    """
    vals = sorted(v for v in (s.get(key) for s in summaries) if v is not None)
    if not vals:
        return {"min": None, "median": None, "max": None, "n_passes": 0}
    return {"min": vals[0], "median": statistics.median(vals),
            "max": vals[-1], "n_passes": len(vals)}


def _signals(db_path: str, limit: int) -> list[dict]:
    """Distinct real buy signals from the ledger, most recent first.

    One per (symbol, strategy): the same symbol judged ten times by the same
    strategy is one disposition sampled ten times, not ten samples, and letting
    it in would let the most-traded names set the rate.
    """
    import sqlite3
    seen, out = set(), []
    con = sqlite3.connect(db_path)
    try:
        rows = list(con.execute('select data from events where stream="ledger"'))
    finally:
        con.close()
    for (data,) in reversed(rows):
        try:
            r = json.loads(data)
        except ValueError:
            continue
        if r.get("action") != "buy" or not r.get("indicators"):
            continue
        key = (r.get("symbol"), r.get("strategy"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _fmt(x, ref=None):
    if x is None:
        return "     —"          # absent, and it must not read as zero
    s = f"{x:6.3f}"
    return s if ref is None else f"{s}   (fitted: {ref:.3f})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--repeat", type=int, default=1,
                    help="passes over the SAME signals; >1 reports a range "
                         "instead of a point estimate (the judge samples)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, args.config)) as f:
        cfg = yaml.safe_load(f)

    import llm
    import store
    from ledger import Ledger
    from memory import Memory

    store.configure(cfg)
    ledger = Ledger(cfg["memory"]["ledger_path"])
    memory = Memory(cfg, ledger)

    db = os.path.join(os.path.dirname(cfg["memory"]["ledger_path"]), "agent.db")
    sigs = _signals(db, args.limit)
    if not sigs:
        print("no replayable buy signals in the ledger", file=sys.stderr)
        return 1

    ref = load_reference(root)
    passes, all_rows = [], []
    for p in range(max(1, args.repeat)):
        results = []
        for i, r in enumerate(sigs):
            sig = types.SimpleNamespace(
                action="buy", symbol=r["symbol"],
                strategy=r.get("strategy") or "",
                reason=r.get("strategy_reason") or r.get("detail") or "",
                indicators=r["indicators"])
            t0 = time.time()
            rev = llm.review_signal(
                sig, memory.context_for_llm(symbol=r["symbol"],
                                            strategy=r.get("strategy")), cfg)
            row = {"pass": p, "symbol": r["symbol"],
                   "strategy": r.get("strategy"), "seconds": time.time() - t0,
                   "degraded": (rev.get("degraded_reason")
                                if rev.get("degraded") else None),
                   "verdict": rev.get("verdict"), "scale": rev.get("scale")}
            results.append(row)
            if not args.json and args.repeat == 1:
                tag = (f"DEGRADED {row['degraded']}" if row["degraded"]
                       else f"{row['verdict']:<9} scale={row['scale']}")
                print(f"[{i:2d}] {row['symbol']:<6} "
                      f"{str(row['strategy'] or '?'):<12} "
                      f"{row['seconds']:4.1f}s  {tag}")
        passes.append(summarize(results, ref))
        all_rows.extend(results)
        if not args.json and args.repeat > 1:
            print(f"pass {p}: veto={_fmt(passes[-1]['veto_rate'])} "
                  f"downsize={_fmt(passes[-1]['downsize_rate'])} "
                  f"approve={_fmt(passes[-1]['approve_rate'])} "
                  f"exposure={_fmt(passes[-1]['expected_exposure'])}")

    results = all_rows
    s = summarize(results, ref)
    if args.repeat > 1:
        s["spread"] = {k: spread(passes, k) for k in
                       ("veto_rate", "downsize_rate", "approve_rate",
                        "mean_scale_non_veto", "expected_exposure")}
    if args.json:
        print(json.dumps({"summary": s, "results": results}, indent=2))
        return 0

    print("\n" + "=" * 66)
    print(f"n_judged={s['n_judged']}  n_degraded={s['n_degraded']}  "
          f"latency_p50={_fmt(s['latency_p50'])}s")
    if ref:
        print(f"fitted reference: n={ref['n']} judged buys, "
              f"{ref['observed_from']} → {ref['observed_to']}, "
              f"context_version {ref['fitted_context_version']}")
    r = ref or {}
    print(f"  veto_rate      {_fmt(s['veto_rate'], r.get('veto_rate'))}")
    print(f"  downsize_rate  {_fmt(s['downsize_rate'], r.get('downsize_rate'))}")
    print(f"  approve_rate   {_fmt(s['approve_rate'])}")
    print(f"  mean_scale     {_fmt(s['mean_scale_non_veto'], r.get('mean_scale'))}"
          "   (non-veto only)")
    print(f"  exposure/sig   {_fmt(s['expected_exposure'])}"
          "   P(survives) x size")
    if s.get("spread"):
        print(f"\n  across {args.repeat} passes over the SAME signals "
              f"(the judge samples — a single pass is a draw):")
        for k, sp in s["spread"].items():
            if sp["median"] is None:
                print(f"    {k:<20}      —")
            else:
                print(f"    {k:<20} {sp['min']:.3f} .. {sp['max']:.3f}"
                      f"   median {sp['median']:.3f}")
    if s["unknown_verdicts"]:
        print(f"  UNKNOWN VERDICTS: {s['unknown_verdicts']}")
    print("\nDisposition, not agreement: different judge AND different context\n"
          "vintage. See docs/divergences.md #25.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
