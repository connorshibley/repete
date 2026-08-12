#!/usr/bin/env python3
"""ETF-UNIVERSE SURVIVORSHIP PROBE — is this universe honest by construction?

Companion to `probe_delisted_coverage.py`, asking the same question from the
other end. That probe asked "can the losers be bought back into a stock
universe?" and REFUSED: yfinance serves no pre-delisting history for SIVB, FRC,
TWTR or ATVI, and returns SBNY bars that all postdate the bank's seizure by
seventeen months. §52 froze the EDGE budget on that answer.

This probe asks a narrower question that may have a better answer: **is there a
universe whose current membership and its point-in-time membership are the same
set?** If so, survivorship cannot be present, because there is nothing that left.

THE CANDIDATE. The Select Sector SPDR suite is a partition of the S&P 500 chosen
by GICS sector — that is, chosen BY CONSTRUCTION rather than by performance. The
original nine listed together in December 1998; XLRE was carved out of XLF in
2015 and XLC arrived with the 2018 GICS realignment. Plus four broad-index funds
that predate all of them. Nothing in that list was picked because it did well.

WHAT THIS PROBE CAN AND CANNOT ESTABLISH — stated here rather than blurred,
because dressing an assumption as a check is the failure this repo keeps
catching:

  CAN     each declared fund returns history that begins at its declared
          inception, runs to the present, and contains no multi-month hole of
          the kind a delist-and-relist would leave.

  CANNOT  that the enumeration is COMPLETE. "No Select Sector SPDR was ever
          launched and closed" is an external, sourced fact. Prices cannot prove
          a negative, and this probe does not pretend to. `UNIVERSE` below is a
          DECLARED ASSUMPTION with its sources, and `report()` prints it as one.

THE NEGATIVE CONTROL IS WHAT GIVES THIS TEETH. A probe that only looks at
healthy funds and finds them healthy has demonstrated nothing about its own
ability to detect a dead one. So the same `assess()` predicate is applied to
symbols already MEASURED dead by the sibling probe — and every one of them must
come back NOT healthy. SBNY is the sharp case: it returns recent, continuous,
plausible-looking bars, and it is a seized bank. If this probe's notion of
"alive and continuous from inception" cannot tell SBNY from XLK, then it cannot
certify anything, and it REFUSES no matter how clean the fifteen look.

A LIVE CONTROL is included for the same reason the sibling probe has one: if
everything returns nothing, the honest reading is "the network is down", not
"the data is bad". Nothing is concluded unless the control came back.

VERDICT. Both checks pass -> this universe may be built into a snapshot under
`data/pit/` and EDGE claims registered against it. Either fails -> it may not,
`data/pit/` stays empty, and the §52 freeze stands undisturbed.

REUSABLE. Same contract as the sibling: point `--source` at a new fetcher and it
answers the same questions about a candidate vendor's ETF coverage.

    python scripts/probe_etf_universe_survivorship.py
    python scripts/probe_etf_universe_survivorship.py --json

Read-only. Fetches prices and mutates no repo state.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

START, END = "1993-01-01", "2026-07-10"

# DECLARED ASSUMPTION, NOT A MEASUREMENT. Sources: the Select Sector SPDR
# prospectus family (nine funds listed 1998-12-16, first trading 1998-12-22),
# the 2015 GICS Real Estate separation and the 2018 Communication Services
# realignment. The dates below are the inception dates those documents give.
#
# The honest caveat, recorded because it affects any run starting before ~2002:
# the original nine were mandated on the older S&P sector scheme and were
# REMAPPED onto GICS in the early 2000s — XLV was "Consumer Services" before it
# was "Health Care". The FUND is continuous; its MANDATE is not. That is a
# composition change, not survivorship, and it is a failure mode for the spec
# rather than a reason to refuse here.
UNIVERSE = [
    ("XLB",  "1998-12-22", "Materials Select Sector SPDR"),
    ("XLE",  "1998-12-22", "Energy Select Sector SPDR"),
    ("XLF",  "1998-12-22", "Financial Select Sector SPDR"),
    ("XLI",  "1998-12-22", "Industrial Select Sector SPDR"),
    ("XLK",  "1998-12-22", "Technology Select Sector SPDR"),
    ("XLP",  "1998-12-22", "Consumer Staples Select Sector SPDR"),
    ("XLU",  "1998-12-22", "Utilities Select Sector SPDR"),
    ("XLV",  "1998-12-22", "Health Care Select Sector SPDR"),
    ("XLY",  "1998-12-22", "Consumer Discretionary Select Sector SPDR"),
    ("XLRE", "2015-10-08", "Real Estate Select Sector SPDR — GICS split from XLF"),
    ("XLC",  "2018-06-19", "Communication Services Select Sector SPDR — 2018 GICS"),
    ("SPY",  "1993-01-29", "SPDR S&P 500 — required by the simulator for regime"),
    ("DIA",  "1998-01-20", "SPDR Dow Jones Industrial Average"),
    ("QQQ",  "1999-03-10", "Invesco QQQ — Nasdaq 100"),
    ("IWM",  "2000-05-26", "iShares Russell 2000"),
    # §64 additions (2026-08-12): the GEM/dual-momentum legs. Inception dates
    # from issuer fact sheets. Each is a single perpetual fund, not a screened
    # universe, so the question here is only continuous pricing over the
    # window it is asked to cover — the same question the fifteen above answer.
    ("EFA",  "2001-08-14", "iShares MSCI EAFE — developed ex-US equity"),
    ("IEF",  "2002-07-22", "iShares 7-10 Year Treasury Bond"),
    ("TLT",  "2002-07-22", "iShares 20+ Year Treasury Bond"),
    ("AGG",  "2003-09-22", "iShares Core U.S. Aggregate Bond"),
    ("VEU",  "2007-03-02", "Vanguard FTSE All-World ex-US"),
]

# NEGATIVE CONTROL. Symbols the sibling probe already MEASURED as dead, with the
# date each stopped being itself and the listing date it would have had to cover
# to look alive. Every one must come back NOT healthy. If any looks healthy,
# `assess()` cannot see death and this probe certifies nothing.
#
# SBNY is the one that matters: it returns recent, continuous, plausible bars.
# Any predicate that calls SBNY healthy would call a resurrected ticker healthy.
CLOSED = [
    ("SBNY", "2004-03-23", "2023-03-12", "Signature Bank — seized; yfinance "
                                         "returns bars starting 2024-08-15"),
    ("SIVB", "1988-01-01", "2023-03-10", "SVB Financial — seized by the FDIC"),
    ("FRC",  "2010-12-08", "2023-05-01", "First Republic — seized, sold to JPM"),
    ("TWTR", "2013-11-07", "2022-10-27", "Twitter — taken private"),
]

# Not part of the verdict. It exists so "no rows anywhere" cannot be confused
# with a broken network or a bad install. Deliberately NOT a member of UNIVERSE:
# a control that is also a subject cannot referee itself.
CONTROL = "AAPL"

# A first bar this long after the declared inception means the history is not
# the fund's own. Generous enough for holidays, a vendor's late start and the
# gap between listing and first settled bar; far tighter than the seventeen
# months SBNY is off by.
INCEPTION_TOLERANCE_DAYS = 21
# A last bar this long before END means the thing stopped trading.
RECENT_TOLERANCE_DAYS = 21
# A hole longer than this is a suspension or a relist. The longest legitimate
# US market closure on record is four trading days (September 2001).
MAX_GAP_DAYS = 45


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


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def largest_gap(dates: list) -> tuple:
    """(days, after_this_date) for the biggest hole in an ordered date list."""
    worst, at = 0, None
    for prev, nxt in zip(dates, dates[1:]):
        d = _days(prev, nxt)
        if d > worst:
            worst, at = d, prev
    return worst, at


def assess(ticker: str, inception: str, rows: list, end: str) -> dict:
    """ONE predicate, applied to the universe AND to the negative control.

    Using the same function on both is the whole design: the universe must all
    pass it and the known-dead must all fail it, so a predicate too weak to see
    death cannot quietly certify the universe.
    """
    rec = {"ticker": ticker, "inception": inception, "rows": len(rows),
           "first_bar": rows[0][0] if rows else None,
           "last_bar": rows[-1][0] if rows else None,
           "gap_days": 0, "gap_after": None, "healthy": False, "why": ""}
    if not rows:
        rec["why"] = "no history at all"
        return rec

    dates = [d for d, _ in rows]
    rec["gap_days"], rec["gap_after"] = largest_gap(dates)

    late = _days(inception, dates[0])
    if late > INCEPTION_TOLERANCE_DAYS:
        rec["why"] = (f"history starts {dates[0]}, {late} days after the "
                      f"declared inception {inception} — these bars are not "
                      f"this fund's own")
        return rec
    stale = _days(dates[-1], end)
    if stale > RECENT_TOLERANCE_DAYS:
        rec["why"] = (f"history stops {dates[-1]}, {stale} days before {end} — "
                      f"it stopped trading")
        return rec
    if rec["gap_days"] > MAX_GAP_DAYS:
        rec["why"] = (f"a {rec['gap_days']}-day hole after {rec['gap_after']} — "
                      f"longer than any legitimate market closure, so this is "
                      f"a suspension or a relist")
        return rec

    rec["healthy"] = True
    rec["why"] = "continuous from inception to the present"
    return rec


def probe(fetch, end: str = END) -> dict:
    out = {"control": {}, "universe": [], "closed": [],
           "check1_continuous_from_inception": None,
           "check2_detects_the_dead": None, "verdict": None}

    control = fetch(CONTROL)
    out["control"] = {"ticker": CONTROL, "rows": len(control)}
    if not control:
        out["verdict"] = "UNDETERMINED"
        out["reason"] = (f"the control {CONTROL} returned no rows — the fetch "
                         f"path itself is broken, so nothing here is a "
                         f"measurement of anything")
        return out

    for ticker, inception, what in UNIVERSE:
        rec = assess(ticker, inception, fetch(ticker), end)
        rec["what"] = what
        out["universe"].append(rec)

    for ticker, listed, died, what in CLOSED:
        rec = assess(ticker, listed, fetch(ticker), end)
        rec["what"] = what
        rec["died"] = died
        out["closed"].append(rec)

    out["check1_continuous_from_inception"] = all(
        t["healthy"] for t in out["universe"])
    # The falsifiability check. NOT "are they absent" — absent is one way to be
    # dead, and SBNY proves it is not the only way.
    out["check2_detects_the_dead"] = not any(
        t["healthy"] for t in out["closed"])
    out["verdict"] = ("PASS" if out["check1_continuous_from_inception"]
                      and out["check2_detects_the_dead"] else "REFUSED")
    return out


def _table(rows: list, label: str) -> None:
    print(f"\n{label}")
    print(f"{'ticker':7} {'inception':11} {'first bar':11} {'last bar':11} "
          f"{'rows':>6}  status")
    for t in rows:
        status = "ok" if t["healthy"] else f"NOT HEALTHY — {t['why']}"
        print(f"{t['ticker']:7} {t['inception']:11} "
              f"{str(t['first_bar'] or '-'):11} {str(t['last_bar'] or '-'):11} "
              f"{t['rows']:>6}  {status}")


def report(res: dict) -> int:
    print("ETF-UNIVERSE SURVIVORSHIP PROBE\n" + "=" * 78)
    c = res["control"]
    print(f"control {c['ticker']}: {c['rows']} rows")
    if res["verdict"] == "UNDETERMINED":
        print(f"\nUNDETERMINED — {res['reason']}")
        print("\nNo conclusion is drawn and the §52 freeze is untouched. An "
              "unmeasured\nquestion is not an answered one.")
        return 2

    _table(res["universe"], "THE CANDIDATE UNIVERSE")
    _table(res["closed"], "NEGATIVE CONTROL — every one of these MUST be "
                          "detected as not healthy")

    print()
    print(f"  check 1  continuous from inception : "
          f"{'PASS' if res['check1_continuous_from_inception'] else 'FAIL'}")
    print(f"  check 2  detects the known dead    : "
          f"{'PASS' if res['check2_detects_the_dead'] else 'FAIL'}")

    print("\nNOT CHECKED, AND IT MATTERS: that the fifteen above are a COMPLETE"
          "\nenumeration. 'No Select Sector SPDR was ever launched and closed' "
          "is an\nexternal sourced fact; prices cannot prove a negative. The "
          "list is a\nDECLARED ASSUMPTION — see UNIVERSE in this file for its "
          "sources and for the\nearly-2000s GICS remapping caveat.")

    if res["verdict"] == "PASS":
        print("\nPASS — current membership and point-in-time membership are the "
              "same set for\nthis universe, so a snapshot built from it carries "
              "no survivorship. It may be\nwritten to data/pit/ and EDGE claims "
              "registered against it.")
        return 0

    print("\nREFUSED — this universe may NOT be certified.")
    sick = [t["ticker"] for t in res["universe"] if not t["healthy"]]
    blind = [t["ticker"] for t in res["closed"] if t["healthy"]]
    if sick:
        print(f"  incomplete history : {', '.join(sick)} — the snapshot would "
              f"not carry\n                       what it claims to carry.")
    if blind:
        print(f"  CANNOT SEE DEATH   : {', '.join(blind)} — these are known "
              f"dead and this\n                       probe called them "
              f"healthy. The predicate is too weak to\n                       "
              f"certify anything, and the fifteen tell us nothing.")
    print("\ndata/pit/ stays empty and the §52 freeze stands. See §56 in "
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
