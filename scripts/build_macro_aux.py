#!/usr/bin/env python3
"""Build the frozen macro-regime aux series (UNRATE, first-print only) for
the §64 macro gate (config `risk.macro_gate`) — the spec that
`probe_alfred_vintages.py` was written to license.

Why UNRATE and why "first print only"
--------------------------------------
`config.yaml`'s `risk.macro_gate` scaffold names UNRATE as the intended
series and describes the aux it expects as "a first-print ALFRED vintage
series." Two decisions follow from that, made HERE rather than left
implicit:

1. First print, never a later revision. FRED's `output_type=4`
   ("Initial Release Only") returns exactly one row per observation date —
   the value as FIRST published, with `realtime_start` set to the day that
   vintage became public. This is the ALFRED analogue of `^IRX` being a
   market print that is never revised: whatever this file stores is exactly
   what a trading agent running live on that date would have seen, and nothing
   a later benchmark revision changed retroactively.
2. A DAILY step series, not one row per monthly release. `risk.macro_blocked`
   reads `ma_days` through `_asof_closes`, which takes "the last N *entries*
   with ts <= decision date" — not the last N calendar days. Fed the sparse
   ~monthly release series directly, `ma_days: 252` (a ~1-trading-year
   window, the same convention as the credit gate) would silently become a
   252-MONTH trailing average — 21 years, not 1. This script forward-fills
   each first-print value onto every business day until the next release
   supersedes it, so `ma_days` means the same thing here as it does for a
   daily price series. Weekends are dropped; U.S. market holidays are not
   specially excluded — the daily calendar is Mon-Fri only, a deliberate
   simplification documented here: for a step function that moves at most
   once a month, the ~9 holiday rows/year this admits change a 252-row
   trailing mean by a negligible amount, and precision here is not the axis
   this gate's edge (if any) will be decided on.

Shape and location
-------------------
Same `load_bars_file` shape as `build_rate_aux.py`'s `^IRX` output —
`{"UNRATE": [{"ts","open","high","low","close","volume"}, ...]}` — so
`run_gate`'s `aux:` machinery loads it with no new code, and the SAME `close`
value at every row within a release window (a step, not a ramp).

Checks (all fatal):
  * coverage begins on/before the requested start and ends within 40
    calendar days of the requested end (UNRATE's own publication lag can run
    to ~80 days, so a stricter window than the 7-day one `^IRX` uses would
    misfire on a completely healthy run);
  * every release's `realtime_start` postdates its observation `date` by at
    least MIN_LAG_DAYS — the same anti-lookahead assertion
    `probe_alfred_vintages.py` makes generically, checked here directly
    against the series actually being shipped, not a proxy;
  * no gap between consecutive RELEASES longer than 95 calendar days (a
    normal cadence is ~30; the widest real gap in this series is the 2025
    federal shutdown, which delayed the September 2025 report to Nov 20 and
    left October 2025 with no independent release at all — a genuine ~81-day
    gap this loader must tolerate, not paper over by silently widening the
    threshold with no record of why);
  * no value outside [2, 20] percent (would indicate a unit error — 2000+
    UNRATE has ranged 3.4 to 14.8);
  * a regime spot check: 2009 (post-GFC) mean well above 2019 (expansion low).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT_DIR = os.path.join(ROOT, "data", "aux")
MANIFEST = os.path.join(OUT_DIR, "MANIFEST.json")

SERIES = "UNRATE"
MIN_LAG_DAYS = 5
BASE = "https://api.stlouisfed.org/fred"

# ALFRED's full realtime window — required to see the FIRST vintage rather
# than whatever output_type=4 would silently default to (TODAY, the same
# trap RT_ALL in probe_alfred_vintages.py exists to avoid).
RT_ALL = {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31"}


def _key() -> str:
    k = os.environ.get("FRED_API_KEY", "").strip()
    if not k:
        print("FRED_API_KEY is not set. Install it with "
              "./scripts/set_fred_key.sh (do not paste it into a chat).",
              file=sys.stderr)
        raise SystemExit(2)
    return k


def fetch_first_releases(start: str, end: str) -> list[dict]:
    """One row per observation date: {date, realtime_start, value}, all
    FIRST-print (output_type=4). One API call covers the whole span —
    FRED's per-call limit is far above the ~300 rows a 25-year monthly
    series produces."""
    import urllib.parse
    import urllib.request

    params = {"series_id": SERIES, "output_type": 4,
              "observation_start": start, "observation_end": end,
              "api_key": _key(), "file_type": "json", **RT_ALL}
    url = f"{BASE}/series/observations?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        payload = json.loads(r.read().decode("utf-8", "replace"))
    rows = []
    for o in payload.get("observations", []):
        try:
            rows.append({"date": o["date"], "realtime_start": o["realtime_start"],
                         "value": float(o["value"])})
        except (KeyError, ValueError):
            continue  # a "." (missing) value or malformed row — skip, don't guess
    rows.sort(key=lambda r: r["date"])
    return rows


def step_to_daily(releases: list[dict], start: str, end: str) -> list[dict]:
    """Forward-fill each first-print value onto every weekday from its
    publication date until the next release supersedes it. Days before the
    first release's `realtime_start` carry no row — there is nothing a live
    bot could have known yet, and `macro_blocked` already fails open on an
    empty as-of window."""
    if not releases:
        return []
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    idx = -1  # index of the most recent release known as-of the current day
    for n in range((d1 - d0).days + 1):
        day = d0 + timedelta(days=n)
        if day.weekday() >= 5:  # Sat/Sun
            continue
        iso = day.isoformat()
        while (idx + 1 < len(releases)
               and releases[idx + 1]["realtime_start"] <= iso):
            idx += 1
        if idx < 0:
            continue  # nothing published yet as of this day
        v = releases[idx]["value"]
        out.append({"ts": f"{iso}T00:00:00Z", "open": v, "high": v, "low": v,
                    "close": v, "volume": 0})
    return out


def verify(releases: list[dict], daily: list[dict], start: str, end: str) -> list[str]:
    problems = []
    if not releases:
        return ["no releases returned"]

    last_pub = releases[-1]["realtime_start"]
    if date.fromisoformat(releases[0]["date"]) > date.fromisoformat(start) + timedelta(days=35):
        problems.append(f"first observation {releases[0]['date']}, requested {start}")
    if date.fromisoformat(last_pub) < date.fromisoformat(end) - timedelta(days=40):
        problems.append(f"last publication {last_pub}, requested through {end}")

    for r in releases:
        lag = (date.fromisoformat(r["realtime_start"])
               - date.fromisoformat(r["date"])).days
        if lag < MIN_LAG_DAYS:
            problems.append(f"{r['date']}: publication lag {lag}d < {MIN_LAG_DAYS}d "
                            "— looks like a period date wearing a vintage date")

    prev = None
    for r in releases:
        d = date.fromisoformat(r["date"])
        if prev and (d - prev).days > 95:
            problems.append(f"release gap {prev} -> {d} ({(d - prev).days}d)")
        prev = d

    bad = [r for r in releases if not (2.0 <= r["value"] <= 20.0)]
    if bad:
        problems.append(f"{len(bad)} rows outside [2, 20] pct — unit error? "
                        f"first: {bad[0]}")

    def mean_in(lo, hi):
        vals = [r["value"] for r in releases if lo <= r["date"] <= hi]
        return sum(vals) / len(vals) if vals else None
    m2009 = mean_in("2009-01-01", "2009-12-31")
    m2019 = mean_in("2019-01-01", "2019-12-31")
    if m2009 is not None and m2019 is not None and not (m2009 > m2019 + 2.0):
        problems.append(f"2009 mean {m2009:.2f} not clearly above 2019 mean "
                        f"{m2019:.2f} — regime shape looks wrong")

    if not daily:
        problems.append("daily step series is empty")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default=str(date.today()))
    args = ap.parse_args()

    releases = fetch_first_releases(args.start, args.end)
    daily = step_to_daily(releases, args.start, args.end)
    problems = verify(releases, daily, args.start, args.end)
    if problems:
        print("REFUSING — the macro aux series failed its own checks:")
        for p in problems:
            print(f"  * {p}")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"unrate_{args.start}_{daily[-1]['ts'][:10]}.json.gz"
    path = os.path.join(OUT_DIR, name)
    payload = json.dumps({SERIES: daily}).encode()
    with open(path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            f.write(payload)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()

    manifest = {}
    if os.path.exists(MANIFEST):
        manifest = json.load(open(MANIFEST))
    manifest[name] = {
        "sha256": sha, "symbol": SERIES, "rows": len(daily),
        "releases": len(releases),
        "start": daily[0]["ts"][:10], "end": daily[-1]["ts"][:10],
        "unit": "unemployment rate, PERCENT, first ALFRED vintage only",
        "source": "FRED/ALFRED UNRATE, output_type=4 (initial release), "
                  "forward-filled to a Mon-Fri daily step series",
        "built": str(date.today()),
        "note": ("point-in-time by construction: each day's value is the "
                 "FIRST published print in effect on that day, never a "
                 "later benchmark revision"),
    }
    json.dump(manifest, open(MANIFEST, "w"), indent=2)

    print(f"wrote {path}  ({len(daily)} daily rows from {len(releases)} "
         f"releases, {daily[0]['ts'][:10]} -> {daily[-1]['ts'][:10]})")
    print(f"sha256 {sha}")
    print("verification: PASS (coverage, lag, gaps, range, regime spot check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
