#!/usr/bin/env python3
"""FMP LOOKAHEAD PROBE — may fundamentals data be backtested at all?

Run BEFORE any fundamentals code exists, and before any §46 is written. The
precedent is §28: that gate was fully drafted, then a probe showed the mechanism
could not move the metric its clauses scored, so it was NEVER REGISTERED.
Discovering a claim was unsound after registering it is strictly worse than
discovering it before.

WHAT IS BEING DECIDED. Fundamentals (valuation, margins, analyst targets, DCF,
insider trades) are an input class no hypothesis in this repo has ever been able
to reference — `grep` for price_target/free_cash_flow/analyst/fundamental across
src/ returns nothing, and every strategy is pure price. That is a real gap. But
data that carries lookahead cannot be gated on: a claim scored against it would
be inflated and indistinguishable from a genuine edge, which is the worst
possible outcome for a project whose whole record is EDGE 0-for-12.

THREE WAYS THIS DATA COULD LIE. All fatal, all measurable, all checked here:

  1. RESTATEMENT — are figures as-reported-at-the-time, or as-restated-today?
     Restated numbers for 2008 already embed what happened next.
  2. FILING LAG — is the timestamp the period END or the date it became public?
     Q2 figures dated 30 June were not public until the 10-Q ~45 days later.
     This is the quietest lookahead in fundamentals backtesting.
  3. SURVIVORSHIP — does history include companies that later delisted? If the
     universe holds only survivors, any historical screen silently excludes
     everyone who went bankrupt.

VERDICT. All three pass -> a §46 may be pre-registered and backtested. Any one
fails -> fundamentals are LIVE JUDGE CONTEXT ONLY (the W7 shape: judge-only,
ungated, cannot create a trade), and NO §46 may exist. The script exits non-zero
in that case and says so, rather than leaving the reader to infer it.

This probe is READ-ONLY against FMP and mutates no repo state. It prints its own
call count and is budgeted for the 250/day free tier.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

BASE = "https://financialmodelingprep.com/stable"
CALL_BUDGET = 40          # free tier is 250/day; this stays far inside it
_CALLS = 0

# Companies with large, well-documented restatements. If FMP is point-in-time,
# its figure for the affected quarter matches what was ORIGINALLY filed; if it
# serves today's books, it matches the corrected number instead.
RESTATED = [
    ("KHC", "Kraft Heinz restated FY2016-2018 in its 2019 10-K/A"),
    ("UAA", "Under Armour restated revenue timing after the SEC inquiry"),
]

# Delisted / failed names. Present history = usable; empty = survivor-only.
DELISTED = [
    ("SIVB", "Silicon Valley Bank, failed March 2023"),
    ("FRC", "First Republic Bank, failed May 2023"),
]

LAG_SYMBOLS = ["AAPL", "MSFT", "KO"]


def _key() -> str:
    k = os.environ.get("FMP_API_KEY", "").strip()
    if not k:
        print("FMP_API_KEY is not set. Install it with ./scripts/set_fmp_key.sh "
              "(do not paste it into a chat).", file=sys.stderr)
        raise SystemExit(2)
    return k


class _PlanGated:
    """FMP refused the request for SUBSCRIPTION reasons, not data reasons.

    Distinct from None (transport failure) and from [] (a genuine empty
    answer), and the distinction is the whole point. Measured 2026-08-02 on
    the free tier: `historical-price-eod/full` served AAPL and MSFT 251 rows
    each for 2022 and answered SIVB and FRC with **http 402**. Collapsing
    that to "0 historical bars" is how check 3 came to report "no history for
    failed companies" — a decisive claim about the DATA resting on a question
    the plan never let it ask. The conservative verdict was right; the stated
    reason was false, and a false reason survives into the next decision.
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "<plan-gated>"


PLAN_GATED = _PlanGated()


def get(path: str, **params) -> list | dict | None | _PlanGated:
    """One GET. Returns parsed JSON, PLAN_GATED on 402, or None on any other
    failure.

    The key is never logged, on any path — a diagnosis that has to be redacted
    on its way to a log is the wrong diagnosis.
    """
    global _CALLS
    if _CALLS >= CALL_BUDGET:
        print(f"  !! call budget {CALL_BUDGET} exhausted — stopping", file=sys.stderr)
        return None
    params["apikey"] = _key()
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    _CALLS += 1
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print(f"  http {e.code} on {path}", file=sys.stderr)
        if e.code == 402:
            return PLAN_GATED
    except Exception as e:  # noqa: BLE001
        print(f"  {type(e).__name__} on {path}", file=sys.stderr)
    return None


