"""Intraday fill timing (§25) — hourly bars, regular session only.

The trap this module exists to avoid
------------------------------------
The obvious build is "compute signals on the committed DAILY snapshot, fill at
prices from the HOURLY snapshot." That is wrong and would have silently
fabricated P&L on every single fill:

  * the daily snapshot is **yfinance, dividend-ADJUSTED**
  * the hourly snapshot is **Alpaca, RAW**

Measured on SPY, the raw/adjusted ratio drifts smoothly from 0.9978 to 1.0879
across 1,490 sessions — up to **8.8%**. An entry priced from one basis and
exited on the other books that gap as pure invented profit or loss.

So §25 uses **one source for both**: the hourly Alpaca bars are resampled up to
daily bars here, and those daily bars drive the signals while the underlying
hourly bars drive the fills. Same vendor, same basis, internally consistent.
(Resampled daily intraday-returns correlate 0.9888 with the adjusted daily
snapshot, so the *shape* is intact — only the price basis differs.)

Consequence, and it is the METHOD NOTE 3 lesson again: **§25 numbers are not
comparable with §1–§24 numbers.** The baseline must be re-established inside
this data, which is exactly what the gate does.

Regular trading hours only
--------------------------
The snapshot spans 04:00–20:00 ET. Filling in pre/post-market at the
regular-session 5 bps slippage would be fantasy — those sessions are thin and
spreads are far wider, which would bias the experiment toward a falsely good
result. Everything here is restricted to bars starting 09:00–15:59 ET.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Bars are labelled by their START hour. The 09:00 bar carries the 09:30 open;
# the 15:00 bar carries the 16:00 close. 16:00 onward is after-hours.
RTH_FIRST_HOUR = 9
RTH_LAST_HOUR = 15

# Candidate fill hours a gate may ask for (09:00 == "at the open").
VALID_FILL_HOURS = tuple(range(RTH_FIRST_HOUR, RTH_LAST_HOUR + 1))


def et_hour(ts: str) -> int:
    return datetime.fromisoformat(ts).astimezone(ET).hour


def et_date(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(ET).date().isoformat()


def is_rth(ts: str) -> bool:
    return RTH_FIRST_HOUR <= et_hour(ts) <= RTH_LAST_HOUR


def resample_daily(rows: list) -> list:
    """Hourly bars -> daily bars, regular session only.

    open  = first RTH bar's open   high = max RTH high
    close = last RTH bar's close   low  = min RTH low   volume = sum
    """
    out: dict = {}
    for b in rows:
        ts = b.get("ts") or ""
        if not ts or not is_rth(ts):
            continue
        day = et_date(ts)
        cur = out.get(day)
        if cur is None:
            out[day] = {"ts": ts, "open": b["open"], "high": b["high"],
                        "low": b["low"], "close": b["close"],
                        "volume": float(b.get("volume") or 0)}
            continue
        cur["high"] = max(cur["high"], b["high"])
        cur["low"] = min(cur["low"], b["low"])
        cur["close"] = b["close"]            # rows arrive chronologically
        cur["volume"] += float(b.get("volume") or 0)
    return [out[k] for k in sorted(out)]


def resample_all(hourly: dict) -> dict:
    """{symbol: hourly rows} -> {symbol: daily rows}. Symbols with no RTH data
    are dropped rather than emitted empty."""
    out = {}
    for sym, rows in hourly.items():
        daily = resample_daily(rows)
        if daily:
            out[sym] = daily
    return out


def index_hours(hourly: dict) -> dict:
    """{symbol: {date: {et_hour: bar}}} for O(1) fill lookup. RTH only."""
    idx: dict = {}
    for sym, rows in hourly.items():
        per_day: dict = {}
        for b in rows:
            ts = b.get("ts") or ""
            if not ts or not is_rth(ts):
                continue
            per_day.setdefault(et_date(ts), {})[et_hour(ts)] = b
        idx[sym] = per_day
    return idx


def fill_price(idx: dict, symbol: str, day: str, hour: int) -> float | None:
    """Open of `symbol`'s bar starting at `hour` ET on `day`, or None.

    None is returned — never a substituted price — when the session is short
    (half-days end at 13:00), the symbol has no coverage for that date (11 of
    38 symbols start later than the rest), or the hour is outside RTH. Callers
    must fall back explicitly and COUNT it: a silent fallback would let missing
    coverage masquerade as a result.
    """
    if hour < RTH_FIRST_HOUR or hour > RTH_LAST_HOUR:
        return None
    bar = (idx.get(symbol) or {}).get(day, {}).get(hour)
    if bar is None:
        return None
    px = bar.get("open")
    return float(px) if px and px > 0 else None
