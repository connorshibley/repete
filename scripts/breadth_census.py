#!/usr/bin/env python3
"""How many INDEPENDENT bets is the book actually taking? — §49, 2026-08-03

Grinold's fundamental law says risk-adjusted return has two levers and only
two:

    IR = IC x sqrt(BR)

IC is skill — the correlation between what you predict and what happens. BR is
BREADTH, the number of INDEPENDENT decisions. Thirteen EDGE claims have all
been attempts to raise IC. Nothing here has ever measured BR.

And nominal breadth is not breadth. `config.yaml` lists 38 symbols, but four of
them are broad index ETFs (SPY/QQQ/DIA/IWM), four more are sector ETFs, and the
remaining thirty are US large caps that mostly rise and fall together. Holding
thirty-eight names that move as one is one bet, not thirty-eight, and
sqrt(38)=6.16 against sqrt(2)=1.41 is a 4.4x difference in the IR ceiling that
no amount of signal work can recover.

The existing guard does not measure this. `correlation_cap` is PAIRWISE
(`max_correlated: 2` at `threshold: 0.85`, entries only): it asks "do two names
I already hold correlate with this one above 0.85", which says nothing about
the concentration of the book as a whole. §40 measured that the sibling heat
cap bound ZERO times. §22 recorded that symbols early in `config.yaml` get
systematic first refusal on scarce slots, so SPY/QQQ/DIA/IWM lead the list and
index ETFs are structurally favoured — "an arbitrary bias nobody chose".

WHAT IS COMPUTED

From the correlation matrix C of daily returns, with eigenvalues normalised to
sum to 1 (p_i), two standard concentration measures:

  * ENB_entropy       = exp(-sum p_i ln p_i)   -- perplexity of the spectrum
  * ENB_participation = 1 / sum p_i^2          -- inverse Herfindahl

Both equal N for N uncorrelated assets and 1 for N identical ones. They answer
different-shaped questions and are reported together because agreeing on a
number is weak evidence and disagreeing is informative.

Also reported: mean pairwise correlation, the share of pairs at or above the
`correlation_cap` threshold, and the implied IR ceiling ratio.

    python scripts/breadth_census.py                    # live universe
    python scripts/breadth_census.py --live-book        # only what is held now

It is a MEASUREMENT and produces no verdict. It cannot support or reject any
claim, changes no config value, and licenses nothing. If the book turns out to
be concentrated, WIDENING IT IS A SEPARATE PRE-REGISTERED DECISION — the same
rule census.py states about rails, for the same reason.

SURVIVORSHIP NOTE (§48). The snapshots are built from today's index membership,
which inflates RETURNS enormously (+130pp in 2000-2006; see
scripts/survivorship_check.py). Correlation structure is far less sensitive to
that than return levels are — but survivors are plausibly more correlated with
each other than a full cohort would be, so a breadth number computed here is
more likely to be an UNDER-estimate of true independence than an over-estimate.
Stated so the bias direction is on the record before anyone reads the output.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys

import numpy as np

DEFAULT_SNAPSHOT = "data/snapshots/bars_wide_2022-01-01_2026-07-24.json.gz"


def load_returns(path: str, symbols: list[str]) -> tuple[np.ndarray, list[str]]:
    """Daily simple returns aligned on dates every symbol shares.

    Inner join on timestamps rather than forward-filling: a filled gap invents
    a zero return, which drags correlations toward zero and would flatter
    breadth — the exact direction that would make this measurement comfortable.
    """
    with gzip.open(path) as f:
        data = json.load(f)

    series = {}
    for sym in symbols:
        bars = data.get(sym)
        if not bars or len(bars) < 30:
            continue
        series[sym] = {b["ts"][:10]: float(b["close"]) for b in bars
                       if b.get("close")}

    if not series:
        return np.empty((0, 0)), []

    common = set.intersection(*(set(v) for v in series.values()))
    dates = sorted(common)
    if len(dates) < 30:
        return np.empty((0, 0)), []

    kept = sorted(series)
    prices = np.array([[series[s][d] for d in dates] for s in kept], float)
    rets = np.diff(prices, axis=1) / prices[:, :-1]
    return rets, kept


def effective_bets(corr: np.ndarray) -> dict:
    """Effective number of independent bets from the correlation spectrum."""
    vals = np.linalg.eigvalsh(corr)
    vals = np.clip(vals, 1e-12, None)      # symmetric PSD; clip numerical dust
    p = vals / vals.sum()
    entropy = float(np.exp(-(p * np.log(p)).sum()))
    participation = float(1.0 / (p ** 2).sum())
    return {"enb_entropy": round(entropy, 2),
            "enb_participation": round(participation, 2),
            "top_eigenvalue_share_pct": round(100 * float(p.max()), 1)}


def measure(path: str, symbols: list[str], threshold: float) -> dict:
    rets, kept = load_returns(path, symbols)
    if rets.size == 0:
        return {"error": "not enough overlapping data", "n": 0}

    corr = np.corrcoef(rets)
    n = len(kept)
    off = corr[np.triu_indices(n, k=1)]

    out = {"snapshot": os.path.basename(path), "n_nominal": n,
           "n_days": rets.shape[1],
           "mean_pairwise_corr": round(float(off.mean()), 3),
           "median_pairwise_corr": round(float(np.median(off)), 3),
           "pairs_at_or_above_threshold_pct":
               round(100 * float((off >= threshold).mean()), 2),
           "threshold": threshold}
    out.update(effective_bets(corr))

    # The point of the exercise. IR = IC x sqrt(BR): what the concentration
    # costs, expressed as the factor by which the achievable IR is reduced
    # against a hypothetical book of n INDEPENDENT bets.
    enb = out["enb_entropy"]
    out["ir_ceiling_ratio"] = round(math.sqrt(enb / n), 3) if n else None
    out["ir_ceiling_note"] = (
        f"sqrt({enb})/sqrt({n}) = {out['ir_ceiling_ratio']} — the fraction of "
        f"an independent book's IR ceiling this universe can reach")
    return out


def _live_book() -> list[str]:
    """Symbols currently held, from the ledger's newest positions_mark."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    path = cfg.get("memory", {}).get("ledger_path", "memory/ledger.jsonl")
    held: list[str] = []
    with open(path) as f:
        for line in f:
            if '"positions_mark"' not in line:
                continue
            try:
                rec = json.loads(line)
                held = sorted(json.loads(rec["detail"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return held


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", default=DEFAULT_SNAPSHOT)
    ap.add_argument("--live-book", action="store_true",
                    help="measure only the symbols currently held")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    threshold = ((cfg.get("risk") or {}).get("correlation_cap") or {}).get(
        "threshold", 0.85)

    symbols = _live_book() if args.live_book else cfg["symbols"]
    label = "LIVE BOOK" if args.live_book else "CONFIGURED UNIVERSE"
    out = measure(args.snapshot, symbols, threshold)
    out["scope"] = label

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    if out.get("error"):
        print(f"{label}: {out['error']}")
        return 1

    print(f"{label} — {out['snapshot']} ({out['n_days']} days)\n")
    print(f"  nominal positions            {out['n_nominal']}")
    print(f"  effective bets (entropy)     {out['enb_entropy']}")
    print(f"  effective bets (participation) {out['enb_participation']}")
    print(f"  first eigenvalue             {out['top_eigenvalue_share_pct']}% "
          f"of total variance")
    print()
    print(f"  mean pairwise correlation    {out['mean_pairwise_corr']}")
    print(f"  median pairwise correlation  {out['median_pairwise_corr']}")
    print(f"  pairs >= {out['threshold']} (the cap)      "
          f"{out['pairs_at_or_above_threshold_pct']}%")
    print()
    print(f"  IR ceiling vs an independent book of the same size: "
          f"{out['ir_ceiling_ratio']}x")
    print(f"  ({out['ir_ceiling_note']})")
    print("\nMEASUREMENT ONLY — no verdict, nothing enabled. Widening the "
          "universe\nwould be a separate pre-registered decision. See §49.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
