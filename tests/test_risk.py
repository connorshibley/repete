"""risk.py touches cwd-relative files (HALT, memory/.trade_counts.json),
so every test here runs chdir'd into tmp_path.
"""
from datetime import datetime, timedelta, timezone

import pytest

import risk


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# ---- size_order ----

def test_size_order_fixed_fractional(cfg, account):
    # 1% of 100k = $1000 at $50 -> 20 shares
    assert risk.size_order(account, 50.0, cfg) == 20


def test_size_order_clamped_by_order_cap(cfg, account):
    cfg["risk"]["risk_per_trade_pct"] = 50.0  # $50k, but cap is $2000
    assert risk.size_order(account, 100.0, cfg) == 20


def test_size_order_clamped_by_concentration(cfg, account):
    cfg["risk"]["risk_per_trade_pct"] = 50.0
    cfg["risk"]["max_order_value_usd"] = 1_000_000
    # 10% concentration cap -> $10k at $100 -> 100 shares
    assert risk.size_order(account, 100.0, cfg) == 100


def test_size_order_clamped_by_buying_power(cfg, account):
    account["buying_power"] = 150.0
    assert risk.size_order(account, 100.0, cfg) == 1


def test_size_order_zero_when_price_exceeds_budget(cfg, account):
    assert risk.size_order(account, 5000.0, cfg) == 0


# ---- pre_trade_checks rejections ----

def test_rejects_zero_qty(cfg, account):
    with pytest.raises(risk.RiskRejection, match="quantity is zero"):
        risk.pre_trade_checks("buy", "SPY", 0, 100.0, account, {}, cfg)


def test_rejects_when_trade_rate_hit(cfg, account):
    risk.record_trade()
    risk.record_trade()
    with pytest.raises(risk.RiskRejection, match="max trades per day"):
        risk.pre_trade_checks("buy", "SPY", 1, 100.0, account, {}, cfg)


def test_rejects_order_value_over_cap(cfg, account):
    with pytest.raises(risk.RiskRejection, match="exceeds cap"):
        risk.pre_trade_checks("buy", "SPY", 30, 100.0, account, {}, cfg)  # $3000 > $2000


def test_rejects_max_open_positions_for_new_symbol(cfg, account):
    positions = {s: {"market_value": 100.0} for s in "ABCDE"}
    with pytest.raises(risk.RiskRejection, match="max open positions"):
        risk.pre_trade_checks("buy", "SPY", 1, 100.0, account, positions, cfg)


def test_allows_add_to_existing_symbol_at_position_cap(cfg, account):
    positions = {s: {"market_value": 100.0} for s in "ABCD"}
    positions["SPY"] = {"market_value": 100.0}
    risk.pre_trade_checks("buy", "SPY", 1, 100.0, account, positions, cfg)  # no raise


def test_rejects_concentration_including_existing(cfg, account):
    positions = {"SPY": {"market_value": 9500.0}}  # cap is 10% of 100k = $10k
    with pytest.raises(risk.RiskRejection, match="concentration cap"):
        risk.pre_trade_checks("buy", "SPY", 10, 100.0, account, positions, cfg)


def test_rejects_sell_without_position(cfg, account):
    with pytest.raises(risk.RiskRejection, match="state desync guard"):
        risk.pre_trade_checks("sell", "SPY", 5, 100.0, account, {}, cfg)


def test_rejects_when_halted(cfg, account):
    risk.engage_halt("test")
    with pytest.raises(risk.RiskRejection, match="HALT"):
        risk.pre_trade_checks("buy", "SPY", 1, 100.0, account, {}, cfg)


# ---- swing guard ----

def _ts(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_swing_guard_blocks_young_position(cfg):
    with pytest.raises(risk.RiskRejection, match="swing guard"):
        risk.swing_guard(_ts(0.5), cfg)


def test_swing_guard_allows_old_position(cfg):
    risk.swing_guard(_ts(3), cfg)  # no raise


def test_swing_guard_passes_without_entry_ts(cfg):
    risk.swing_guard(None, cfg)


def test_swing_guard_disabled_when_min_days_zero(cfg):
    cfg["risk"]["min_holding_days"] = 0
    risk.swing_guard(_ts(0.1), cfg)


def test_sell_of_young_position_rejected_end_to_end(cfg, account):
    positions = {"SPY": {"market_value": 1000.0}}
    with pytest.raises(risk.RiskRejection, match="swing guard"):
        risk.pre_trade_checks("sell", "SPY", 5, 100.0, account, positions, cfg,
                              entry_ts=_ts(1))


# ---- HALT file ----

def test_halt_engage_and_check(cfg):
    assert not risk.check_halt()
    risk.engage_halt("daily loss limit breached")
    assert risk.check_halt()
    with open(risk.HALT_FILE) as f:
        assert "daily loss limit breached" in f.read()


# ---- daily loss ----

def test_daily_loss_breached(cfg, account):
    account["equity"] = 96_000.0  # -4% vs limit -3%
    assert risk.daily_loss_breached(account, cfg)


def test_daily_loss_not_breached(cfg, account):
    account["equity"] = 99_000.0  # -1%
    assert not risk.daily_loss_breached(account, cfg)