# ---------------------------------------------------------------- check 1
def check_restatement(out: list) -> bool | None:
    """Does FMP expose an as-reported series, and does it DIFFER from the
    standard one? A difference is the leak, measured.

    Returns True (point-in-time available), False (restated only), or None
    (could not determine — treated as a failure by the caller, because an
    unanswerable question is not a passed one).
    """
    out.append("\n== 1. RESTATEMENT ==")
    verdicts = []
    for sym, why in RESTATED:
        out.append(f"\n  {sym}: {why}")
        std = get("income-statement", symbol=sym, period="quarter", limit=40)
        asrep = get("income-statement-as-reported", symbol=sym, period="quarter",
                    limit=40)
        if not isinstance(std, list) or not std:
            out.append("    standard series unavailable (plan-gated or empty)")
            verdicts.append(None)
            continue
        if not isinstance(asrep, list) or not asrep:
            out.append("    NO as-reported endpoint on this plan — cannot tell "
                       "an original figure from a corrected one")
            verdicts.append(None)
            continue

        by_date_std = {r.get("date"): r for r in std if isinstance(r, dict)}
        by_date_rep = {r.get("date"): r for r in asrep if isinstance(r, dict)}
        shared = sorted(set(by_date_std) & set(by_date_rep), reverse=True)
        if not shared:
            out.append("    no overlapping periods to compare")
            verdicts.append(None)
            continue

        diffs = 0
        for d in shared[:12]:
            a = by_date_std[d].get("revenue")
            b = (by_date_rep[d].get("revenues")
                 or by_date_rep[d].get("revenuefromcontractwithcustomer"
                                       "excludingassessedtax")
                 or by_date_rep[d].get("revenue"))
            if a is None or b is None:
                continue
            try:
                if abs(float(a) - float(b)) / max(abs(float(a)), 1.0) > 0.01:
                    diffs += 1
                    out.append(f"    {d}: standard {float(a):,.0f} vs "
                               f"as-reported {float(b):,.0f}")
            except (TypeError, ValueError):
                continue
        out.append(f"    {len(shared[:12])} periods compared, {diffs} differ >1%")
        verdicts.append(diffs == 0)

    if any(v is None for v in verdicts):
        out.append("\n  VERDICT: UNDETERMINED — treat as FAIL. An unanswerable "
                   "question is not a passed one.")
        return None
    if all(verdicts):
        out.append("\n  VERDICT: PASS — standard and as-reported agree, so the "
                   "standard series is not visibly restated.")
        return True
    out.append("\n  VERDICT: FAIL — the two series disagree. The standard "
               "series carries corrections made AFTER the fact.")
    return False


