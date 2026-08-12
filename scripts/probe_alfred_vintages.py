#!/usr/bin/env python3
"""ALFRED VINTAGE PROBE — may macro data be backtested at all?

Run BEFORE any macro code exists, and before any macro spec is written. The
precedent is §28, §46 and §52: a gate that was fully drafted, then a probe
showed the data could not support it, so it was NEVER REGISTERED. Discovering
a claim was unsound after registering it is strictly worse than discovering it
before.

WHAT IS BEING DECIDED. This is not a new idea — it is a deferral this repo
already made. §31 chose cross-asset prices over FRED and said why, in
knowledge/backtest_candidates.md: "FRED serves CURRENT values and most macro
series are revised. Backtesting against revised data is lookahead, and it would
quietly invalidate this gate rather than fail it loudly. ALFRED vintages are
the correct fix and are a separate project."

This is that separate project, and it asks the only question that matters
first: does ALFRED actually deliver point-in-time values, or does it merely
claim to? Macro is an input class no hypothesis here has ever referenced. That
is a real gap. But data carrying lookahead cannot be gated on — a claim scored
against it would be inflated and indistinguishable from a genuine edge, which
is the worst possible outcome for a project whose EDGE record stands at ONE
pass in 15 — and whose single pass (§51) its own write-up showed to be
explicable by survivorship.

THREE WAYS THIS DATA COULD LIE. All fatal, all measurable, all checked here:

  1. NO VINTAGES — does the series expose more than one vintage at all? A
     single vintage is FRED-current wearing ALFRED's name, and every backtest
     built on it silently uses figures nobody had at the time.
  2. NO REVISION DIVERGENCE — for a series known to be revised, do the vintage
     values actually DIFFER from each other? If they are all identical, the
     realtime metadata is decoration over a current-values feed. This check
     carries a NEGATIVE CONTROL: a market rate that is never revised must show
     exactly one value, proving the check can tell the two cases apart rather
     than firing on everything.
  3. RELEASE LAG — is the first vintage of an observation dated when the figure
     became PUBLIC, or when the period it describes ended? Payrolls for January
     are not public until early February. This is the quietest lookahead in
     macro backtesting, and it is the same failure the FMP probe measures for
     filing dates.

VERDICT. All three pass -> a macro claim MAY be pre-registered and backtested.
Any one fails -> macro is LIVE JUDGE CONTEXT ONLY (the W7 shape: judge-only, so
under invariant #2 it can veto or shrink and can never create a trade), and no
macro spec may exist. The script exits non-zero in that case and says so,
rather than leaving the reader to infer it.

This probe is READ-ONLY against ALFRED and mutates no repo state. It prints its
own call count and stays far inside FRED's rate limit.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

BASE = "https://api.stlouisfed.org/fred"
CALL_BUDGET = 30          # FRED allows 120 req/min; this stays far inside it
_CALLS = 0

# ALFRED's full realtime window. Asking for it is what distinguishes "every
# vintage ever published" from "the current value" — the default for both
# realtime params is TODAY, which is exactly the trap §31 described.
RT_ALL = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}

# Heavily revised. Real GDP is revised at least three times per quarter
# (advance -> second -> third) and again at annual and comprehensive revisions.
REVISED_SERIES = ("GDPC1", "Real GDP — advance/second/third + annual revisions")

# NEVER revised: a market-observed constant-maturity yield. Once the day is
# done the number is the number. This is the negative control — if THIS shows
# multiple distinct values, the divergence check itself is unsound and the
# result is UNDETERMINED rather than a pass.
CONTROL_SERIES = ("DGS10", "10-year Treasury constant maturity — a market rate")

# Monthly, with a well-known publication schedule: the employment situation
# lands on the first Friday after the reference month ends, so the lag between
# the observation date and its first vintage should be roughly a month.
LAG_SERIES = "PAYEMS"
LAG_DATES = ["2023-01-01", "2023-02-01", "2023-03-01", "2023-04-01",
             "2023-05-01"]

# An observation date inside REVISED_SERIES to inspect across every vintage.
# Q2 2020 is deliberate: the largest revisions in the modern series.
REVISION_PROBE_DATE = "2020-04-01"

# Observation date for the negative-control series (CONTROL_SERIES). Must be
# a real trading day — DGS10 has no quote on a Sunday, and 2023-01-01 (the
# date reused from LAG_DATES in an earlier version) is one. A market with no
# quote returns zero rows, which proves nothing either way.
CONTROL_OBS_DATE = "2023-01-03"

MIN_LAG_DAYS = 5


def _key() -> str:
    k = os.environ.get("FRED_API_KEY", "").strip()
    if not k:
        print("FRED_API_KEY is not set. Install it with "
              "./scripts/set_fred_key.sh (do not paste it into a chat).",
              file=sys.stderr)
        raise SystemExit(2)
    return k


def get(path: str, **params) -> dict | None:
    """One GET. Returns parsed JSON, or None on any failure.

    The key is never logged, on any path — a diagnosis that has to be redacted
    on its way to a log is the wrong diagnosis.
    """
    global _CALLS
    if _CALLS >= CALL_BUDGET:
        print(f"  !! call budget {CALL_BUDGET} exhausted — stopping",
              file=sys.stderr)
        return None
    params["api_key"] = _key()
    params["file_type"] = "json"
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    _CALLS += 1
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        print(f"  http {e.code} on {path}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  {type(e).__name__} on {path}", file=sys.stderr)
    return None


def _observations(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    obs = payload.get("observations")
    return obs if isinstance(obs, list) else []


def _values_at(series: str, obs_date: str, rt: dict = RT_ALL) -> list[str] | None:
    """Every vintage value in effect for ONE observation date.

    Returns the raw value strings in vintage order, or None if the call failed.

    `rt` bounds the realtime window queried. RT_ALL is correct for a
    quarterly series like GDPC1 (few hundred vintages total) but is wrong
    for a DAILY market series like the negative control: FRED counts
    vintage dates over the WHOLE SERIES HISTORY within the requested
    realtime period, not just around obs_date, and a decades-long daily
    series blows past FRED's 2000-vintage-date response cap (~5000 for
    DGS10) even though only one vintage near obs_date is ever needed.
    """
    payload = get("series/observations", series_id=series,
                  observation_start=obs_date, observation_end=obs_date,
                  **rt)
    if payload is None:
        return None
    out = []
    for row in _observations(payload):
        if not isinstance(row, dict):
            continue
        if "realtime_start" not in row or "value" not in row:
            # A row without realtime metadata cannot be placed in time.
            return None
        out.append(str(row["value"]))
    return out


# ---------------------------------------------------------------- check 1
def check_vintages_exist(out: list) -> bool | None:
    """Does ALFRED expose more than one vintage for a revised series?"""
    out.append("\n== 1. VINTAGE AVAILABILITY ==")
    sym, why = REVISED_SERIES
    out.append(f"\n  {sym}: {why}")

    payload = get("series/vintagedates", series_id=sym, **RT_ALL)
    if payload is None:
        out.append("    vintagedates unavailable")
        out.append("\n  VERDICT: UNDETERMINED — the endpoint did not answer. "
                   "An unanswerable question is not a passed one.")
        return None

    dates = payload.get("vintage_dates")
    if not isinstance(dates, list):
        out.append("\n  VERDICT: FAIL — no vintage_dates field. There is no "
                   "vintage axis to index by, so nothing can be read "
                   "as-of a past date.")
        return False

    out.append(f"    {len(dates)} vintage dates published")
    if len(dates) < 2:
        out.append("\n  VERDICT: FAIL — fewer than two vintages. This is the "
                   "current value wearing ALFRED's name, which is exactly what "
                   "§31 refused to backtest against.")
        return False

    out.append(f"    earliest {dates[0]}, latest {dates[-1]}")
    out.append("\n  VERDICT: PASS — a real vintage axis exists.")
    return True


# ---------------------------------------------------------------- check 2
def check_revision_divergence(out: list) -> bool | None:
    """Do vintage values actually DIFFER for a revised series — and stay
    identical for one that is never revised?

    The negative control is the point. A check that fires on everything proves
    nothing; this one has to distinguish revised from unrevised to count.
    """
    out.append("\n== 2. REVISION DIVERGENCE ==")

    sym, why = REVISED_SERIES
    out.append(f"\n  {sym} @ {REVISION_PROBE_DATE}: {why}")
    revised = _values_at(sym, REVISION_PROBE_DATE)
    if revised is None:
        out.append("    unavailable, or a row carried no realtime metadata")
        out.append("\n  VERDICT: UNDETERMINED — cannot tell revised from "
                   "current.")
        return None
    distinct = sorted(set(revised))
    out.append(f"    {len(revised)} vintage rows, {len(distinct)} distinct "
               f"values")
    if len(distinct) < 2:
        out.append("\n  VERDICT: FAIL — a series revised at least three times "
                   "per quarter shows ONE value across all vintages. The "
                   "realtime metadata is decoration over a current-values "
                   "feed.")
        return False

    ctl, ctl_why = CONTROL_SERIES
    out.append(f"\n  NEGATIVE CONTROL — {ctl}: {ctl_why}")
    # RT_ALL is unusable here: DGS10 is a daily series with ~5000 vintage
    # dates across its full history, and FRED's JSON endpoint caps a
    # response at 2000 — the 1776-9999 window that works for quarterly
    # GDPC1 gets a flat 400 on a daily series. A one-year window from the
    # observation date is more than enough to catch a revision to a rate
    # that (if the control holds) is never revised at all.
    ctl_obs = date.fromisoformat(CONTROL_OBS_DATE)
    ctl_rt = {"realtime_start": ctl_obs.isoformat(),
              "realtime_end": (ctl_obs + timedelta(days=365)).isoformat()}
    control = _values_at(ctl, CONTROL_OBS_DATE, rt=ctl_rt)
    if control is None:
        out.append("    unavailable")
        out.append("\n  VERDICT: UNDETERMINED — without the control, a "
                   "divergence on the revised series could be noise.")
        return None
    if not control:
        out.append("    0 rows — no observation on the control date itself")
        out.append("\n  VERDICT: UNDETERMINED — zero rows proves nothing "
                   "about revision behavior; this is a missing observation, "
                   "not a confirmed single vintage.")
        return None
    ctl_distinct = sorted(set(control))
    out.append(f"    {len(control)} vintage rows, {len(ctl_distinct)} distinct "
               f"values")
    if len(ctl_distinct) > 1:
        out.append("\n  VERDICT: UNDETERMINED — a never-revised market rate "
                   "reports multiple values. The check cannot separate "
                   "revision from noise, so its PASS on the revised series "
                   "means nothing.")
        return None

    out.append("\n  VERDICT: PASS — revisions are visible on a revised series "
               "and absent on one that is never revised. The vintage axis "
               "carries real information.")
    return True


# ---------------------------------------------------------------- check 3
def check_release_lag(out: list) -> bool | None:
    """Is the first vintage dated when the figure became PUBLIC?"""
    out.append("\n== 3. RELEASE LAG ==")
    out.append(f"\n  {LAG_SERIES}: monthly payrolls, published the first "
               f"Friday after the reference month")

    lags: list[int] = []
    missing = 0
    for d in LAG_DATES:
        payload = get("series/observations", series_id=LAG_SERIES,
                      observation_start=d, observation_end=d, **RT_ALL)
        rows = _observations(payload)
        if not rows:
            missing += 1
            continue
        starts = [r.get("realtime_start") for r in rows
                  if isinstance(r, dict) and r.get("realtime_start")]
        if not starts:
            missing += 1
            continue
        try:
            first = min(date.fromisoformat(s) for s in starts)
            lag = (first - date.fromisoformat(d)).days
        except (TypeError, ValueError):
            missing += 1
            continue
        out.append(f"    {d}: first vintage {first.isoformat()} ({lag}d)")
        lags.append(lag)

    if not lags:
        out.append("\n  VERDICT: FAIL — no first-vintage date on any "
                   "observation. The lag cannot be corrected for on data that "
                   "does not carry it.")
        return False
    if missing:
        out.append(f"\n  VERDICT: FAIL — {missing} observation(s) carry no "
                   "usable vintage date, so a backtest would silently use the "
                   "period date for those.")
        return False

    lags.sort()
    med = lags[len(lags) // 2]
    out.append(f"    lag min {lags[0]}d, median {med}d, max {lags[-1]}d")
    if med < MIN_LAG_DAYS:
        out.append(f"\n  VERDICT: FAIL — median lag under {MIN_LAG_DAYS} days "
                   "is not a real publication date; it is the period date "
                   "wearing another name.")
        return False

    out.append("\n  VERDICT: PASS — the first vintage postdates the period it "
               "describes, so it can be used as the point of availability.")
    return True


REFUSAL = """
=======================================================================
REFUSING: ALFRED data does not deliver usable point-in-time values. Do
NOT register a macro gate on this source — a claim scored against it
would be inflated and indistinguishable from a real edge.

