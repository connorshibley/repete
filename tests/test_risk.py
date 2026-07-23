"""risk.py touches cwd-relative files (HALT, memory/.trade_counts.json),
so every test here runs chdir'd into tmp_path.
"""
import glob
import json
import os
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


# ---- §11: risk_pct is clamped by max_order_value_usd ----

def _acct_100k():
    return {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}


def test_risk_pct_is_clamped_by_max_order_value(cfg):
    """knowledge/backtest_candidates.md §11: risk_pct 1.0/2.0/5.0 are the SAME
    setting at the current caps — size_order applies max_order_value_usd after
    the risk-based calculation, so all three clamp to the same position. If this
    ever fails, the effective position-size lever changed and §11's conclusion
    (and the kill-switch interaction) must be re-derived."""
    cfg["risk"]["max_order_value_usd"] = 2000
    cfg["risk"]["max_position_pct"] = 10.0
    price, stop = 100.0, 93.0                      # 7% stop distance
    sizes = {}
    for rp in (1.0, 2.0, 5.0):
        cfg["risk"]["risk_sizing"] = {"enabled": True, "risk_pct": rp,
                                      "strategies": ["meanrev"]}
        sizes[rp] = risk.size_order(_acct_100k(), price, cfg,
                                    strategy="meanrev", stop_price=stop)
    assert len(set(sizes.values())) == 1, f"expected identical sizing, got {sizes}"
    assert sizes[5.0] * price == 2000          # clamped to the per-order cap


def test_single_trade_loss_cannot_trip_the_kill_switch(cfg):
    """A single clamped stop-out must stay well under daily_loss_limit_pct —
    the 2026-07-22 claim that 5% risk would HALT the bot on its first loser was
    wrong, and this pins the real relationship."""
    cfg["risk"]["max_order_value_usd"] = 2000
    cfg["risk"]["daily_loss_limit_pct"] = 3.0
    cfg["risk"]["risk_sizing"] = {"enabled": True, "risk_pct": 5.0,
                                  "strategies": ["meanrev"]}
    acct = _acct_100k()
    kill_switch_dollars = acct["equity"] * cfg["risk"]["daily_loss_limit_pct"] / 100
    for stop_frac in (0.04, 0.07, 0.15, 0.30):
        price = 100.0
        stop = price * (1 - stop_frac)
        qty = risk.size_order(acct, price, cfg, strategy="meanrev",
                              stop_price=stop)
        loss = qty * (price - stop)
        assert loss < kill_switch_dollars, (
            f"stop {stop_frac:.0%}: single loss ${loss:,.0f} would trip the "
            f"${kill_switch_dollars:,.0f} kill switch")


def test_heat_cap_not_binding_for_clamped_meanrev_entries(cfg):
    """§11 correction: at max_portfolio_heat_pct 4.0 a full book of clamped
    meanrev positions does NOT breach the cap, so raising it to 10.0 was
    unnecessary."""
    cfg["risk"]["max_order_value_usd"] = 2000
    cfg["risk"]["max_portfolio_heat_pct"] = 4.0
    cfg["risk"]["risk_sizing"] = {"enabled": True, "risk_pct": 5.0,
                                  "strategies": ["meanrev"]}
    acct = _acct_100k()
    price, stop = 100.0, 93.0
    qty = risk.size_order(acct, price, cfg, strategy="meanrev", stop_price=stop)
    open_trades = {f"t{i}": {"symbol": f"S{i}", "qty": qty,
                             "entry_price": price, "order": {"stop_price": stop}}
                   for i in range(4)}
    heat = risk.portfolio_heat(open_trades, cfg, acct["equity"])
    cap = acct["equity"] * cfg["risk"]["max_portfolio_heat_pct"] / 100
    assert heat < cap, f"heat ${heat:,.0f} unexpectedly >= cap ${cap:,.0f}"


# ---- trade counter must fail CLOSED, not open ----

def test_corrupt_trade_counter_fails_closed(cfg, tmp_path, monkeypatch):
    """The daily trade cap is a rail. A corrupt counter file used to be
    swallowed as 0, silently un-capping max_trades_per_day. It must instead be
    treated as at-limit so the rail keeps binding."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    with open(risk._TRADECOUNT_FILE, "w") as f:
        f.write("{not valid json")
    assert risk._trades_today() >= cfg["risk"]["max_trades_per_day"]


def test_trade_counter_write_is_atomic(cfg, tmp_path, monkeypatch):
    """record_trade must never leave a truncated file behind: it writes to a
    temp file and renames, so a crash mid-write keeps the previous good value."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    risk.record_trade()
    risk.record_trade()
    assert risk._trades_today() == 2
    with open(risk._TRADECOUNT_FILE) as f:
        json.load(f)                      # parses => not truncated
    assert not glob.glob(risk._TRADECOUNT_FILE + ".tmp*")   # no litter
