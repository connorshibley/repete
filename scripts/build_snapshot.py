#!/usr/bin/env python3
"""Build a FROZEN bars snapshot for gate reproducibility.

Why this exists
---------------
Gate sections §1–§13 in `knowledge/backtest_candidates.md` were all scored on
one frozen file, `memory/bars_snapshot_2020_2026-07-10.json`. `memory/` is in
`.gitignore`, so that file was never committed — and it is now gone. Every
number those sections quote is, today, **unreproducible**. That is a process
bug, not bad luck: a snapshot that a gate depends on must live in version
control next to the verdicts it supports.

So snapshots now land in `data/snapshots/` (committed) and carry a SHA256 in
`data/snapshots/MANIFEST.json`. `--verify` re-hashes and fails loudly on drift,
which is what makes "re-running §14 reproduces §14" a checkable claim rather
than a hope.

Source: yfinance. No API keys, so any future session can rebuild without
credentials — that is the whole point.

    python scripts/build_snapshot.py                 # build the default snapshot
    python scripts/build_snapshot.py --verify        # check hashes, touch nothing

NOT interchangeable with the old file
-------------------------------------
yfinance serves split/dividend-ADJUSTED closes; the retired Alpaca snapshot
served raw prices. Same tickers, different numbers. Any strategy comparison
must therefore re-measure its baseline *inside this snapshot* — carrying a
figure across the two is comparing two different datasets. Sections written
against this file say so in their header.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots")
MANIFEST = os.path.join(SNAP_DIR, "MANIFEST.json")

DEFAULT_START = "2020-01-01"
DEFAULT_END = "2026-07-10"


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


def fetch(symbols: list, start: str, end: str) -> dict:
    """{symbol: [{ts, open, high, low, close, volume}, ...]} in broker.bars shape."""
    import yfinance as yf

    # end is inclusive for us, exclusive for yfinance.
    end_excl = (datetime.fromisoformat(end).date().toordinal() + 1)
    end_excl = datetime.fromordinal(end_excl).date().isoformat()

    raw = yf.download(symbols, start=start, end=end_excl, interval="1d",
                      auto_adjust=True, progress=False, group_by="ticker",
                      threads=True)
    if raw is None or raw.empty:
        raise SystemExit("yfinance returned nothing — no network, or bad symbols")

    out: dict = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all")
        except KeyError:
            print(f"  !! {sym}: absent from the response — SKIPPED", file=sys.stderr)
            continue
        rows = []
        for ts, r in df.iterrows():
            if any(r.get(c) is None or r.get(c) != r.get(c)      # NaN check
                   for c in ("Open", "High", "Low", "Close")):
                continue
            rows.append({
                "ts": ts.tz_localize("UTC").isoformat() if ts.tzinfo is None
                      else ts.tz_convert("UTC").isoformat(),
                "open": round(float(r["Open"]), 6),
                "high": round(float(r["High"]), 6),
                "low": round(float(r["Low"]), 6),
                "close": round(float(r["Close"]), 6),
                "volume": float(r.get("Volume", 0) or 0),
            })
        if not rows:
            print(f"  !! {sym}: zero usable bars — SKIPPED", file=sys.stderr)
            continue
        out[sym] = rows
    return out


def sanity_check(bars: dict, symbols: list, start: str, end: str) -> list:
    """Return a list of problems. A snapshot that silently lost half its
    universe would quietly change every gate verdict downstream, so these are
    reported and — for the fatal ones — refuse to write."""
    problems = []
    missing = [s for s in symbols if s not in bars]
    if missing:
        problems.append(f"FATAL: {len(missing)} symbols missing: {missing}")

    lengths = {s: len(v) for s, v in bars.items()}
    if lengths:
        longest = max(lengths.values())
        # A 2020-2026 daily series is ~1630 bars. Anything under 80% of the
        # longest series is a listing gap or a truncated download, not a bar
        # count — and it would bias any cross-sectional strategy.
        short = {s: n for s, n in lengths.items() if n < 0.8 * longest}
        if short:
            problems.append(f"WARN: short series (<80% of {longest} bars): {short}")

    for sym, rows in bars.items():
        for r in rows[:1] + rows[-1:]:
            if not (r["low"] <= r["open"] <= r["high"]
                    and r["low"] <= r["close"] <= r["high"]):
                problems.append(f"FATAL: {sym} {r['ts']} OHLC bounds violated")
        if any(r["close"] <= 0 for r in rows):
            problems.append(f"FATAL: {sym} has a non-positive close")
        ts = [r["ts"] for r in rows]
        if ts != sorted(ts):
            problems.append(f"FATAL: {sym} bars are not chronologically sorted")
        if len(set(ts)) != len(ts):
            problems.append(f"FATAL: {sym} has duplicate timestamps")
    return problems


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as f:
            return json.load(f)
    return {"snapshots": {}}


def cmd_verify() -> int:
    man = load_manifest()
    if not man["snapshots"]:
        print("no snapshots recorded in MANIFEST.json")
        return 1
    bad = 0
    for name, meta in sorted(man["snapshots"].items()):
        path = os.path.join(SNAP_DIR, name)
        if not os.path.exists(path):
            print(f"MISSING  {name}")
            bad += 1
            continue
        actual = sha256_file(path)
        ok = actual == meta["sha256"]
        print(f"{'OK      ' if ok else 'DRIFTED '} {name}  "
              f"{meta['n_symbols']} symbols  {meta['n_bars']} bars")
        if not ok:
            print(f"         expected {meta['sha256'][:16]}… got {actual[:16]}…")
            bad += 1
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END, help="inclusive")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="default: every symbol in config.yaml")
    p.add_argument("--out", default=None, help="default: bars_<start>_<end>.json.gz")
    p.add_argument("--verify", action="store_true",
                   help="re-hash committed snapshots and exit")
    args = p.parse_args()

    if args.verify:
        return cmd_verify()

    symbols = args.symbols or _config_symbols()
    name = args.out or f"bars_{args.start}_{args.end}.json.gz"
    os.makedirs(SNAP_DIR, exist_ok=True)
    path = os.path.join(SNAP_DIR, name)

    print(f"fetching {len(symbols)} symbols {args.start} → {args.end} (yfinance)…")
    bars = fetch(symbols, args.start, args.end)

    problems = sanity_check(bars, symbols, args.start, args.end)
    for msg in problems:
        print("  " + msg, file=sys.stderr)
    if any(m.startswith("FATAL") for m in problems):
        print("refusing to write a snapshot that failed sanity checks",
              file=sys.stderr)
        return 2

    # gzip: 7.8 MB -> 1.8 MB, and these files live in git forever.
    # mtime=0 so rebuilding identical data yields an identical hash.
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as gz:
        gz.write(json.dumps(bars, separators=(",", ":"),
                            sort_keys=True).encode())

    n_bars = sum(len(v) for v in bars.values())
    man = load_manifest()
    man["snapshots"][name] = {
        "sha256": sha256_file(path),
        "source": "yfinance (auto_adjust=True — split/dividend adjusted)",
        "start": args.start,
        "end": args.end,
        "n_symbols": len(bars),
        "n_bars": n_bars,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "note": "Adjusted prices. NOT comparable with the retired Alpaca raw-price "
                "snapshot used by §1–§13; re-baseline inside this file.",
    }
    with open(MANIFEST, "w") as f:
        json.dump(man, f, indent=2, sort_keys=True)

    size_mb = os.path.getsize(path) / 1e6
    print(f"wrote {path}  ({len(bars)} symbols, {n_bars} bars, {size_mb:.1f} MB)")
    print(f"sha256 {man['snapshots'][name]['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
