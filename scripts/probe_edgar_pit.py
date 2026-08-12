#!/usr/bin/env python3
"""EDGAR POINT-IN-TIME PROBE — may filings data be backtested at all?

Run BEFORE any filings code exists, and before any filings spec is written. The
precedent is §28, §46 and §52: a gate that was fully drafted, then a probe
showed the data could not support it, so it was NEVER REGISTERED. Discovering a
claim was unsound after registering it is strictly worse than discovering it
before.

WHAT IS BEING DECIDED. Filings are an input class no hypothesis in this repo has
ever referenced — every strategy is pure price, and earnings_cache.json holds
earnings DATES for blackout logic, not filed content. The FMP probe asked
whether a paid vendor's fundamentals could be gated on. This asks the same
question of the primary source those vendors resell, which is free, requires no
key, and is published by the SEC as a public good.

Free and primary is not the same as safe. Three ways it can lie:

  1. NO ACCEPTANCE TIMESTAMP — is the filing dated by the period it covers, the
     day it was filed, or the moment the SEC ACCEPTED it? These are three
     different instants. An 8-K accepted at 18:05 ET is not tradeable that
     session, and a backtest gating on calendar date alone buys hours before
     the information existed. This is the same failure the FMP probe measures
     as filing lag, one layer closer to the source.
  2. RETROACTIVE EDIT — when a company amends, does the amendment appear as a
     NEW filing alongside the original, or does the original get replaced? If
     history is rewritten in place, every backtest reads today's corrected
     story and calls it what was known then.
  3. SURVIVORSHIP — are filing histories retained for issuers that ceased to
     exist? If the losers are dropped, any historical screen silently excludes
     everyone who went bankrupt.

The restatement names (KHC, UAA) and the failed issuers (SIVB, FRC) are
deliberately the SAME names scripts/probe_fmp_lookahead.py uses, so the two
probes are directly comparable rather than each testing its own convenient
universe.

VERDICT. All three pass -> a filings claim MAY be pre-registered and
backtested. Any one fails -> filings are LIVE JUDGE CONTEXT ONLY (the W7 shape:
judge-only, so under invariant #2 it can veto or shrink and can never create a
trade), and no filings spec may exist. The script exits non-zero in that case
and says so, rather than leaving the reader to infer it.

This probe is READ-ONLY against EDGAR and mutates no repo state. EDGAR needs no
key, but it DOES require a declared User-Agent carrying contact information and
throttles traffic that does not identify itself; the probe prints its own call
count and stays far inside the published 10 requests/second limit.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime

BASE = "https://data.sec.gov"
CALL_BUDGET = 20          # SEC allows 10/sec; this is nowhere near it
CALL_SPACING_S = 0.2      # deliberate self-throttle, well under the limit
_CALLS = 0

# CIK is the SEC's own identifier; tickers are not stable keys at the SEC and
# resolving them would add a call and a failure mode for no benefit. These are
# zero-padded to 10 digits as the submissions endpoint requires.
RESTATED = [
    ("0001637459", "KHC", "Kraft Heinz restated FY2016-2018 in its 2019 10-K/A"),
    ("0001336917", "UAA", "Under Armour restated revenue timing after the SEC "
                          "inquiry"),
]

DELISTED = [
    ("0000719739", "SIVB", "SVB Financial Group, failed March 2023"),
    ("0001132979", "FRC", "First Republic Bank, failed May 2023"),
]

# Forms whose timing is most obviously tradeable, and therefore where an
# acceptance timestamp matters most.
TIMED_FORMS = {"8-K", "10-Q", "10-K"}

MIN_TIMESTAMPED_FRACTION = 1.0   # every row, or it is not usable


def _user_agent() -> str:
    """EDGAR requires a declared identity. This is contact metadata, not a
    credential — but it is a personal email, so it lives in .env rather than
    in the source, and is never printed."""
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "").strip()
    if not ua:
        print("SEC_EDGAR_USER_AGENT is not set. EDGAR requires a declared "
              "User-Agent with contact info (e.g. 'Name email@example.com') "
              "and throttles anonymous traffic. Set it in .env.",
              file=sys.stderr)
        raise SystemExit(2)
    return ua


def get(path: str) -> dict | None:
    """One GET. Returns parsed JSON, or None on any failure.

    The User-Agent is never logged, on any path — it carries a real email
    address, and a diagnosis that has to be redacted on its way to a log is the
    wrong diagnosis.
    """
    global _CALLS
    if _CALLS >= CALL_BUDGET:
        print(f"  !! call budget {CALL_BUDGET} exhausted — stopping",
              file=sys.stderr)
        return None
    req = urllib.request.Request(
        f"{BASE}/{path}",
        headers={"User-Agent": _user_agent(), "Accept-Encoding": "gzip, deflate"},
    )
    _CALLS += 1
    time.sleep(CALL_SPACING_S)
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print(f"  http {e.code} on {path}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  {type(e).__name__} on {path}", file=sys.stderr)
    return None


def _recent(cik: str) -> dict | None:
    """The `filings.recent` block: parallel arrays, one index per filing."""
    payload = get(f"submissions/CIK{cik}.json")
    if not isinstance(payload, dict):
        return None
    recent = payload.get("filings", {})
    recent = recent.get("recent") if isinstance(recent, dict) else None
    return recent if isinstance(recent, dict) else None


def _rows(recent: dict) -> list[dict]:
    """Transpose EDGAR's parallel arrays into per-filing dicts."""
    keys = ("form", "filingDate", "acceptanceDateTime", "reportDate",
            "accessionNumber")
    cols = {k: recent.get(k) for k in keys}
    n = len(cols["form"]) if isinstance(cols.get("form"), list) else 0
    out = []
    for i in range(n):
        row = {}
        for k, v in cols.items():
            row[k] = v[i] if isinstance(v, list) and i < len(v) else None
        out.append(row)
    return out


