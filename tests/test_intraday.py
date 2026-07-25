"""Intraday fill timing (§25) — src/intraday.py and the simulator wiring.

Two properties carry the whole experiment:

1. **Equivalence.** `fill_hour=None` must reproduce today's ensemble results
   EXACTLY. Same argument that made `simulate_ensemble` trustworthy in §20 — if
   the inert path drifts, every §25 number is suspect.
2. **No silent substitution.** A missing hourly bar must fall back to the daily
   open AND be counted. Thin coverage quietly masquerading as a result is the
   failure mode that would make §25 look better than it is.
"""
from datetime import date, timedelta

import pytest

import backtest as bt
import intraday


def hbars(day: str, hours, price_at=lambda h: 100.0 + h):
    """Hourly bars for one day at the given ET hours (UTC-stamped)."""
    out = []
    for h in hours:
        # ET is UTC-4 in summer; 2025-06-10 is EDT.
        utc_h = h + 4
        px = price_at(h)
        out.append({"ts": f"{day}T{utc_h:02d}:00:00+00:00", "open": px,
                    "high": px + 1, "low": px - 1, "close": px + 0.5,
                    "volume": 1_000_000})
    return out


# ---------------- RTH boundaries ----------------

def test_regular_session_hours_are_9_to_15():
    assert intraday.RTH_FIRST_HOUR == 9
    assert intraday.RTH_LAST_HOUR == 15
    assert intraday.VALID_FILL_HOURS == (9, 10, 11, 12, 13, 14, 15)


def test_extended_hours_bars_are_rejected():
    """Filling pre/post-market at regular-session slippage would be fantasy —
    those sessions are thin and spreads far wider."""
    rows = hbars("2025-06-10", [4, 7, 8, 9, 15, 16, 19])
    kept = [b for b in rows if intraday.is_rth(b["ts"])]
    assert [intraday.et_hour(b["ts"]) for b in kept] == [9, 15]


def test_fill_price_refuses_hours_outside_rth():
    idx = intraday.index_hours({"AAA": hbars("2025-06-10", [8, 9, 16])})
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 8) is None
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 16) is None
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 9) is not None


# ---------------- lookup ----------------

def test_fill_price_returns_that_hours_open():
    idx = intraday.index_hours({"AAA": hbars("2025-06-10", [9, 12, 15])})
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 12) == 112.0


def test_missing_hour_symbol_or_date_returns_none():
    """None, never a substituted price — the caller must decide and count."""
    idx = intraday.index_hours({"AAA": hbars("2025-06-10", [9, 10])})
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 14) is None   # half-day
    assert intraday.fill_price(idx, "AAA", "2025-06-11", 9) is None    # holiday
    assert intraday.fill_price(idx, "ZZZ", "2025-06-10", 9) is None    # no cover


def test_non_positive_price_is_treated_as_missing():
    rows = hbars("2025-06-10", [9])
    rows[0]["open"] = 0.0
    idx = intraday.index_hours({"AAA": rows})
    assert intraday.fill_price(idx, "AAA", "2025-06-10", 9) is None


# ---------------- resampling ----------------

def test_resample_builds_a_daily_bar_from_the_session():
    rows = hbars("2025-06-10", [9, 10, 11, 12, 13, 14, 15])
    d = intraday.resample_daily(rows)
    assert len(d) == 1
    bar = d[0]
    assert bar["open"] == 109.0                      # first RTH bar's open
    assert bar["close"] == pytest.approx(115.5)      # last RTH bar's close
    assert bar["high"] == 116.0 and bar["low"] == 108.0
    assert bar["volume"] == 7_000_000


def test_resample_excludes_extended_hours():
    """Pre/post-market must not set the daily open, high, low or close."""
    rows = hbars("2025-06-10", [4, 9, 10, 19])
    bar = intraday.resample_daily(rows)[0]
    assert bar["open"] == 109.0                      # not the 04:00 bar
    assert bar["high"] <= 111.0 and bar["low"] >= 108.0


def test_resample_orders_days_and_handles_multiple():
    rows = hbars("2025-06-11", [9, 10]) + hbars("2025-06-10", [9, 10])
    d = intraday.resample_daily(rows)
    assert [b["ts"][:10] for b in d] == ["2025-06-10", "2025-06-11"]


def test_resample_all_drops_symbols_with_no_session_data():
    # 4 and 8 ET are pre-market; hbars shifts +4 for EDT, so both stay valid
    # UTC hours while sitting outside the regular session.
    out = intraday.resample_all({"AAA": hbars("2025-06-10", [9]),
                                 "PREMKT": hbars("2025-06-10", [4, 8])})
    assert set(out) == {"AAA"}


# ---------------- THE equivalence check ----------------

def synth(symbols, n=320, seed=11):
    out = {}
    for k, sym in enumerate(symbols):
        px, rows, d, st = 100.0 + 10 * k, [], date(2024, 1, 1), seed + k
        for _ in range(n):
            st = (st * 1103515245 + 12345) % (2 ** 31)
            px = max(5.0, px + ((st % 1000) / 1000.0 - 0.48) * 2.2)
            rows.append({"ts": f"{d.isoformat()}T21:00:00+00:00",
                         "open": round(px * 0.998, 4), "high": round(px * 1.02, 4),
                         "low": round(px * 0.98, 4), "close": round(px, 4),
                         "volume": 1_000_000})
            d += timedelta(days=1)
        out[sym] = rows
    return out


def test_fill_hour_none_reproduces_the_baseline_exactly():
    """THE load-bearing test. The §25 path must be perfectly inert when unused,
    or every §25 result is measuring the plumbing rather than the timing."""
    cfg = bt.load_config()
    picks = [s for s in cfg["symbols"] if s in ("SPY", "AAPL", "MSFT", "XOM")]
    bars = synth(picks)

    base = bt.simulate_ensemble(bars, cfg, 100_000.0)
    with_none = bt.simulate_ensemble(bars, cfg, 100_000.0,
                                     hour_index={}, fill_hour=None)

    for f in ("total_return_pct", "n_trades", "profit_factor",
              "max_drawdown_pct", "win_rate", "avg_deployment_pct"):
        assert getattr(base, f) == getattr(with_none, f), f"{f} drifted"
    assert ([(t.symbol, t.entry_ts, round(t.pnl, 6)) for t in base.trades]
            == [(t.symbol, t.entry_ts, round(t.pnl, 6)) for t in with_none.trades])
    assert base.n_fill_fallback == 0


def test_every_fill_falls_back_and_is_counted_when_no_intraday_data():
    """With a fill_hour set but no coverage at all, results must equal the
    baseline AND the fallback counter must be non-zero — proving the fallback
    is visible rather than silent."""
    cfg = bt.load_config()
    picks = [s for s in cfg["symbols"] if s in ("SPY", "AAPL", "MSFT", "XOM")]
    bars = synth(picks)

    base = bt.simulate_ensemble(bars, cfg, 100_000.0)
    fell_back = bt.simulate_ensemble(bars, cfg, 100_000.0,
                                     hour_index={}, fill_hour=12)

    assert fell_back.total_return_pct == base.total_return_pct
    assert fell_back.n_fill_fallback > 0, "fallback happened but was not counted"


def test_baseline_reports_zero_fallbacks():
    cfg = bt.load_config()
    picks = [s for s in cfg["symbols"] if s in ("SPY", "AAPL")]
    r = bt.simulate_ensemble(synth(picks), cfg, 100_000.0)
    assert r.n_fill_fallback == 0