Macro may still be used as LIVE JUDGE CONTEXT ONLY: judge-only, so
under invariant #2 it can veto or shrink and can never create a trade;
ungated; and recorded as a sim/live divergence. That is exactly the
shape W7 used for news memory.

This leaves §31's original decision standing unchanged: cross-asset
prices remain the only orthogonal input that is point-in-time by
construction.

EDGE stands at 1 pass in 15 and K stays 15. Nothing is adopted
by this result.
=======================================================================
"""

BLESSING = """
=======================================================================
All three checks PASS. A macro claim MAY be pre-registered and
backtested against ALFRED vintages, subject to the usual discipline:
canonical-hash the spec before running, count it against Bonferroni K,
and use the exposure-matched benchmark.

Read every series AS OF the decision date, never with default realtime
params — the default for both is TODAY, which silently serves current
values and is the precise failure §31 refused to risk.

This does NOT mean macro helps. It means a test of whether it helps
would be meaningful. EDGE still stands at 1 pass in 15.
=======================================================================
"""


def main() -> int:
    out: list[str] = [
        "ALFRED VINTAGE PROBE",
        f"run {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "read-only; no repo state mutated; no strategy or rail touched",
        "closes the deferral §31 recorded: 'ALFRED vintages are the correct "
        "fix and are a separate project'",
    ]
    results = {
        "vintages_exist": check_vintages_exist(out),
        "revision_divergence": check_revision_divergence(out),
        "release_lag": check_release_lag(out),
    }
    out.append(f"\nFRED calls used: {_CALLS} (budget {CALL_BUDGET})")

    passed = all(v is True for v in results.values())
    out.append(BLESSING if passed else REFUSAL)
    for k, v in results.items():
        out.append(f"  {k:22} "
                   f"{'PASS' if v is True else 'FAIL' if v is False else 'UNDETERMINED'}")

    report = "\n".join(out)
    print(report)
    try:
        os.makedirs("research", exist_ok=True)
        p = f"research/alfred_vintages_{date.today().isoformat()}.txt"
        with open(p, "w") as f:
            f.write(report + "\n")
        print(f"\nreport -> {p}")
    except OSError as e:
        print(f"report not written: {type(e).__name__}", file=sys.stderr)

    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
