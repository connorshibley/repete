#!/usr/bin/env python3
"""DELISTED-COVERAGE PROBE — can a survivorship-free universe be built at all?

Run BEFORE any point-in-time universe code exists, and before any §52 is
written. The precedent is §28 and §46: a gate that was fully drafted, then a
probe showed the data could not support it, so it was NEVER REGISTERED.
Discovering a claim was unsound after registering it is strictly worse than
discovering it before.

WHAT IS BEING DECIDED. §51 produced an EDGE pass, and its own results
section showed the pass is explicable by survivorship: the 38-name universe
carries +200.28pp of inflation because those names are in config.yaml precisely
because they are today's winners. §51 named the fix — replication on a universe
not selected on the outcome — and this probe asks whether that fix is buildable
with the data this repo actually has.

`scripts/build_snapshot.py` sources its universe from `index_constituents()`,
which reads CURRENT Wikipedia membership. Every snapshot in data/snapshots/ is
therefore survivor-selected. Fixing that needs price history for companies that
DIED, which is the thing free vendors do not sell.

TWO WAYS THIS CAN FAIL. Both fatal, both checked here, and the second is the
one that matters:

  1. COVERAGE — does the source return usable history for a delisted name at
     all? If not, a point-in-time universe cannot be assembled: the losers are
     simply missing.

  2. TICKER REUSE — for any delisted name that DOES return bars, do those bars
     cover the period BEFORE the delisting? A ticker reassigned to a different
     company passes check 1 and fails check 2, and it is far more dangerous
     than a missing one. SBNY is the worked example: Signature Bank was seized
     2023-03-12, and yfinance returns 475 bars starting 2024-08-15 — still
     labelled "Signature Bank", on the pink sheets. A builder that only asked
     "did we get bars?" would splice in a successor entity and produce a
     dataset that NEVER OBSERVES THE COLLAPSE while carrying the name of the
     company that collapsed. That is not survivorship bias; it is fabricated
     history wearing the costume of the fix.

A CONTROL IS INCLUDED ON PURPOSE. If every ticker returns nothing the honest
reading might be "the network is down", not "the vendor lacks delisted data".
The probe fetches a living name too and refuses to draw any conclusion unless
the control came back — an unmeasured question is not an answered one, which is
the §33 RUN 1 rule applied to a probe.

VERDICT. Both checks pass -> a §52 replication may be pre-registered on a
point-in-time universe. Either fails -> it may not, and the EDGE freeze in
`register_gate.py` stands. Exits non-zero in that case and says so, rather than
leaving the reader to infer it.

REUSABLE. This is the acceptance test for any candidate vendor — Norgate,
Sharadar, Polygon, CRSP. Point `--source` at a new fetcher and it answers yes or
no on the same two questions.

    python scripts/probe_delisted_coverage.py
    python scripts/probe_delisted_coverage.py --json

Read-only. Fetches prices and mutates no repo state.
"""
from __future__ import annotations

import argparse
import json
import sys

START, END = "2020-01-01", "2026-07-10"

# Each entry carries the date the company STOPPED BEING ITSELF, which is what
# makes check 2 checkable rather than eyeballed. A universe spanning START..END
# needs every one of these to be present with pre-delisting history.
DELISTED = [
    ("SIVB", "2023-03-10", "SVB Financial — seized by the FDIC"),
    ("SBNY", "2023-03-12", "Signature Bank — seized by regulators"),
    ("FRC",  "2023-05-01", "First Republic — seized, sold to JPMorgan"),
    ("TWTR", "2022-10-27", "Twitter — taken private"),
    ("ATVI", "2023-10-13", "Activision Blizzard — acquired by Microsoft"),
]

# Not part of the verdict. It exists so "no rows anywhere" cannot be confused
# with a broken network or a bad install.
CONTROL = "AAPL"


def fetch_yfinance(ticker: str) -> list:
    """[(iso_date, close)] or [] — the source every snapshot is built from."""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf
    df = yf.download(ticker, start=START, end=END, progress=False,
                     auto_adjust=True, threads=False)
    if df is None or df.empty:
        return []
    closes = df["Close"]
    if hasattr(closes, "columns"):          # yfinance may return a MultiIndex
        closes = closes.iloc[:, 0]
    return [(str(ts.date()), float(v)) for ts, v in closes.items()]


