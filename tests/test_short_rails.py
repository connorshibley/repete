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
