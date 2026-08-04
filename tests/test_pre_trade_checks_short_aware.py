"""`pre_trade_checks` was never made short-aware (Phase 1 review, changes
requested).

`ENTRY_ACTIONS` was applied inside `pure_checks` when shorts were added, but
its caller `pre_trade_checks` still gated four rails on the literal string
`action == "buy"`: HALT, the daily trade cap, the portfolio heat cap and the
correlation cap. Demonstrated consequence — with HALT engaged, a "buy" was
refused and a "short" was allowed straight through. HALT is the operator kill
switch; the same gap applied to the daily cap and both heat caps.

A fifth gate had the mirror bug on the exit side: `swing_guard` (the minimum-
holding-period guard that makes day-trading structurally impossible) was
gated on `action == "sell"`, so a "cover" — closing a short — bypassed the
minimum holding period entirely.

Boundary pairs throughout, in the style of `tests/test_short_rails.py`: each
refusal sits beside a permitted twin differing in exactly the dimension the
rail measures.
"""
import json
import os

import pytest

import risk


def _acct(equity=100_000.0):
    return {"equity": equity, "buying_power": equity, "last_equity": equity}


def _cfg(**over):
    r = {"risk_per_trade_pct": 8.0, "max_position_pct": 100.0,
         "max_order_value_usd": 0, "max_open_positions": 0,
         "max_trades_per_day": 0, "max_drawdown_pct": 0,
         "max_portfolio_heat_pct": 0, "min_holding_days": 2,
         "daily_loss_limit_pct": 3.0}
    r.update(over)
    return {"risk": r}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate HALT / trade-count / high-water state from the live deployment
    — `pre_trade_checks` does real file I/O against relative paths."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    return tmp_path


# ------------------------------------------------------------------ HALT

def test_a_halt_now_blocks_a_short_the_same_as_a_buy(sandbox):
    """THE demonstrated bug: an engaged HALT refused a buy and let a short
    straight through, because the guard read `action == "buy"` instead of
    `action in ENTRY_ACTIONS`."""
    risk.engage_halt("operator stop", mode=risk.HALT_MODE_FREEZE)
    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, _cfg())
    assert ei.value.rail == "halt"


def test_the_same_short_is_permitted_with_no_halt_engaged(sandbox):
    """The paired half, varying only the dimension the rail measures: same
    short, no HALT file."""
    risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, _cfg())


# ------------------------------------------------------------- daily cap

def test_the_daily_cap_now_blocks_a_short_at_the_limit(sandbox):
    with open(risk._TRADECOUNT_FILE, "w") as f:
        json.dump({str(risk.date.today()): 5}, f)
    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {},
                              _cfg(max_trades_per_day=5))
    assert ei.value.rail == "daily_cap"


def test_the_same_short_is_permitted_below_the_daily_cap(sandbox):
    with open(risk._TRADECOUNT_FILE, "w") as f:
        json.dump({str(risk.date.today()): 4}, f)
    risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {},
                          _cfg(max_trades_per_day=5))


# ---------------------------------------------------------------- heat cap

def test_the_heat_cap_now_blocks_a_short_that_exhausts_the_budget(sandbox):
    """Mirrors `test_ONE_stopless_open_position_exhausts_the_whole_heat_budget`
    in tests/test_rail_interactions.py, but for a short."""
    cfg = _cfg(max_portfolio_heat_pct=4.0)
    stopless = {"AAA": {"qty": 1, "entry_price": 100.0, "order": {}}}
    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, cfg,
                              open_trades=stopless, candidate_stop=99.0)
    assert ei.value.rail == "heat"


def test_the_same_short_is_permitted_when_the_book_has_room(sandbox):
    cfg = _cfg(max_portfolio_heat_pct=4.0)
    bracketed = {"AAA": {"qty": 10, "entry_price": 100.0,
                         "order": {"stop_price": 99.0}}}
    risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, cfg,
                          open_trades=bracketed, candidate_stop=99.0)


# ----------------------------------------------------------- correlation cap

def _bars(symbol):
    return {symbol: [{"ts": f"2026-06-{d:02d}T21:00:00+00:00",
                      "close": 100.0 + d}
                     for d in range(1, 16)]}


def test_correlation_cap_now_blocks_a_short_at_zero_tolerance(sandbox):
    """`max_correlated: 0` means ANY count (including the vacuous zero from no
    open positions) already meets the threshold — isolates the gate from the
    correlation arithmetic itself."""
    cfg = _cfg(correlation_cap={"enabled": True, "lookback": 10,
                                "threshold": 0.5, "max_correlated": 0})
    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, cfg,
                              bars_map=_bars("ZZZ"))
    assert ei.value.rail == "correlation"


def test_the_same_short_is_permitted_with_the_correlation_cap_disabled(sandbox):
    cfg = _cfg(correlation_cap={"enabled": False, "lookback": 10,
                                "threshold": 0.5, "max_correlated": 0})
    risk.pre_trade_checks("short", "ZZZ", 10, 100.0, _acct(), {}, cfg,
                          bars_map=_bars("ZZZ"))


# --------------------------------------------------------------- swing guard

def test_a_cover_now_respects_the_minimum_holding_period(sandbox):
    """THE mirror bug: `swing_guard` was gated on `action == "sell"`, so a
    cover — closing a short — bypassed the minimum holding period that makes
    day-trading structurally impossible."""
    young_entry = risk.datetime.now(risk.timezone.utc).isoformat()  # 0 days old
    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("cover", "ZZZ", 10, 100.0, _acct(),
                              {"ZZZ": {"market_value": -1000.0}}, _cfg(),
                              entry_ts=young_entry)
    assert ei.value.rail == "swing_guard"


def test_the_same_cover_is_permitted_once_the_holding_period_has_passed(sandbox):
    old_entry = "2000-01-01T00:00:00+00:00"
    risk.pre_trade_checks("cover", "ZZZ", 10, 100.0, _acct(),
                          {"ZZZ": {"market_value": -1000.0}}, _cfg(),
                          entry_ts=old_entry)


def test_a_buy_still_ignores_the_swing_guard_entirely(sandbox):
    """Regression guard: swing_guard must stay EXIT-only. A buy on a young
    position (e.g. adding to an existing long) must never be blocked by it."""
    young_entry = risk.datetime.now(risk.timezone.utc).isoformat()
    risk.pre_trade_checks("buy", "ZZZ", 10, 100.0, _acct(), {}, _cfg(),
                          entry_ts=young_entry)