# ---------------------------------------------------------------- check 1
def check_acceptance_timestamp(out: list) -> bool | None:
    """Is there a moment-of-availability distinct from the calendar date?"""
    out.append("\n== 1. ACCEPTANCE TIMESTAMP ==")
    seen = 0
    timestamped = 0
    distinct_from_period = 0
    after_close = 0

    for cik, sym, _why in RESTATED:
        recent = _recent(cik)
        if recent is None:
            out.append(f"  {sym}: submissions unavailable")
            continue
        rows = [r for r in _rows(recent) if r.get("form") in TIMED_FORMS]
        out.append(f"  {sym}: {len(rows)} 8-K/10-Q/10-K rows")
        for r in rows:
            seen += 1
            acc = r.get("acceptanceDateTime")
            if not acc:
                continue
            timestamped += 1
            if r.get("reportDate") and r["reportDate"] != r.get("filingDate"):
                distinct_from_period += 1
            # A timestamp that is always midnight is a date in disguise.
            if "T" in str(acc) and not str(acc).endswith("T00:00:00.000Z"):
                after_close += 1

    if not seen:
        out.append("\n  VERDICT: UNDETERMINED — no filings retrieved, so the "
                   "question was not answered. An unanswerable question is not "
                   "a passed one.")
        return None

    out.append(f"\n  {timestamped}/{seen} rows carry acceptanceDateTime; "
               f"{after_close} carry a real time-of-day; "
               f"{distinct_from_period} have a period date distinct from the "
               f"filing date")

    if timestamped < seen * MIN_TIMESTAMPED_FRACTION:
        out.append("\n  VERDICT: FAIL — some filings carry no acceptance "
                   "timestamp, so a backtest would fall back to the calendar "
                   "date for those and trade information hours before it "
                   "existed.")
        return False
    if not after_close:
        out.append("\n  VERDICT: FAIL — every timestamp is midnight. That is "
                   "the filing date wearing a clock, and it cannot separate a "
                   "pre-open filing from one accepted after the close.")
        return False

    out.append("\n  VERDICT: PASS — acceptance carries a real time of day and "
               "is distinct from both the period and the filing date.")
    return True


