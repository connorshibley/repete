"""§29 (2026-07-26): the count caps became 0-disableable and the $2k order
clamp came off.

The risk in a change like this is not that the disabled path fails — it is that
disabling one rail silently disables others that were hiding behind it, or that
the backtester and the live bot stop agreeing about what 0 means. Both are the
mechanism behind divergences #1-#8, so both are tested explicitly.
"""
import pytest

import risk


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _cfg():
    return {"risk": {
        "risk_per_trade_pct": 8.0,
        "max_position_pct": 10.0,
        "max_order_value_usd": 0,       # §29: disabled
        "max_open_positions": 0,        # §29: disabled
        "max_trades_per_day": 15,
        "daily_loss_limit_pct": 3.0,
    }}


def _account(equity=100_000.0, bp=None):
    return {"equity": equity, "buying_power": bp if bp is not None else equity}


def _positions(n, value=5_000.0):
    return {f"S{i}": {"market_value": value, "qty": 10} for i in range(n)}


# ---- max_open_positions: 0 disables ----

def test_zero_open_positions_cap_lets_the_book_grow():
    cfg = _cfg()
    # 40 open positions would have been refused at any previous setting
    risk.pure_checks("buy", "NEW", 10, 100.0, _account(),
                     _positions(40), cfg)


def test_a_nonzero_open_positions_cap_still_binds():
    """0 must mean 'disabled', NOT 'the check is gone'."""
    cfg = _cfg()
    cfg["risk"]["max_open_positions"] = 8
    with pytest.raises(risk.RiskRejection, match="max open positions"):
        risk.pure_checks("buy", "NEW", 10, 100.0, _account(),
                         _positions(8), cfg)


def test_adding_to_an_existing_position_is_never_blocked_by_the_count():
    cfg = _cfg()
    cfg["risk"]["max_open_positions"] = 2
    pos = _positions(2)
    risk.pure_checks("buy", "S0", 1, 100.0, _account(), pos, cfg)


# ---- max_order_value_usd: 0 disables ----

def test_zero_order_cap_allows_orders_above_the_old_2000_clamp():
    cfg = _cfg()
    risk.pure_checks("buy", "AAA", 50, 100.0, _account(), {}, cfg)  # $5,000


def test_a_nonzero_order_cap_still_binds():
    cfg = _cfg()
    cfg["risk"]["max_order_value_usd"] = 2000
    with pytest.raises(risk.RiskRejection, match="exceeds cap"):
        risk.pure_checks("buy", "AAA", 50, 100.0, _account(), {}, cfg)


def test_concentration_cap_survives_the_clamp_removal():
    """max_position_pct is now the ONLY per-name rail. If disabling the order
    clamp had also slipped this, a single name could take the whole account."""
    cfg = _cfg()
    with pytest.raises(risk.RiskRejection, match="concentration cap"):
        # $11,000 on $100k equity vs a 10% cap
        risk.pure_checks("buy", "AAA", 110, 100.0, _account(), {}, cfg)


def test_sizing_no_longer_clamps_at_2000_but_still_respects_concentration():
    cfg = _cfg()
    # 8% of 100k = $8,000 target; 10% concentration ceiling = $10,000
    assert risk.size_order(_account(), 100.0, cfg) == 80
    cfg["risk"]["risk_per_trade_pct"] = 50.0      # $50k target
    assert risk.size_order(_account(), 100.0, cfg) == 100   # clipped to $10k


def test_sizing_still_respects_buying_power():
    cfg = _cfg()
    assert risk.size_order(_account(bp=1_000.0), 100.0, cfg) == 10


def test_max_position_pct_is_not_disableable_and_fails_safe():
    """A 0 here must block trading, not unlock it. Fail-safe direction."""
    cfg = _cfg()
    cfg["risk"]["max_position_pct"] = 0
    assert risk.size_order(_account(), 100.0, cfg) == 0


# ---- max_trades_per_day ----

def test_trade_rate_guard_still_fires_at_its_limit():
    cfg = _cfg()
    cfg["risk"]["max_trades_per_day"] = 2
    for _ in range(2):
        risk.record_trade()
    with pytest.raises(risk.RiskRejection, match="max trades per day"):
        risk.pre_trade_checks("buy", "AAA", 1, 100.0, _account(), {}, cfg)


def test_zero_trade_rate_guard_disables_it():
    cfg = _cfg()
    cfg["risk"]["max_trades_per_day"] = 0
    for _ in range(50):
        risk.record_trade()
    risk.pre_trade_checks("buy", "AAA", 1, 100.0, _account(), {}, cfg)


# ---- sim/live agreement on what 0 MEANS (divergence #9 prevention) ----

def test_backtest_and_live_read_the_same_disable_semantics():
    """backtest.py keeps its own copy of the trades/day check.

    If only one of the two honoured 0, the simulator and the live bot would
    disagree about the size of the book — which is precisely the shape of every
    divergence this project has logged.
    """
    import inspect

    import backtest as bt

    src = inspect.getsource(bt)
    # both call sites must guard on a falsy cap before comparing
    assert src.count('_cap_day = cfg["risk"].get("max_trades_per_day") or 0') == 2, (
        "a trades/day call site in backtest.py does not honour 0=disabled")
    assert "if _cap_day and fills_by_date" in src


def test_pure_checks_is_the_shared_path_for_both():
    """The count and order caps live in pure_checks, which the backtester
    imports directly — so they cannot diverge by construction. This asserts the
    sharing still exists rather than trusting it."""
    import inspect

    import backtest as bt

    assert "risk.pure_checks(" in inspect.getsource(bt)