# ---------------------------------------------------------------- check 2
def check_filing_lag(out: list) -> bool | None:
    """Is a filing/acceptance date present, and does it lag the period end?

    Without it a backtest keys off period end and trades on figures nobody had
    for another six weeks. This is the most common fundamentals lookahead and
    the least visible.
    """
    out.append("\n== 2. FILING LAG ==")
    lags, missing = [], 0
    gated, read_any = 0, False
    for sym in LAG_SYMBOLS:
        rows = get("income-statement", symbol=sym, period="quarter", limit=8)
        if rows is PLAN_GATED:
            gated += 1
            out.append(f"  {sym}: plan-gated (http 402) — not measured")
            continue
        if not isinstance(rows, list) or not rows:
            out.append(f"  {sym}: unavailable")
            continue
        read_any = True
        for r in rows:
            if not isinstance(r, dict):
                continue
            end = r.get("date")
            filed = r.get("fillingDate") or r.get("filingDate") or r.get(
                "acceptedDate")
            if not end or not filed:
                missing += 1
                continue
            try:
                d0 = datetime.fromisoformat(str(end)[:10]).date()
                d1 = datetime.fromisoformat(str(filed)[:10]).date()
            except ValueError:
                missing += 1
                continue
            lags.append((d1 - d0).days)
        out.append(f"  {sym}: {len(rows)} periods read")

    if not lags:
        if gated and not read_any:
            out.append("\n  VERDICT: UNDETERMINED — treat as FAIL. Every symbol "
                       "was refused for plan reasons, so NO row was read and "
                       "nothing was learned about whether filing dates exist. "
                       "Saying 'no filing date on any row' here would describe "
                       "the subscription, not the data.")
            return None
        out.append("\n  VERDICT: FAIL — no filing date on any row. The lag "
                   "cannot be corrected for on data that does not carry it.")
        return False
    lags.sort()
    med = lags[len(lags) // 2]
    out.append(f"  {len(lags)} periods with both dates; lag min {lags[0]}d, "
               f"median {med}d, max {lags[-1]}d; {missing} rows missing a date")
    if missing:
        out.append("\n  VERDICT: FAIL — some rows carry no filing date, so a "
                   "backtest would silently use period end for those.")
        return False
    if med < 5:
        out.append("\n  VERDICT: FAIL — median lag under 5 days is not a real "
                   "filing date; it is the period end wearing another name.")
        return False
    out.append("\n  VERDICT: PASS — a genuine filing date is present on every "
               "row and can be used as the point of availability.")
    return True


# ---------------------------------------------------------------- check 3
def check_survivorship(out: list) -> bool | None:
    """Is history retained for companies that stopped existing?"""
    out.append("\n== 3. SURVIVORSHIP ==")
    found, gated = 0, 0
    for sym, why in DELISTED:
        rows = get("historical-price-eod/full", symbol=sym)
        if rows is PLAN_GATED:
            gated += 1
            out.append(f"  {sym} ({why}): plan-gated (http 402) — not measured")
            continue
        n = len(rows) if isinstance(rows, list) else 0
        out.append(f"  {sym} ({why}): {n} historical bars")
        if n:
            found += 1
    if gated:
        out.append("\n  VERDICT: UNDETERMINED — treat as FAIL. This plan will "
                   "not serve delisted symbols at all, so their absence "
                   "measures the SUBSCRIPTION, not the data. A plan that "
                   "serves them may or may not retain their history; this run "
                   "does not know, and must not claim to.")
        return None
    if found == len(DELISTED):
        out.append("\n  VERDICT: PASS — delisted names retain history.")
        return True
    if found == 0:
        out.append("\n  VERDICT: FAIL — no history for failed companies. Any "
                   "historical screen would exclude everyone who went bankrupt.")
        return False
    out.append("\n  VERDICT: FAIL — coverage of delisted names is partial, "
               "which biases a screen in a way that cannot be corrected.")
    return False


# Two refusals, because they rest on different evidence and imply different
# next steps. MEASURED says the data was read and is bad — upgrading the plan
# changes nothing. UNMEASURED says the plan refused the questions — the source
# is unproven, which forbids a gate just the same, but a paid plan could still
# settle it. Printing the MEASURED wording after an all-402 run is how a
# subscription limit gets recorded as a fact about the vendor's data.
REFUSAL_HEAD_MEASURED = """
=======================================================================
REFUSING: FMP data carries lookahead. Do NOT register a fundamentals
gate on this source — a claim scored against it would be inflated and
indistinguishable from a real edge."""

REFUSAL_HEAD_UNMEASURED = """
=======================================================================
REFUSING: this run could not show FMP to be free of lookahead. No check
came back clean and at least one was refused for plan reasons, so the
source is UNPROVEN, not convicted. Do NOT register a fundamentals gate
on it — an unverified source and a bad one are equally unusable for
scoring — but note that a plan which serves the gated endpoints could
still settle the question either way."""

REFUSAL_TAIL = """

Fundamentals may still be used as LIVE JUDGE CONTEXT ONLY: judge-only,
so under invariant #2 it can veto or shrink and can never create a
trade; ungated; and recorded as a sim/live divergence. That is exactly
the shape W7 used for news memory — see docs/divergences.md #14.

Nothing is adopted by this result. The EDGE tally is unchanged; the
count of record lives in knowledge/backtest_candidates.md, not here.
=======================================================================
"""


def refusal(results: dict) -> str:
    """MEASURED only when a check actually came back False."""
    head = (REFUSAL_HEAD_MEASURED if any(v is False for v in results.values())
            else REFUSAL_HEAD_UNMEASURED)
    return head + REFUSAL_TAIL

BLESSING = """
=======================================================================
All three checks PASS. A fundamentals claim MAY be pre-registered and
backtested against this source, subject to the usual discipline:
canonical-hash the spec before running, count it against Bonferroni K,
and use the exposure-matched benchmark.

This does NOT mean fundamentals help. It means a test of whether they
help would be meaningful, not that they do. The EDGE tally is unchanged
and is kept in knowledge/backtest_candidates.md, not here.
=======================================================================
"""


def main() -> int:
    out: list[str] = [
        "FMP LOOKAHEAD PROBE",
        f"run {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "read-only; no repo state mutated; no strategy or rail touched",
    ]
    results = {
        "restatement": check_restatement(out),
        "filing_lag": check_filing_lag(out),
        "survivorship": check_survivorship(out),
    }
    out.append(f"\nFMP calls used: {_CALLS} (budget {CALL_BUDGET}, "
               f"free tier 250/day)")

    passed = all(v is True for v in results.values())
    out.append(BLESSING if passed else refusal(results))
    for k, v in results.items():
        out.append(f"  {k:14} {'PASS' if v is True else 'FAIL' if v is False else 'UNDETERMINED'}")

    report = "\n".join(out)
    print(report)
    try:
        os.makedirs("research", exist_ok=True)
        p = f"research/fmp_lookahead_{date.today().isoformat()}.txt"
        with open(p, "w") as f:
            f.write(report + "\n")
        print(f"\nreport -> {p}")
    except OSError as e:
        print(f"report not written: {type(e).__name__}", file=sys.stderr)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
