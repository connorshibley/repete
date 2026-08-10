"""§56. The ETF-universe probe, and above all: can it REFUSE?

A probe that only ever passes is a rubber stamp, and this one certifies the
directory that EDGE claims are allowed to live in — so the tests that matter are
the ones driving it to REFUSED.

`test_a_negative_control_that_looks_healthy_refuses_even_on_a_perfect_universe`
is the decisive one. `assess()` is applied to the candidate funds AND to symbols
already measured dead, and the whole design rests on one predicate serving both.
If that predicate is ever weakened enough to call a resurrected ticker healthy,
the fifteen funds tell us nothing — and this file must go red before the probe
gets a chance to say PASS.

Offline throughout: `probe()` takes the fetcher, the pattern
`tests/test_edge_freeze.py` established.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import probe_etf_universe_survivorship as probe          # noqa: E402

END = "2026-07-10"


def _series(start: str, end: str, step: int = 1) -> list:
    """[(iso_date, close)] at `step`-day spacing. Calendar days, not trading
    days — the gap check measures calendar distance, so weekends would only
    make the fixtures harder to read without testing anything extra."""
    d, stop, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= stop:
        out.append((d.isoformat(), 100.0))
        d += timedelta(days=step)
    return out


def _healthy_world(**override) -> dict:
    """Every candidate fund continuous from its declared inception, every
    known-dead symbol absent. The PASS baseline every other test perturbs."""
    world = {probe.CONTROL: _series("2020-01-01", END)}
    for t, inception, _ in probe.UNIVERSE:
        world[t] = _series(inception, END)
    for t, *_ in probe.CLOSED:
        world[t] = []
    world.update(override)
    return world


def _fetch(world: dict):
    return lambda ticker: world.get(ticker, [])


def _run(world: dict) -> dict:
    return probe.probe(_fetch(world), end=END)


# ---- the baseline -----------------------------------------------------------

def test_a_clean_universe_passes():
    """'A probe that could never pass would just be a comment' — §52's own test
    file makes the same point about itself."""
    res = _run(_healthy_world())
    assert res["check1_continuous_from_inception"] is True
    assert res["check2_detects_the_dead"] is True
    assert res["verdict"] == "PASS"
    assert probe.report(res) == 0


# ---- THE decisive test ------------------------------------------------------

def test_a_negative_control_that_looks_healthy_refuses_even_on_a_perfect_universe():
    """If the predicate cannot see death, the fifteen prove nothing.

    SBNY is given exactly the shape a resurrected ticker has: continuous,
    current, plausible — but starting long after the company died. Here it is
    handed a history running from its ORIGINAL listing, i.e. the case where
    `assess()` has been weakened until it can no longer tell the difference.
    The universe is untouched and perfect; the verdict must still be REFUSED.
    """
    world = _healthy_world(SBNY=_series("2004-03-23", END))
    res = _run(world)
    assert res["check1_continuous_from_inception"] is True   # universe fine
    assert res["check2_detects_the_dead"] is False           # and yet
    assert res["verdict"] == "REFUSED"
    assert probe.report(res) == 1


def test_the_real_sbny_shape_is_detected():
    """The measured case, not a hypothetical: 475 bars all starting
    2024-08-15, seventeen months after the bank was seized."""
    world = _healthy_world(SBNY=_series("2024-08-15", END))
    rec = _run(world)["closed"][0]
    assert rec["ticker"] == "SBNY"
    assert rec["healthy"] is False
    assert "after the declared inception" in rec["why"]


# ---- the ways a candidate fund can fail check 1 -----------------------------

def test_a_fund_whose_history_starts_late_is_not_healthy():
    late = date.fromisoformat("1998-12-22") + timedelta(
        days=probe.INCEPTION_TOLERANCE_DAYS + 1)
    res = _run(_healthy_world(XLK=_series(late.isoformat(), END)))
    assert res["check1_continuous_from_inception"] is False
    assert res["verdict"] == "REFUSED"
    bad = [t for t in res["universe"] if t["ticker"] == "XLK"][0]
    assert "days after the declared inception" in bad["why"]


def test_a_fund_that_stopped_trading_is_not_healthy():
    res = _run(_healthy_world(XLE=_series("1998-12-22", "2020-01-01")))
    assert res["check1_continuous_from_inception"] is False
    bad = [t for t in res["universe"] if t["ticker"] == "XLE"][0]
    assert "it stopped trading" in bad["why"]


def test_a_fund_with_a_hole_longer_than_any_market_closure_is_not_healthy():
    """A suspension-and-relist leaves a hole. The longest legitimate US closure
    on record is four trading days (September 2001), so anything past 45 days
    is not the market being shut."""
    head = _series("1998-12-22", "2010-01-01")
    tail = _series("2010-06-01", END)          # a five-month hole
    res = _run(_healthy_world(XLF=head + tail))
    assert res["check1_continuous_from_inception"] is False
    bad = [t for t in res["universe"] if t["ticker"] == "XLF"][0]
    assert "hole after" in bad["why"] and bad["gap_days"] > probe.MAX_GAP_DAYS


def test_a_fund_with_no_history_is_not_healthy():
    res = _run(_healthy_world(XLRE=[]))
    assert res["check1_continuous_from_inception"] is False
    bad = [t for t in res["universe"] if t["ticker"] == "XLRE"][0]
    assert bad["why"] == "no history at all"


# ---- boundaries -------------------------------------------------------------

@pytest.mark.parametrize("extra,healthy", [
    (probe.INCEPTION_TOLERANCE_DAYS, True),
    (probe.INCEPTION_TOLERANCE_DAYS + 1, False),
])
def test_the_inception_tolerance_boundary(extra, healthy):
    """Tested on the boundary on purpose: an off-by-one here silently changes
    what 'starts at inception' means for every future vendor."""
    start = date.fromisoformat("2015-10-08") + timedelta(days=extra)
    rec = probe.assess("XLRE", "2015-10-08",
                       _series(start.isoformat(), END), END)
    assert rec["healthy"] is healthy


@pytest.mark.parametrize("gap,healthy", [
    (probe.MAX_GAP_DAYS, True),
    (probe.MAX_GAP_DAYS + 1, False),
])
def test_the_gap_boundary(gap, healthy):
    head = _series("2018-06-19", "2020-01-01")
    resume = date.fromisoformat("2020-01-01") + timedelta(days=gap)
    rec = probe.assess("XLC", "2018-06-19",
                       head + _series(resume.isoformat(), END), END)
    assert rec["healthy"] is healthy


def test_largest_gap_finds_the_biggest_hole_not_the_first():
    dates = ["2020-01-01", "2020-01-05", "2020-03-01", "2020-03-03"]
    days, after = probe.largest_gap(dates)
    assert (days, after) == (56, "2020-01-05")


def test_largest_gap_on_a_single_bar_is_zero():
    assert probe.largest_gap(["2020-01-01"]) == (0, None)


# ---- the live control -------------------------------------------------------

def test_a_dead_control_is_undetermined_not_refused():
    """'We could not measure' and 'it did not pass' are different facts — the
    §33 RUN 1 rule. Both checks must stay None, so nothing downstream can read
    an unmeasured question as an answered one."""
    res = _run(_healthy_world(**{probe.CONTROL: []}))
    assert res["verdict"] == "UNDETERMINED"
    assert res["check1_continuous_from_inception"] is None
    assert res["check2_detects_the_dead"] is None
    assert res["universe"] == [] and res["closed"] == []
    assert probe.report(res) == 2


def test_the_control_is_not_also_a_subject():
    """A control that is also being judged cannot referee itself."""
    assert probe.CONTROL not in {t for t, *_ in probe.UNIVERSE}
    assert probe.CONTROL not in {t for t, *_ in probe.CLOSED}


# ---- the declared enumeration ----------------------------------------------

def test_the_universe_is_well_formed_and_carries_its_sources():
    assert len(probe.UNIVERSE) == 15
    for ticker, inception, what in probe.UNIVERSE:
        assert ticker.isupper() and 2 <= len(ticker) <= 5
        date.fromisoformat(inception)          # raises if malformed
        assert len(what) > 10, f"{ticker} has no description"
    assert len({t for t, *_ in probe.UNIVERSE}) == 15, "duplicate ticker"


def test_spy_is_in_the_universe():
    """Not cosmetic. `simulate_ensemble` reads `sym_bars.get("SPY", [])` for the
    regime label and does NOT raise when it is missing — `regime_exposure` just
    goes dark and reports zero blocks. A snapshot without SPY would silently
    score a bot with no down-regime cap."""
    assert "SPY" in {t for t, *_ in probe.UNIVERSE}


def test_every_negative_control_names_when_it_died():
    for ticker, listed, died, what in probe.CLOSED:
        date.fromisoformat(listed)
        date.fromisoformat(died)
        assert died > listed, f"{ticker} died before it listed"
        assert len(what) > 10


def test_the_probe_says_out_loud_what_it_did_not_check(capsys):
    """The enumeration is an assumption. A reader must not be able to skim the
    PASS and come away thinking completeness was measured."""
    probe.report(_run(_healthy_world()))
    out = capsys.readouterr().out
    assert "NOT CHECKED" in out
    assert "COMPLETE" in out and "DECLARED ASSUMPTION" in out
