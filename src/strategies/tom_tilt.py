"""§64 turn-of-the-month tilt (McConnell & Xu, FAJ 2008): hold the index fund
only across the turn of the month — where their 1926-2005 sample concentrates
essentially the entire equity premium — and stand aside the rest of the time.

WINDOW, STATED EXACTLY. Target exposure is the McConnell-Xu [-1, +3] window.
Signals fire on a bar's close and fill at the NEXT open, so: the BUY fires
when exactly one weekday remains in the month after today (fill = the last
trading day's open) and the SELL fires on the close of the third trading day
of the new month (fill = the fourth day's open). Held days: -1 through +3.

THE CALENDAR IS THE SIGNAL, AND IT IS PIT-PERFECT — no price, no estimate,
nothing revisable. The one approximation is that "one weekday remains" reads
the civil calendar, not the exchange calendar: a month-end holiday (rare;
NYSE closures cluster mid-month) shifts entry one day. That error is fixed,
known in advance, and symmetric across arms — it cannot be fitted.

Calendar effects are the canonical data-mining graveyard, which is why the
window is the published one, unmodified, and why the spec's reading rule
discounts any pass driven by a single period. Ships `enabled: false`; any
tilt leverage comes from a spec arm's `risk.margin.multiplier`.
"""
from datetime import date, timedelta

from strategies.base import Signal

NAME = "tom_tilt"
NEEDS_CROSS_SECTION = False


def required_lookback(params: dict) -> int:
    # Enough bars to count trading days since the month began.
    return 30


def _bar_date(bar: dict) -> date:
    return date.fromisoformat(bar["ts"][:10])


def weekdays_remaining_after(d: date) -> int:
    """Civil-calendar weekdays strictly after `d` in d's month."""
    n, cur = 0, d + timedelta(days=1)
    while cur.month == d.month:
        if cur.weekday() < 5:
            n += 1
        cur += timedelta(days=1)
    return n


def trading_day_of_month(bars: list[dict]) -> int:
    """1-based index of the last bar within its calendar month."""
    last = _bar_date(bars[-1])
    n = 0
    for b in reversed(bars):
        if _bar_date(b).month != last.month or _bar_date(b).year != last.year:
            break
        n += 1
    return n


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section: dict | None = None) -> Signal:
    if len(bars) < 2:
        return Signal(symbol, "hold", "insufficient history", strategy=NAME)
    if "ts" not in bars[-1]:
        # A calendar strategy without a calendar: bars may legally arrive
        # bare-OHLC (the reachability fixture does), and a date invented here
        # would be a signal invented here.
        return Signal(symbol, "hold", "bars carry no timestamps", strategy=NAME)
    today = _bar_date(bars[-1])
    hold_days = int(params.get("hold_days_into_month", 3))
    tdom = trading_day_of_month(bars)
    ind = {"date": today.isoformat(), "trading_day_of_month": tdom,
           "weekdays_remaining": weekdays_remaining_after(today)}
    if not holding and weekdays_remaining_after(today) == 1:
        return Signal(symbol, "buy",
                      "one weekday left in the month — entering the "
                      "turn-of-the-month window at tomorrow's open", ind, NAME)
    if holding and tdom >= hold_days:
        return Signal(symbol, "sell",
                      f"trading day {tdom} of the new month — the "
                      f"turn-of-the-month window closes at tomorrow's open",
                      ind, NAME)
    state = "in window" if holding else "outside window"
    return Signal(symbol, "hold", f"calendar says {state}", ind, NAME)
