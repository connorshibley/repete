"""Prove each rail is switched on, not merely un-triggered.

Why this file exists
--------------------
An audit on 2026-07-27 replaced the entire risk engine with

    def pure_checks(...):        return None
    def pre_trade_checks(...):   return None
    def swing_guard(...):        return None

and re-ran the safety suite. **Thirteen tests still passed**, including
`test_EXITS_ARE_NEVER_BLOCKED`, whose own docstring calls it "the one that
matters".

They were not wrong, exactly. Each asserts that a PERMISSIVE case does not
raise — the rail must not over-fire, block a sell during a drawdown, or refuse
an entry when its cap is disabled. Those are real properties. But "does not
raise" is trivially satisfied when nothing can raise, so a test shaped that way
cannot tell "the rail correctly allowed this" apart from "there is no rail".

The missing half is the boundary. For every "allowed" case there is a nearby
"blocked" case differing in ONE variable, and asserting both is what pins the
rail's existence and its threshold at the same time. That is what this file
does — deliberately as a separate file, so the originals keep documenting the
permissive intent in their own words while these carry the proof.

Read `_boundary` as: *this call is allowed, this neighbouring call is not, and
the only difference between them is the thing the rail measures.*
"""
import pytest

import risk


def _acct(equity):
    return {"equity": equity, "buying_power": equity, "last_equity": equity}


def _cfg(**over):
    r = {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
         "max_order_value_usd": 0, "max_open_positions": 0,
         "max_trades_per_day": 15, "max_drawdown_pct": 10.0,
         "min_holding_days": 2}
    r.update(over)
    return {"risk": r}


def _boundary(allowed, blocked, match):
    """`allowed` must pass; `blocked` must raise. Both are zero-arg callables
    differing in exactly one input."""
    allowed()                       # no rejection on the permissive side
    with pytest.raises(risk.RiskRejection, match=match):
        blocked()


# ---- drawdown circuit breaker ----

def test_drawdown_rail_is_live_at_its_threshold():
    """Pairs with test_entries_allowed_just_inside_the_limit, which passes
    with the breaker deleted."""
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(90_500), {}, _cfg(),
                                         peak_equity=100_000),
        blocked=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(89_500), {}, _cfg(),
                                         peak_equity=100_000),
        match="drawdown circuit breaker")


def test_exits_are_exempt_while_entries_at_the_same_equity_are_not():
    """The exit exemption, stated as a contrast rather than an absence.

    A single sell that does not raise proves nothing. A sell that passes at an
    equity where a buy is refused proves the exemption is real and deliberate.
    """
    deep = _acct(50_000)
    pos = {"AAA": {"qty": 10, "market_value": 1000}}
    _boundary(
        allowed=lambda: risk.pure_checks("sell", "AAA", 10, 100.0, deep, pos,
                                         _cfg(), peak_equity=100_000),
        blocked=lambda: risk.pure_checks("buy", "AAA", 10, 100.0, deep, {},
                                         _cfg(), peak_equity=100_000),
        match="drawdown circuit breaker")


def test_zero_disables_the_breaker_and_nonzero_re_enables_it():
    """Pairs with test_zero_disables_the_breaker."""
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(10_000), {}, _cfg(max_drawdown_pct=0),
                                         peak_equity=100_000),
        blocked=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(10_000), {}, _cfg(max_drawdown_pct=10.0),
                                         peak_equity=100_000),
        match="drawdown circuit breaker")


def test_absent_peak_opts_out_but_a_present_peak_binds():
    """Pairs with test_absent_peak_does_not_block."""
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(50_000), {}, _cfg(),
                                         peak_equity=None),
        blocked=lambda: risk.pure_checks("buy", "AAA", 10, 100.0,
                                         _acct(50_000), {}, _cfg(),
                                         peak_equity=100_000),
        match="drawdown circuit breaker")


# ---- position count cap ----

def test_position_count_cap_is_live_when_set():
    """Pairs with test_zero_open_positions_cap_lets_the_book_grow. §29 set the
    cap to 0 (disabled); the rail must still exist for anyone who sets it."""
    book = {f"S{i}": {"qty": 1, "market_value": 100} for i in range(6)}
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "NEW", 1, 100.0,
                                         _acct(100_000), book,
                                         _cfg(max_open_positions=0)),
        blocked=lambda: risk.pure_checks("buy", "NEW", 1, 100.0,
                                         _acct(100_000), book,
                                         _cfg(max_open_positions=5)),
        match="max open positions")


def test_adding_to_a_held_name_is_exempt_but_a_new_name_is_not():
    """Pairs with test_adding_to_an_existing_position_is_never_blocked_by_the
    _count — the exemption only means something if the cap otherwise binds."""
    book = {f"S{i}": {"qty": 1, "market_value": 100} for i in range(5)}
    cfg = _cfg(max_open_positions=5)
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "S0", 1, 100.0,
                                         _acct(100_000), book, cfg),
        blocked=lambda: risk.pure_checks("buy", "NEW", 1, 100.0,
                                         _acct(100_000), book, cfg),
        match="max open positions")


# ---- per-order value cap ----

def test_order_value_cap_is_live_when_set():
    """Pairs with test_zero_order_cap_allows_orders_above_the_old_2000_clamp.
    The $2000 clamp was removed in §29 — this proves removing it was a config
    decision, not a code deletion."""
    _boundary(
        allowed=lambda: risk.pure_checks("buy", "AAA", 50, 100.0,
                                         _acct(100_000), {},
                                         _cfg(max_order_value_usd=0)),
        blocked=lambda: risk.pure_checks("buy", "AAA", 50, 100.0,
                                         _acct(100_000), {},
                                         _cfg(max_order_value_usd=2000)),
        match="order value")


# ---- swing guard ----

def test_swing_guard_is_live_and_measures_age():
    """Pairs with test_swing_guard_allows_old_position. Invariant #3 — the
    rail that makes this a swing bot rather than a day-trading bot."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=9)).isoformat()
    young = (now - timedelta(hours=3)).isoformat()
    _boundary(
        allowed=lambda: risk.swing_guard(old, _cfg()),
        blocked=lambda: risk.swing_guard(young, _cfg()),
        match="swing guard")


def test_swing_guard_opts_out_without_an_entry_time_but_binds_with_one():
    """Pairs with test_swing_guard_passes_without_entry_ts."""
    from datetime import datetime, timedelta, timezone
    young = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _boundary(
        allowed=lambda: risk.swing_guard(None, _cfg()),
        blocked=lambda: risk.swing_guard(young, _cfg()),
        match="swing guard")


def test_min_holding_days_zero_disables_it_and_nonzero_restores_it():
    """Pairs with test_swing_guard_disabled_when_min_days_zero.

    Note preflight refuses min_holding_days: 0 in the shipped config — this
    covers the code path, not a supported configuration.
    """
    from datetime import datetime, timedelta, timezone
    young = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _boundary(
        allowed=lambda: risk.swing_guard(young, _cfg(min_holding_days=0)),
        blocked=lambda: risk.swing_guard(young, _cfg(min_holding_days=2)),
        match="swing guard")


# ---- the meta-guard ----

def test_a_gutted_rail_engine_would_fail_this_file():
    """Stated as an executable claim rather than a comment.

    Every test above calls a real rail and requires a real rejection, so
    replacing the engine with `return None` cannot leave this file green. That
    is the property the thirteen tests it complements did not have.
    """
    with pytest.raises(risk.RiskRejection):
        risk.pure_checks("buy", "AAA", 10, 100.0, _acct(50_000), {}, _cfg(),
                         peak_equity=100_000)
