#!/usr/bin/env python3
"""Build the frozen T-bill rate aux series (^IRX) for §64+ specs.

Why ^IRX and not FRED
---------------------
Two §64 needs share one input: GEM's absolute-momentum filter needs a
risk-free return threshold, and the margin/financing model needs a borrow
rate basis. FRED's DTB3 would serve both but needs an API key the repo does
not hold yet. ^IRX — the CBOE 13-week T-bill discount rate index on Yahoo —
is the same market rate, keyless, daily back to 1960, and NEVER REVISED: a
market print is point-in-time by construction, the same argument §31 made
for HYG:LQD over FRED series. When a FRED key arrives, DTB3 vintages can
cross-check this file; they should agree to the basis-convention rounding.

Shape and location
------------------
Output is `load_bars_file` shape — {"^IRX": [{"ts", "open", "high", "low",
"close", "volume"}, ...]} — so `run_gate`'s `aux:` machinery loads it with
no new code. `close` is the ANNUALIZED DISCOUNT RATE IN PERCENT (e.g. 4.85
means 4.85%/yr), not a price. Consumers divide by 100 and day-count it.

It lands in `data/aux/` with its own MANIFEST, NOT `data/pit/`:
`data/pit/` is certified by the ETF survivorship probe, whose predicate is
fund-liveness — shape-mismatched for a rate index that is not a fund. The
verification below is this file's own certificate, printed and committed
alongside the data. An index cannot be survivor-selected out of existence
the way a screened stock universe can; the risks here are gaps and unit
errors, which is what gets checked.

Checks (all fatal):
  * coverage begins on/before the requested start and ends within 7
    calendar days of the requested end;
  * no value outside [-1, 25] percent (would indicate a unit error — ^IRX
    printed ~0.0x in ZIRP and ~15 in 1981; 2000+ range is tighter);
  * no gap between consecutive rows longer than 10 calendar days (holiday
    clusters are ~4);
  * a spot check: the 2000s carry rates near 5-6%, ZIRP years near 0 —
    asserts the series is not accidentally a price series.
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

SYMBOL = "^IRX"


def fetch(start: str, end: str) -> list[dict]:
    import yfinance as yf
    df = yf.Ticker(SYMBOL).history(start=start, end=end, auto_adjust=False)
    if df.empty:
        raise SystemExit(f"yfinance returned nothing for {SYMBOL}")
    rows = []
    for ts, r in df.iterrows():
        if r["Close"] != r["Close"]:      # NaN guard
            continue
        rows.append({"ts": ts.strftime("%Y-%m-%dT00:00:00Z"),
                     "open": round(float(r["Open"]), 4),
                     "high": round(float(r["High"]), 4),
                     "low": round(float(r["Low"]), 4),
                     "close": round(float(r["Close"]), 4),
                     "volume": 0})
    return rows


def verify(rows: list[dict], start: str, end: str) -> list[str]:
    problems = []
    first = rows[0]["ts"][:10]
    last = rows[-1]["ts"][:10]
    if date.fromisoformat(first) > date.fromisoformat(start) + timedelta(days=5):
        # ^IRX trades from 1960; a first row more than a holiday-cluster past
        # the requested start means the fetch is short, not the calendar.
        problems.append(f"coverage starts {first}, requested {start}")
    if date.fromisoformat(last) < date.fromisoformat(end) - timedelta(days=7):
        problems.append(f"coverage ends {last}, requested {end}")
    bad = [r for r in rows if not (-1.0 <= r["close"] <= 25.0)]
    if bad:
        problems.append(f"{len(bad)} rows outside [-1, 25] pct — unit error? "
                        f"first: {bad[0]}")
    prev = None
    for r in rows:
        d = date.fromisoformat(r["ts"][:10])
        if prev and (d - prev).days > 10:
            problems.append(f"gap {prev} -> {d} ({(d - prev).days}d)")
        prev = d
    # Not-a-price spot check: mean level in known regimes.
    def mean_in(lo, hi):
        vals = [r["close"] for r in rows if lo <= r["ts"][:10] <= hi]
        return sum(vals) / len(vals) if vals else None
    m2006 = mean_in("2006-01-01", "2006-12-31")
    m2013 = mean_in("2013-01-01", "2013-12-31")
    if m2006 is not None and not (3.0 <= m2006 <= 6.0):
        problems.append(f"2006 mean {m2006:.2f} not in [3,6] — wrong series?")
    if m2013 is not None and not (-0.2 <= m2013 <= 0.5):
        problems.append(f"2013 (ZIRP) mean {m2013:.2f} not in [-0.2,0.5]")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default=str(date.today()))
    args = ap.parse_args()

    rows = fetch(args.start, args.end)
    problems = verify(rows, args.start, args.end)
    if problems:
        print("REFUSING — the rate series failed its own checks:")
        for p in problems:
            print(f"  * {p}")
        return 1

    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"irx_{args.start}_{rows[-1]['ts'][:10]}.json.gz"
    path = os.path.join(OUT_DIR, name)
    payload = json.dumps({SYMBOL: rows}).encode()
    with open(path, "wb") as raw:
        # mtime=0 pins the gzip header so the sha256 is reproducible
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
            f.write(payload)
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()

    manifest = {}
    if os.path.exists(MANIFEST):
        manifest = json.load(open(MANIFEST))
    manifest[name] = {
        "sha256": sha, "symbol": SYMBOL, "rows": len(rows),
        "start": rows[0]["ts"][:10], "end": rows[-1]["ts"][:10],
        "unit": "annualized discount rate, PERCENT",
        "source": "yfinance ^IRX (CBOE 13-week T-bill index)",
        "built": str(date.today()),
        "note": ("market rate, never revised — PIT by construction; "
                 "cross-check against FRED DTB3 vintages when a key exists"),
    }
    json.dump(manifest, open(MANIFEST, "w"), indent=2)

    print(f"wrote {path}  ({len(rows)} rows, "
          f"{rows[0]['ts'][:10]} -> {rows[-1]['ts'][:10]})")
    print(f"sha256 {sha}")
    print("verification: PASS (coverage, range, gaps, regime spot checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
