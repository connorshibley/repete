"""Short-side rail properties. Phase 1 ships no shorts — these pin the rails
BEFORE anything can emit one.

The failure mode being prevented is silent. Alpaca reports a short position's
`market_value` as NEGATIVE, so a rail that sums raw values nets a long against a
short: a book carrying 100% gross reads as 0% and the cap simply stops binding.
Nothing logs, nothing raises, and the rail appears to be working.

Boundary pairs throughout, in the style of tests/test_credit_gate.py: each
blocked case sits beside a permitted one differing in exactly the thing the rail
measures.
"""
import pytest

import risk

ACCOUNT = {"equity": 100_000.0}


def _cfg(**overrides):
    base = {"max_position_pct": 100.0, "max_open_positions": 0}
    base.update(overrides)
    return {"risk": base}


# ---- gross exposure counts magnitude, not sign ----

def test_a_short_adds_to_gross_exposure_rather_than_cancelling_a_long():
    """$50k long + $50k short is a 100% gross book, not a 0% one."""
    cfg = _cfg(regime_exposure={"enabled": True, "down_max_gross_pct": 50})
    positions = {"AAPL": {"market_value": 50_000.0},
                 "TSLA": {"market_value": -50_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "MSFT", 1, 1.0, ACCOUNT, positions, cfg,
                         regime_label="down_trend")
    assert e.value.rail == "regime_exposure"


def test_the_same_book_under_the_cap_is_permitted():
    """The paired half, varying only the dimension the rail measures: same
    long + short book as above, sized so gross ($40k) stays under the $50k
    cap. A rail that blocked everything would also pass the test above, and
    would be just as broken."""
    cfg = _cfg(regime_exposure={"enabled": True, "down_max_gross_pct": 50})
    positions = {"AAPL": {"market_value": 20_000.0},
                 "TSLA": {"market_value": -20_000.0}}
    risk.pure_checks("buy", "MSFT", 1, 1.0, ACCOUNT, positions, cfg,
                     regime_label="down_trend")


def test_the_concentration_cap_measures_a_short_by_magnitude():
    """Adding to an existing SHORT in the same name must count against the
    per-name cap. Raw addition would read -$9,000 + $2,000 as tiny."""
    cfg = _cfg(max_position_pct=10.0)
    positions = {"TSLA": {"market_value": -9_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "TSLA", 20, 100.0, ACCOUNT, positions, cfg)
    assert e.value.rail == "position_cap"


def test_the_short_under_the_cap_is_permitted():
    """The paired half: the same short, sized so $9,000 + $500 stays under
    the $10,000 cap. Proves the rail measures magnitude without over-firing
    on a short that is legitimately within bounds."""
    cfg = _cfg(max_position_pct=10.0)
    positions = {"TSLA": {"market_value": -9_000.0}}
    risk.pure_checks("buy", "TSLA", 5, 100.0, ACCOUNT, positions, cfg)


# ---- shorts are ENTRIES; covers are EXITS ----

def test_a_short_passes_through_the_entry_rails():
    """A short is an entry and must meet every entry rail. Gating the entry
    block on `action == "buy"` would let shorts bypass the concentration cap,
    the drawdown breaker and the slot limits entirely."""
    cfg = _cfg(max_position_pct=10.0)
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 200, 100.0, ACCOUNT, {}, cfg)
    assert e.value.rail == "position_cap"


def test_a_new_short_under_the_cap_is_permitted():
    """The paired half: a short opening a brand-new position, sized so its
    $5,000 order value stays under the $10,000 cap on $100k equity. Proves
    the entry gate subjects a short to the concentration rail rather than
    blocking every short outright."""
    cfg = _cfg(max_position_pct=10.0)
    risk.pure_checks("short", "TSLA", 50, 100.0, ACCOUNT, {}, cfg)


def test_a_cover_with_no_position_is_a_desync():
    """The mirror of the sell guard. Covering something you are not short is a
    state desync, and sending it would OPEN a long."""
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, {}, _cfg())
    assert e.value.rail == "desync_cover"


def test_a_cover_against_a_position_held_long_is_also_a_desync():
    """Presence alone is not enough: TSLA IS in `positions` here, but held
    LONG. `symbol not in positions` would pass this straight through, and the
    cover would submit a BUY that ADDS TO the long — the guard has to check
    the SIGN of the existing position, not merely whether one is on file."""
    positions = {"TSLA": {"market_value": 5_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())
    assert e.value.rail == "desync_cover"


def test_a_cover_against_a_genuine_short_is_allowed():
    """The paired half — proves the guard passes a REAL short rather than
    merely 'symbol is present in positions', which the long case above would
    also have satisfied under a presence-only check."""
    positions = {"TSLA": {"market_value": -5_000.0}}
    risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())


def test_a_sell_against_a_position_held_short_is_a_desync():
    """The mirror defect on the sell side: TSLA IS in `positions` here, but
    held SHORT. `symbol not in positions` would pass this straight through,
    and the sell would submit a SELL that ADDS TO the short."""
    positions = {"TSLA": {"market_value": -5_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("sell", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())
    assert e.value.rail == "desync_sell"


# ---- the net-exposure band ----

BAND = {"net_exposure_pct": {"min": 80, "max": 120}}


def test_a_short_that_would_push_net_below_the_floor_is_refused():
    positions = {"AAPL": {"market_value": 85_000.0}}      # net 85%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 100, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # -10% -> net 75%
    assert e.value.rail == "net_exposure"


def test_a_smaller_short_inside_the_band_is_allowed():
    """The paired half: same book, same direction, smaller size."""
    positions = {"AAPL": {"market_value": 85_000.0}}
    risk.pure_checks("short", "TSLA", 10, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))                         # -1% -> net 84%


def test_a_buy_that_would_push_net_above_the_ceiling_is_refused():
    positions = {"AAPL": {"market_value": 118_000.0}}      # net 118%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "MSFT", 50, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # +5% -> net 123%
    assert e.value.rail == "net_exposure"


def test_a_smaller_buy_inside_the_band_is_allowed():
    """The paired half missing from the ceiling case above: same book, same
    direction, smaller size. Without this, the ceiling test could be tripping
    on any threshold at all and nothing would tell you the rail also lets a
    compliant buy through."""
    positions = {"AAPL": {"market_value": 118_000.0}}
    risk.pure_checks("buy", "MSFT", 10, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))                         # +1% -> net 119%


def test_a_buy_is_never_blocked_by_the_FLOOR():
    """THE LOAD-BEARING CASE. §48 measured deployment at 4.72%. If the floor
    applied to buys, a bot below the band could never trade its way back in —
    the rail would be an outage, and it would look like a working rail."""
    risk.pure_checks("buy", "MSFT", 1, 100.0, ACCOUNT, {}, _cfg(**BAND))


def test_a_cover_is_never_blocked_by_the_band():
    """Exits always run."""
    positions = {"TSLA": {"market_value": -50_000.0}}
    risk.pure_checks("cover", "TSLA", 100, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))


def test_the_band_is_off_when_unconfigured():
    """Absent config must behave exactly as before, or this rail silently
    changes every existing gate result."""
    positions = {"AAPL": {"market_value": 500_000.0}}
    risk.pure_checks("buy", "MSFT", 1, 100.0, ACCOUNT, positions, _cfg())
