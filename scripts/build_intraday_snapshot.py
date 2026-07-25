#!/usr/bin/env python3
"""Build a FROZEN INTRADAY bars snapshot (Phase 0 of the intraday project).

Why Alpaca and not yfinance
---------------------------
`build_snapshot.py` uses yfinance because it needs no credentials — the right
call for daily bars. It cannot serve this one: yfinance caps intraday history
at ~2 years hourly and ~60 days for 5-minute, which would leave an intraday
backtest with no in-sample period separate from the already-mined OOS window.

Alpaca's basic tier returns **6 years** of both (verified 2026-07-24: SPY
hourly 23,861 bars and 5-minute 281,810 bars, both back to 2020-07-27) — the
same window as the committed daily snapshot, so IS/OOS splits line up exactly.

The cost is that this builder REQUIRES ALPACA KEYS, unlike the daily one. That
is a real reproducibility regression and is recorded rather than hidden: a
future session without keys can verify the committed hash but cannot rebuild
the file from scratch.

Resolution
----------
**1Hour is the default** because ~38 symbols x ~24k bars ≈ 900k bars ≈ 26 MB
gzipped, which git can hold, so the snapshot stays committed and verifiable.
5-minute is ~10.7M bars (~300 MB) and would need Git LFS or external storage —
available via `--timeframe 5Min` but it will not be committed by default.

    python scripts/build_intraday_snapshot.py                 # hourly, 2020->2026
    python scripts/build_intraday_snapshot.py --verify        # hash check only
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import intraday

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots")
MANIFEST = os.path.join(SNAP_DIR, "MANIFEST.json")

DEFAULT_START = "2020-08-01"      # Alpaca intraday begins ~2020-07-27
DEFAULT_END = "2026-07-10"        # matches the daily snapshot's end
DEFAULT_TF = "1Hour"


def _config_symbols() -> list:
    import yaml
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    with open(cfg_path) as f:
        return list(yaml.safe_load(f)["symbols"])


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(symbols: list, start: str, end: str, timeframe: str) -> dict:
    """{symbol: [{ts, open, high, low, close, volume}, ...]} in broker.bars shape.

    Fetched per symbol and windowed in chunks: a 6-year 5-minute request for 38
    symbols in one call would be millions of rows and will time out.
    """
    from dotenv import load_dotenv
    load_dotenv()
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.data.enums import Adjustment

    tf_map = {
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    }
    if timeframe not in tf_map:
        raise SystemExit(f"unsupported timeframe {timeframe!r}; "
                         f"choose from {sorted(tf_map)}")

    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        raise SystemExit(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY required — this builder cannot "
            "use yfinance (it caps intraday history far too short). See the "
            "module docstring.")

    client = StockHistoricalDataClient(key, secret)
    t0 = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    t1 = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    out: dict = {}
    for i, sym in enumerate(symbols, 1):
        rows: list = []
        cur = t0
        while cur < t1:
            chunk_end = min(cur + timedelta(days=365), t1)
            try:
                resp = client.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=sym, timeframe=tf_map[timeframe],
                    start=cur, end=chunk_end,
                    # MUST be adjusted. The first build of this snapshot used
                    # RAW (the default) and contained 11 unadjusted splits —
                    # AAPL 4:1, AMZN 20:1, GOOGL 20:1, NVDA 4:1 and 10:1,
                    # TSLA 5:1 and 3:1, AVGO 10:1, WMT 3:1 and two ETF splits.
                    # The simulator read each as an overnight 50-95% collapse,
                    # which fired stops on healthy positions, produced an ATR
                    # of 159.93 against a $171 price, and voided the first §25
                    # gate run entirely.
                    adjustment=Adjustment.ALL))
                for b in resp.data.get(sym, []):
                    rows.append({
                        "ts": b.timestamp.astimezone(timezone.utc).isoformat(),
                        "open": round(float(b.open), 6),
                        "high": round(float(b.high), 6),
                        "low": round(float(b.low), 6),
                        "close": round(float(b.close), 6),
                        "volume": float(b.volume or 0),
                    })
            except Exception as e:  # noqa: BLE001 — one window must not kill the run
                print(f"  !! {sym} {cur.date()}..{chunk_end.date()}: {e}",
                      file=sys.stderr)
            cur = chunk_end
        # De-duplicate on timestamp (chunk edges can overlap) and sort.
        seen: dict = {}
        for r in rows:
            seen[r["ts"]] = r
        rows = [seen[k] for k in sorted(seen)]
        if rows:
            out[sym] = rows
        print(f"  [{i:>2}/{len(symbols)}] {sym:<6} {len(rows):>7,} bars",
              flush=True)
    return out


def sanity_check(bars: dict, symbols: list) -> list:
    """Problems worth refusing to write. Same spirit as the daily builder: a
    snapshot that silently lost half its universe would change every gate
    verdict downstream."""
    problems = []
    missing = [s for s in symbols if s not in bars]
    if missing:
        problems.append(f"FATAL: {len(missing)} symbols missing: {missing}")
    lengths = {s: len(v) for s, v in bars.items()}
    if lengths:
        longest = max(lengths.values())
        short = {s: n for s, n in lengths.items() if n < 0.8 * longest}
        if short:
            problems.append(f"WARN: short series (<80% of {longest}): {short}")
    for sym, rows in bars.items():
        for r in rows[:1] + rows[-1:]:
            if not (r["low"] <= r["open"] <= r["high"]
                    and r["low"] <= r["close"] <= r["high"]):
                problems.append(f"FATAL: {sym} {r['ts']} OHLC bounds violated")
        if any(r["close"] <= 0 for r in rows):
            problems.append(f"FATAL: {sym} has a non-positive close")
        ts = [r["ts"] for r in rows]
        if ts != sorted(ts):
            problems.append(f"FATAL: {sym} bars not chronologically sorted")
        if len(set(ts)) != len(ts):
            problems.append(f"FATAL: {sym} has duplicate timestamps")

        # UNADJUSTED-SPLIT DETECTOR. The first build of this file used raw
        # prices and carried 11 splits (AAPL 4:1, AMZN 20:1, GOOGL 20:1, NVDA
        # 10:1, TSLA 5:1, ...). The simulator read each as an overnight
        # 50-95% collapse — firing stops on healthy positions and voiding a
        # whole gate run. Nothing caught it except a stray ATR in the logs.
        #
        # Compare SESSION to SESSION, not hour to hour: a real overnight move
        # is spread across thin extended-hours bars, so an hourly comparison
        # dilutes it and can miss the very thing this guards against. Splits
        # are 50-95% and would survive either test, but the session view is
        # the honest one.
        #
        # 45% is deliberately above the largest genuine single-session move in
        # this universe (AMD +35.8% on 2025-10-03, real news) so a true price
        # move is never mistaken for a corporate action.
        sessions = intraday.resample_daily(rows)
        for a, b in zip(sessions, sessions[1:]):
            if a["close"] <= 0:
                continue
            chg = (b["open"] - a["close"]) / a["close"]
            if abs(chg) > 0.45:
                problems.append(
                    f"FATAL: {sym} {a['ts'][:10]}->{b['ts'][:10]} moves "
                    f"{chg:+.1%} ({a['close']:.2f}->{b['open']:.2f}) — "
                    f"unadjusted split? build with adjustment=ALL")
                break
    return problems


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {"snapshots": {}}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument("--timeframe", default=DEFAULT_TF,
                   choices=["1Hour", "15Min", "5Min"])
    p.add_argument("--symbols", nargs="+", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--verify", action="store_true")
    args = p.parse_args()

    if args.verify:
        man = load_manifest()
        bad = 0
        for name, meta in sorted(man["snapshots"].items()):
            path = os.path.join(SNAP_DIR, name)
            if not os.path.exists(path):
                print(f"MISSING  {name}")
                bad += 1
                continue
            ok = sha256_file(path) == meta["sha256"]
            print(f"{'OK      ' if ok else 'DRIFTED '} {name}  "
                  f"{meta['n_symbols']} symbols  {meta['n_bars']:,} bars")
            bad += 0 if ok else 1
        return 1 if bad else 0

    symbols = args.symbols or _config_symbols()
    tf = args.timeframe
    name = args.out or f"bars_{tf}_{args.start}_{args.end}.json.gz"
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, name)

    print(f"fetching {len(symbols)} symbols {args.start} → {args.end} "
          f"@ {tf} (Alpaca)…")
    bars = fetch(symbols, args.start, args.end, tf)

    problems = sanity_check(bars, symbols)
    for m in problems:
        print("  " + m, file=sys.stderr)
    if any(m.startswith("FATAL") for m in problems):
        print("refusing to write a snapshot that failed sanity checks",
              file=sys.stderr)
        return 2

    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as gz:
        gz.write(json.dumps(bars, separators=(",", ":"),
                            sort_keys=True).encode())

    n_bars = sum(len(v) for v in bars.values())
    man = load_manifest()
    man["snapshots"][name] = {
        "sha256": sha256_file(path),
        "source": f"Alpaca {tf} (adjustment=ALL — split AND dividend adjusted)",
        "start": args.start, "end": args.end, "timeframe": tf,
        "n_symbols": len(bars), "n_bars": n_bars,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "note": "INTRADAY, adjustment=ALL. Requires Alpaca keys to rebuild "
                "(yfinance caps intraday history far too short). The FIRST "
                "build used RAW and carried 11 unadjusted splits that the "
                "simulator read as overnight collapses — it voided a gate run. "
                "Different vendor from the yfinance daily file; re-baseline "
                "inside this file.",
    }
    with open(MANIFEST, "w") as f:
        json.dump(man, f, indent=2, sort_keys=True)

    size_mb = os.path.getsize(path) / 1e6
    print(f"\nwrote {path}")
    print(f"  {len(bars)} symbols, {n_bars:,} bars, {size_mb:.1f} MB")
    print(f"  sha256 {man['snapshots'][name]['sha256']}")
    if size_mb > 90:
        print("  !! over ~90 MB — too large for a normal git object; consider "
              "Git LFS or leaving this file uncommitted", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
