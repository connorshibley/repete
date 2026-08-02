"""Upper bounds on the percentage rails (2026-08-02).

Why this file exists
--------------------
Every existing preflight check asks whether a risk number is PRESENT and
non-negative. None asked whether it was possible. So `risk_per_trade_pct: 150`
and `max_position_pct: 500` both passed preflight and went straight into the
sizing arithmetic — a fat-fingered decimal point silently removing the
concentration cap rather than being caught before production.

The bound is deliberately loose, and the tests below pin that looseness as
hard as they pin the bound. This owner runs values other people would call
reckless, on purpose and with recorded evidence (§29 took per-trade risk from
1.0 to 8.0). Preflight must not relitigate a risk decision; it may only report
arithmetic that cannot be true. A percentage of equity above 100 is the latter:
you cannot risk more than the account on one trade, or fall more than 100% from
a peak.

So: 80 passes (bold, coherent, the owner's call). 800 fails (a keystroke).
"""
import preflight


def _cfg(**risk_over):
    risk = {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
            "daily_loss_limit_pct": 5.0, "min_holding_days": 2,
            "max_order_value_usd": 0, "max_trades_per_day": 15,
            "max_drawdown_pct": 10.0, "max_portfolio_heat_pct": 4.0,
            "brackets": {"enabled": True}}
    risk.update(risk_over)
    return {
        "mode": "paper",
        "risk": risk,
        "strategy": {"timeframe": "1Day"},
        "llm": {"enabled": False},
        "learning": {"regime": {"sma_period": 200, "vol_period": 20}},
        "memory": {"ledger_path": "memory/does-not-exist.jsonl"},
    }


def _bound_fails(cfg):
    return [f for f in preflight.run(cfg) if "not possible" in f]


# ---- it catches the keystroke ---------------------------------------------

def test_every_percentage_rail_rejects_an_impossible_value():
    for key in preflight.PERCENT_OF_EQUITY_RAILS:
        fails = _bound_fails(_cfg(**{key: 150}))
        assert any(key in f for f in fails), (
            f"risk.{key} accepted 150% of equity", fails)


def test_the_audits_own_example_is_caught():
    """`risk_per_trade_pct: 150` — the value the 2026-08-02 audit found would
    ship silently."""
    assert _bound_fails(_cfg(risk_per_trade_pct=150))


def test_a_missing_decimal_point_on_the_concentration_cap_is_caught():
    """max_position_pct: 500 would let one name hold five times the account."""
    fails = _bound_fails(_cfg(max_position_pct=500))
    assert any("max_position_pct" in f for f in fails), fails


# ---- it does NOT relitigate the owner's risk appetite ----------------------

def test_the_shipped_config_passes_its_own_new_check():
    """The §29 trap, restated: a preflight rule nobody ran the SHIPPED config
    through is how every cycle silently aborted for a day."""
    import yaml
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        shipped = yaml.safe_load(f)
    assert not _bound_fails(shipped), "the shipped config fails its own bound"


def test_bold_but_coherent_values_are_left_alone():
    """80% per trade is not a typo the tool should catch — it is a decision the
    tool has no standing to overrule. Only impossible values are reported."""
    for key in preflight.PERCENT_OF_EQUITY_RAILS:
        assert not _bound_fails(_cfg(**{key: 80})), (
            f"risk.{key} at 80 was reported; preflight must not second-guess "
            f"an aggressive but coherent choice")


def test_exactly_one_hundred_is_allowed():
    """The boundary itself: 100% of equity is coherent (it is the whole
    account), so the check is > not >=."""
    for key in preflight.PERCENT_OF_EQUITY_RAILS:
        assert not _bound_fails(_cfg(**{key: 100})), key


# ---- it does not invent failures ------------------------------------------

def test_an_absent_optional_rail_is_not_reported_by_the_bound():
    """max_drawdown_pct and max_portfolio_heat_pct are legitimately omittable
    (risk.py reads them as `or 0`). Absence must not surface as a bound
    violation — that would bury the real fault in noise."""
    cfg = _cfg()
    del cfg["risk"]["max_drawdown_pct"]
    del cfg["risk"]["max_portfolio_heat_pct"]
    assert not _bound_fails(cfg)


def test_a_boolean_is_not_mistaken_for_a_percentage():
    """`isinstance(True, int)` is True in Python. A bool here is a different
    config error and must not be reported as an impossible percentage."""
    assert not _bound_fails(_cfg(risk_per_trade_pct=True))