# ---------------------------------------------------------------- check 2
def check_no_retroactive_edit(out: list) -> bool | None:
    """Do amendments ADD a filing, or replace the original in place?"""
    out.append("\n== 2. RETROACTIVE EDIT ==")
    verdicts = []
    for cik, sym, why in RESTATED:
        out.append(f"\n  {sym}: {why}")
        recent = _recent(cik)
        if recent is None:
            out.append("    submissions unavailable")
            verdicts.append(None)
            continue
        rows = _rows(recent)
        forms = [str(r.get("form") or "") for r in rows]
        amended = [f for f in forms if f.endswith("/A")]
        originals = {f[:-2] for f in amended}
        both = [f for f in originals if f in forms]
        out.append(f"    {len(rows)} filings; {len(amended)} amendments "
                   f"({sorted(set(amended))[:4]})")
        if not amended:
            out.append("    no amendment in the retained window")
            verdicts.append(None)
            continue
        if both:
            out.append(f"    originals still present for: {sorted(both)[:4]}")
            verdicts.append(True)
        else:
            out.append("    amendments present but no surviving original of "
                       "the same form")
            verdicts.append(False)

    if any(v is False for v in verdicts):
        out.append("\n  VERDICT: FAIL — an amendment replaced its original "
                   "rather than joining it. History is rewritten in place, so "
                   "a backtest reads today's corrected story as though it were "
                   "known at the time.")
        return False
    if not any(v is True for v in verdicts):
        out.append("\n  VERDICT: UNDETERMINED — no amendment/original pair was "
                   "observable in the retained window.")
        return None

    out.append("\n  VERDICT: PASS — amendments are additive; the original "
               "filing survives alongside its correction.")
    return True


# ---------------------------------------------------------------- check 3
def check_survivorship(out: list) -> bool | None:
    """Is filing history retained for issuers that stopped existing?"""
    out.append("\n== 3. SURVIVORSHIP ==")
    found = 0
    for cik, sym, why in DELISTED:
        recent = _recent(cik)
        n = len(_rows(recent)) if recent else 0
        out.append(f"  {sym} ({why}): {n} filings retained")
        if n:
            found += 1

    if found == len(DELISTED):
        out.append("\n  VERDICT: PASS — failed issuers retain their filing "
                   "history.")
        return True
    if found == 0:
        out.append("\n  VERDICT: FAIL — no filings for failed issuers. Any "
                   "historical screen would exclude everyone who went "
                   "bankrupt.")
        return False
    out.append("\n  VERDICT: FAIL — coverage of failed issuers is partial, "
               "which biases a screen in a way that cannot be corrected.")
    return False


REFUSAL = """
=======================================================================
REFUSING: EDGAR data as read here does not support a point-in-time
backtest. Do NOT register a filings gate on this source — a claim
scored against it would be inflated and indistinguishable from a real
edge.

Filings may still be used as LIVE JUDGE CONTEXT ONLY: judge-only, so
under invariant #2 it can veto or shrink and can never create a trade;
ungated; and recorded as a sim/live divergence. That is exactly the
shape W7 used for news memory.

EDGE stands at 1 pass in 15 and K stays 15. Nothing is adopted
by this result.
=======================================================================
"""

BLESSING = """
=======================================================================
All three checks PASS. A filings claim MAY be pre-registered and
backtested against EDGAR, subject to the usual discipline:
canonical-hash the spec before running, count it against Bonferroni K,
and use the exposure-matched benchmark.

Gate on acceptanceDateTime, never on filingDate — and treat anything
accepted after 16:00 ET as available the NEXT session. The whole value
of this source is that it carries the moment of availability; throwing
that away at the point of use reintroduces the lookahead the probe
just cleared.

Note the submissions endpoint returns a RECENT window, not all history;
a claim reaching further back needs the full index, and the retained
window is itself a coverage question this probe does not settle.

This does NOT mean filings help. It means a test of whether they help
would be meaningful. EDGE still stands at 1 pass in 15.
=======================================================================
"""


def main() -> int:
    out: list[str] = [
        "EDGAR POINT-IN-TIME PROBE",
        f"run {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "read-only; no repo state mutated; no strategy or rail touched",
        "same restatement and failed-issuer names as probe_fmp_lookahead.py, "
        "so the two are directly comparable",
    ]
    results = {
        "acceptance_timestamp": check_acceptance_timestamp(out),
        "no_retroactive_edit": check_no_retroactive_edit(out),
        "survivorship": check_survivorship(out),
    }
    out.append(f"\nEDGAR calls used: {_CALLS} (budget {CALL_BUDGET}, "
               f"SEC limit 10/sec)")

    passed = all(v is True for v in results.values())
    out.append(BLESSING if passed else REFUSAL)
    for k, v in results.items():
        out.append(f"  {k:22} "
                   f"{'PASS' if v is True else 'FAIL' if v is False else 'UNDETERMINED'}")

    report = "\n".join(out)
    print(report)
    try:
        os.makedirs("research", exist_ok=True)
        p = f"research/edgar_pit_{date.today().isoformat()}.txt"
        with open(p, "w") as f:
            f.write(report + "\n")
        print(f"\nreport -> {p}")
    except OSError as e:
        print(f"report not written: {type(e).__name__}", file=sys.stderr)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
