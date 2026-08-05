from datetime import datetime, timezone

import pytest

import counterfactual as cf


DECISION = "2026-01-05T21:00:00+00:00"


def _bar(day, o, h, l, c):
    return {"ts": f"2026-01-{day:02d}T21:00:00+00:00", "open": o, "high": h,
            "low": l, "close": c, "volume": 1000}


def test_stop_fires_pessimistically_when_both_legs_touch():
    # backtest.simulate parity: stop wins when stop AND tp touch in one bar
    bars = [_bar(6, 100, 120, 80, 110)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "stop_loss"
    assert r["pnl_pct"] == pytest.approx(-10.0)


def test_take_profit_fires_when_stop_untouched():
    bars = [_bar(6, 100, 116, 95, 110)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "take_profit"
    assert r["pnl_pct"] == pytest.approx(15.0)


def test_horizon_close_when_no_leg_fires():
    bars = [_bar(6, 100, 104, 97, 103), _bar(7, 103, 105, 100, 104)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "horizon_close"
    assert r["pnl_pct"] == pytest.approx(4.0)


def test_bars_on_decision_day_excluded_and_beyond_horizon_ignored():
    bars = [_bar(5, 100, 200, 50, 100),   # decision day itself: must not count
            _bar(6, 100, 101, 99, 100),
            _bar(20, 100, 200, 50, 150)]  # beyond 7d horizon: ignored
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "horizon_close" and r["pnl_pct"] == 0.0


def test_no_bars_in_window_returns_none():
    assert cf.simulate_veto_counterfactual([], DECISION, 100.0, 90.0, 115.0, 7) is None


def test_no_brackets_exits_at_horizon_close():
    bars = [_bar(6, 100, 130, 70, 95)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=None,
                                        tp=None, horizon_days=7)
    assert r["exit_reason"] == "horizon_close" and r["pnl_pct"] == -5.0


def test_resolution_due_embargo():
    now_early = datetime(2026, 1, 10, tzinfo=timezone.utc)   # 5 days after
    now_due = datetime(2026, 1, 13, tzinfo=timezone.utc)     # 8 days after
    assert not cf.resolution_due(DECISION, 2, 5, now_early)  # needs 7
    assert cf.resolution_due(DECISION, 2, 5, now_due)


# ---------------------------------------------------------------------------
# THE SHORT SIDE — added 2026-08-05, when learn.py's `!= "buy"` filter was
# widened to ENTRY_ACTIONS.
#
# That filter was annotated LOAD-BEARING for a reason this section now makes
# mechanical: with the long geometry, a short's stop sits ABOVE entry, so the
# FIRST bar's low is already beneath it and every vetoed short resolved
# `stop_loss` on bar one — at a POSITIVE return, because the unsigned
# `(exit - entry)/entry` reads a stop above entry as a gain. Every short veto
# scored as a missed winner, silently, teaching the judge its short vetoes were
# always wrong.
#
# Every test below has its long-side twin above it or beside it. The twins are
# the load-bearing half: nothing shorts in production, so the LONG path is the
# one that must not have moved.
# ---------------------------------------------------------------------------

def test_a_short_stops_out_on_the_HIGH_not_the_low():
    """A short's stop is ABOVE entry, so the bar's HIGH is what reaches it.
    Under the long test (`low <= stop`) this same bar resolves on the low
    instead — which for a stop above entry is every bar, always."""
    bars = [_bar(6, 100, 112, 98, 105)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=110.0,
                                        tp=85.0, horizon_days=7,
                                        direction="short")
    assert r["exit_reason"] == "stop_loss"
    assert r["pnl_pct"] == pytest.approx(-10.0)   # NEGATIVE: the short lost


def test_the_same_bar_leaves_a_long_untouched_at_its_own_stop():
    """The twin: identical bar, long geometry, and the long's stop (below
    entry) is not reached — so the two directions genuinely read different
    sides of the bar rather than sharing one answer."""
    bars = [_bar(6, 100, 112, 98, 105)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["exit_reason"] == "horizon_close"


def test_a_short_takes_profit_on_the_LOW():
    bars = [_bar(6, 100, 104, 84, 90)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=110.0,
                                        tp=85.0, horizon_days=7,
                                        direction="short")
    assert r["exit_reason"] == "take_profit"
    assert r["pnl_pct"] == pytest.approx(15.0)    # POSITIVE: price fell


def test_a_short_that_never_touches_a_leg_signs_its_horizon_close():
    """The sign on its own, with no leg involved. Price ROSE 4%, so the short
    is down 4% — the exact inversion the unsigned formula got backwards."""
    bars = [_bar(6, 100, 104, 97, 103), _bar(7, 103, 105, 100, 104)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=110.0,
                                        tp=85.0, horizon_days=7,
                                        direction="short")
    assert r["pnl_pct"] == pytest.approx(-4.0)


def test_the_long_horizon_close_keeps_the_opposite_sign_on_the_same_bars():
    """The twin of the sign test, on identical bars."""
    bars = [_bar(6, 100, 104, 97, 103), _bar(7, 103, 105, 100, 104)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                        tp=115.0, horizon_days=7)
    assert r["pnl_pct"] == pytest.approx(4.0)


def test_a_shorts_stop_still_wins_when_both_legs_touch():
    """Pessimism must survive the inversion. A bar spanning both legs is read
    against the position on the short side too, not just the long."""
    bars = [_bar(6, 100, 120, 80, 100)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=110.0,
                                        tp=85.0, horizon_days=7,
                                        direction="short")
    assert r["exit_reason"] == "stop_loss"
    assert r["pnl_pct"] == pytest.approx(-10.0)


def test_an_omitted_direction_is_a_long():
    """The default is what keeps every caller predating the short leg
    unaffected — and `direction` is passed explicitly by learn.py precisely so
    that this default is never how a real short gets resolved."""
    bars = [_bar(6, 100, 120, 80, 110)]
    explicit = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                               tp=115.0, horizon_days=7,
                                               direction="buy")
    default = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=90.0,
                                              tp=115.0, horizon_days=7)
    assert explicit == default


def test_the_old_long_only_reading_of_a_short_is_no_longer_produced():
    """The regression this whole section exists for, stated as the concrete
    number it used to produce. Entry 100, stop 110, and a first bar whose low
    is 98: the old code fired `low <= stop` immediately and reported
    `_pct(110)` = +10.0 — a missed winner. It must now be a LOSS, and only
    because the high actually reached 110."""
    bars = [_bar(6, 100, 111, 98, 105)]
    r = cf.simulate_veto_counterfactual(bars, DECISION, 100.0, stop=110.0,
                                        tp=85.0, horizon_days=7,
                                        direction="short")
    assert r["pnl_pct"] == pytest.approx(-10.0)
    assert r["pnl_pct"] != pytest.approx(10.0)