SOURCES = {"yfinance": fetch_yfinance}


def probe(fetch) -> dict:
    out = {"control": {}, "tickers": [], "check1_coverage": None,
           "check2_no_ticker_reuse": None, "verdict": None}

    control = fetch(CONTROL)
    out["control"] = {"ticker": CONTROL, "rows": len(control)}
    if not control:
        out["verdict"] = "UNDETERMINED"
        out["reason"] = (f"the control {CONTROL} returned no rows — the fetch "
                         f"path itself is broken, so nothing here is a "
                         f"measurement of vendor coverage")
        return out

    for ticker, delisted_on, what in DELISTED:
        rows = fetch(ticker)
        pre = [d for d, _ in rows if d < delisted_on]
        rec = {"ticker": ticker, "delisted_on": delisted_on, "what": what,
               "rows": len(rows), "rows_before_delisting": len(pre),
               "first_bar": rows[0][0] if rows else None,
               "last_bar": rows[-1][0] if rows else None}
        # Covered = has history, and that history is actually the company's own.
        # A ticker whose bars all POSTDATE its delisting is a different
        # company wearing the same symbol.
        rec["covered"] = len(pre) > 0
        rec["reused"] = len(rows) > 0 and len(pre) == 0
        out["tickers"].append(rec)

    out["check1_coverage"] = all(t["covered"] for t in out["tickers"])
    out["check2_no_ticker_reuse"] = not any(t["reused"] for t in out["tickers"])
    out["verdict"] = ("PASS" if out["check1_coverage"]
                      and out["check2_no_ticker_reuse"] else "REFUSED")
    return out


def report(res: dict) -> int:
    print("DELISTED-COVERAGE PROBE\n" + "=" * 70)
    c = res["control"]
    print(f"control {c['ticker']}: {c['rows']} rows")
    if res["verdict"] == "UNDETERMINED":
        print(f"\nUNDETERMINED — {res['reason']}")
        print("\nNo conclusion is drawn and the EDGE freeze is untouched. An "
              "unmeasured\nquestion is not an answered one.")
        return 2

    print()
    print(f"{'ticker':7} {'delisted':11} {'rows':>6} {'pre-delisting':>14}  status")
    for t in res["tickers"]:
        if t["covered"]:
            status = "ok"
        elif t["reused"]:
            status = (f"*** TICKER REUSE *** all {t['rows']} bars start "
                      f"{t['first_bar']}, after it died")
        else:
            status = "MISSING — no history at all"
        print(f"{t['ticker']:7} {t['delisted_on']:11} {t['rows']:>6} "
              f"{t['rows_before_delisting']:>14}  {status}")

    print()
    print(f"  check 1  coverage        : "
          f"{'PASS' if res['check1_coverage'] else 'FAIL'}")
    print(f"  check 2  no ticker reuse : "
          f"{'PASS' if res['check2_no_ticker_reuse'] else 'FAIL'}")

    if res["verdict"] == "PASS":
        print("\nPASS — a point-in-time universe can be assembled from this "
              "source, so a\n§52 replication may be pre-registered on it.")
        return 0

    print("\nREFUSED — a survivorship-free universe CANNOT be built from this "
          "source.")
    missing = [t["ticker"] for t in res["tickers"] if not t["covered"]
               and not t["reused"]]
    reused = [t["ticker"] for t in res["tickers"] if t["reused"]]
    if missing:
        print(f"  no history at all : {', '.join(missing)} — the losers are "
              f"simply absent,\n                      which is the "
              f"survivorship bias itself.")
    if reused:
        print(f"  ticker reuse      : {', '.join(reused)} — returns bars that "
              f"are NOT that\n                      company. Including these "
              f"would be WORSE than the bias:\n                      the "
              f"dataset would never observe the collapse while\n"
              f"                      carrying the name of what collapsed.")
    print("\nThe EDGE freeze in scripts/register_gate.py stands. See §52 in "
          "knowledge/backtest_candidates.md.")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="yfinance", choices=sorted(SOURCES))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    res = probe(SOURCES[args.source])
    res["source"] = args.source
    if args.json:
        print(json.dumps(res, indent=2))
        return 0 if res["verdict"] == "PASS" else (
            2 if res["verdict"] == "UNDETERMINED" else 1)
    return report(res)


if __name__ == "__main__":
    sys.exit(main())
