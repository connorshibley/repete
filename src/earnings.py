"""Earnings calendar (yfinance) — feeds the earnings-blackout entry filter.

Why: earnings gaps blow through ATR stops, and a swing hold with a 2-day
minimum MUST sit through whatever happens overnight. Blocking new entries
right before a scheduled report removes structurally uncompensated risk.

Data contract:
  * get_dates(symbol) -> sorted ISO dates (past AND scheduled future),
    cached in memory/earnings_cache.json for a day per symbol.
  * ETFs simply have no earnings dates -> never blacked out.
  * EVERY failure fails OPEN (no dates -> no blackout) and is logged:
    a broken calendar source must never stop the bot trading, it only
    loses this one protection.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("earnings")

CACHE_PATH = "memory/earnings_cache.json"
CACHE_TTL_HOURS = 24
FETCH_LIMIT = 60  # ~15 years of quarters; includes scheduled future dates


def _load_cache(path: str = CACHE_PATH) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict, path: str = CACHE_PATH):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(cache, f)
    except OSError as e:
        log.warning("earnings cache write failed: %s", e)


def _fetch(symbol: str) -> list[str] | None:
    """One yfinance lookup -> sorted ISO dates, or None on any failure."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import yfinance as yf
            df = yf.Ticker(symbol).get_earnings_dates(limit=FETCH_LIMIT)
        if df is None or df.empty:
            return []
        return sorted({str(d)[:10] for d in df.index})
    except Exception as e:  # noqa: BLE001 — fail open, always
        log.warning("earnings fetch failed for %s: %s", symbol, e)
        return None


def get_dates(symbol: str, cache_path: str = CACHE_PATH) -> list[str]:
    """Cached earnings dates for one symbol ([] = none known / ETF)."""
    cache = _load_cache(cache_path)
    entry = cache.get(symbol)
    now = datetime.now(timezone.utc)
    if entry:
        try:
            age = now - datetime.fromisoformat(entry["fetched"])
            if age < timedelta(hours=CACHE_TTL_HOURS):
                return entry["dates"]
        except (KeyError, ValueError):
            pass
    dates = _fetch(symbol)
    if dates is None:  # fetch failed: serve stale cache if any, else nothing
        return entry.get("dates", []) if entry else []
    cache[symbol] = {"fetched": now.isoformat(), "dates": dates}
    _save_cache(cache, cache_path)
    return dates


def next_within(dates: list[str], on_date: str, days: int) -> bool:
    """True when the next earnings date on/after `on_date` falls within
    `days` calendar days. Pure — shared by live filter and backtest."""
    if not dates or days <= 0:
        return False
    d0 = date.fromisoformat(on_date[:10])
    for d in dates:  # sorted ascending
        dd = date.fromisoformat(d)
        if dd >= d0:
            return (dd - d0).days <= days
    return False


def blackout_symbols(symbols: list[str], days: int,
                     today: str | None = None,
                     cache_path: str = CACHE_PATH) -> set[str]:
    """Symbols whose next scheduled earnings report is within `days` days.
    Used by the live cycle for the entry filter; wholly deterministic given
    the calendar data."""
    if days <= 0:
        return set()
    today = today or date.today().isoformat()
    out = set()
    for sym in symbols:
        if next_within(get_dates(sym, cache_path), today, days):
            out.add(sym)
    return out
