"""§41 — the drawdown breaker must be able to re-close.

§40's finding, quoted from knowledge/backtest_candidates.md:3761-3783:

    Equity peaks at P. A drawdown of >= 10% blocks every entry. Open positions
    exit normally. The book goes to cash. In cash, equity is FLAT — permanently
    ~0.9P. The peak stays P, so the drawdown stays >= 10%. The bot never buys
    again. For the rest of the run.

Across 2022-2026 that turned 245,213 buy signals into 29 trades, 99.43% of them
stopped by this single rail.

`risk.decayed_peak` is the fix's arithmetic: pure, shared by the live path and
the simulator, and INERT until `risk.drawdown_decay.enabled` is set. It ships
disabled, so every gate result recorded before §41 reproduces byte-identically.
Adoption is a separate, pre-registered decision — a rail is never loosened
because a diagnostic suggested it.
"""
import risk

DECAY = {"enabled": True, "grace_bars": 10, "halflife_bars": 20}


def _cfg(**over):
    d = dict(DECAY)
    d.update(over)
    return {"risk": {"drawdown_decay": d}}


def test_disabled_is_the_identity():
    """The shipped state. Every §1-§40 result must reproduce byte-identically,
    so with decay off this function must not be able to change anything."""
    off = {"risk": {"drawdown_decay": {"enabled": False}}}
    for bars in (0, 5, 50, 5_000):
        assert risk.decayed_peak(100_000.0, 90_000.0, bars, off) == 100_000.0
    assert risk.decayed_peak(100_000.0, 90_000.0, 5_000, {"risk": {}}) == 100_000.0
    assert risk.decayed_peak(100_000.0, 90_000.0, 5_000, {}) == 100_000.0


def test_the_grace_period_holds_the_peak_still():
    """A fresh drawdown must behave exactly as it does today. The rail is not
    being weakened at the moment it matters most."""
    for bars in range(0, 11):
        assert risk.decayed_peak(100_000.0, 90_000.0, bars, _cfg()) == 100_000.0


def test_the_gap_halves_every_halflife():
    """The stated semantics, checked as arithmetic rather than trusted."""
    peak, eq = 100_000.0, 90_000.0          # gap 10,000
    one = risk.decayed_peak(peak, eq, 10 + 20, _cfg())
    two = risk.decayed_peak(peak, eq, 10 + 40, _cfg())
    assert abs(one - 95_000.0) < 1e-6       # gap halved
    assert abs(two - 92_500.0) < 1e-6       # halved again


def test_the_latch_actually_releases():
    """The property §40 says is missing: a book sitting flat in cash below its
    peak eventually stops being blocked."""
    peak, eq, cap = 100_000.0, 90_000.0, 10.0
    assert risk.drawdown_pct(eq, peak) >= cap          # blocked today

    released = next(
        b for b in range(0, 400)
        if risk.drawdown_pct(eq, risk.decayed_peak(peak, eq, b, _cfg())) < cap)
    assert 10 < released <= 60, (
        f"released after {released} bars — outside the registered window")


def test_a_falling_book_never_earns_its_way_back_in():
    """THE BUG THIS FILE CAUGHT, 2026-07-28, before §41 was registered.

    The first implementation counted bars since the last HIGH. On a book that
    keeps falling that count keeps growing, so the gap keeps decaying and the
    rail releases DURING the decline — measured at the time: peak 100,000,
    equity down to 80,000, 40 bars after the high gives a decayed peak of
    87,071 and reports 8.12% drawdown, under the 10% cap. A 20% drawdown would
    have read as clear to trade.

    `decay_clock` counts from the last LOW instead, so every new low restarts
    the clock and a falling book stays blocked.
    """
    trough, bars = None, 0
    equity = 100_000.0
    for _ in range(40):                       # forty bars of steady decline
        equity -= 500.0
        trough, bars = risk.decay_clock(trough, bars, equity, 100_000.0)
        assert bars == 0, "a new low failed to restart the clock"

    peak = risk.decayed_peak(100_000.0, equity, bars, _cfg())
    assert peak == 100_000.0, "the peak decayed while the book was still falling"
    assert risk.drawdown_pct(equity, peak) >= 10.0, "released mid-decline"


def test_the_clock_runs_once_the_fall_stops():
    """The other half: a book that has stopped falling does accumulate credit."""
    trough, bars = None, 0
    for _ in range(60):                       # sixty flat bars at the low
        trough, bars = risk.decay_clock(trough, bars, 90_000.0, 100_000.0)
    assert bars == 59                          # first bar SET the trough
    assert risk.decayed_peak(100_000.0, 90_000.0, bars, _cfg()) < 100_000.0


def test_a_new_high_resets_everything():
    trough, bars = 80_000.0, 45
    assert risk.decay_clock(trough, bars, 100_000.0, 100_000.0) == (100_000.0, 0)


def test_equity_at_or_above_the_peak_is_untouched():
    """No drawdown, nothing to decay — and the peak must never be dragged
    BELOW equity, which would manufacture a drawdown from thin air."""
    assert risk.decayed_peak(100_000.0, 100_000.0, 999, _cfg()) == 100_000.0
    assert risk.decayed_peak(100_000.0, 110_000.0, 999, _cfg()) == 100_000.0


def test_the_decayed_peak_never_falls_below_equity():
    """The asymptote. Even at absurd horizons the gap only approaches zero."""
    for bars in (100, 1_000, 100_000):
        assert risk.decayed_peak(100_000.0, 90_000.0, bars, _cfg()) >= 90_000.0


def test_an_unknown_peak_stays_unknown():
    """Fail-closed is preserved: a corrupt high-water file reads as
    _PEAK_UNKNOWN and must not be decayed into a usable number."""
    unknown = risk._PEAK_UNKNOWN
    assert risk.decayed_peak(unknown, 90_000.0, 500, _cfg()) == unknown
    assert risk.drawdown_pct(90_000.0, unknown) == float("inf")


def test_a_zero_halflife_cannot_wipe_the_peak():
    """A misconfiguration must not silently disable the rail."""
    assert risk.decayed_peak(100_000.0, 90_000.0, 500, _cfg(halflife_bars=0)) == 100_000.0
    assert risk.decayed_peak(100_000.0, 90_000.0, 500, _cfg(halflife_bars=-5)) == 100_000.0


def test_an_identity_decay_would_fail_this_file():
    """Meta-assertion. If decayed_peak were stubbed to `return peak`, the
    release test above is the only thing standing between that and a green
    suite. Prove the enabled path actually moves the number."""
    moved = risk.decayed_peak(100_000.0, 90_000.0, 60, _cfg())
    assert moved < 100_000.0, (
        "decayed_peak returned the peak unchanged with decay ENABLED — the "
        "function is inert and every test in this file is vacuous")
