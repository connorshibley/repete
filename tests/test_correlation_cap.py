"""Correlation heat cap (2026-07-21, EastEquity-review port): co-moving names
are one bet — a new buy is blocked when too many open positions already move
with it. Entries only, fail-open without bars."""
import pytest
import yaml

import main
import risk
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES


def _trend_bars(closes):
    return make_bars(closes)


VARIED = [10, 11, 10.5, 12, 11.5, 13, 12.5, 14, 13.5, 15, 14.5, 16]
INVERSE = [16, 14.5, 15, 13.5, 14, 12.5, 13, 11.5, 12, 10.5, 11, 10]


# ---- pure ----

def test_identical_series_count():
    cand = _trend_bars(VARIED)
    assert risk.correlated_position_count(
        cand, {"A": _trend_bars(VARIED), "B": _trend_bars(VARIED)},
        lookback=60, threshold=0.85) == 2


def test_anticorrelated_and_flat_not_counted():
    cand = _trend_bars(VARIED)
    open_map = {"A": _trend_bars(INVERSE),      # corr ~ -1
                "B": _trend_bars([10] * 12)}    # zero variance -> skipped
    assert risk.correlated_position_count(cand, open_map, 60, 0.85) == 0


def test_missing_or_short_bars_fail_open():
    cand = _trend_bars(VARIED)
    open_map = {"A": None, "B": [], "C": _trend_bars(VARIED[:3])}
    assert risk.correlated_position_count(cand, open_map, 60, 0.85) == 0


def _cfg_with_cap(cfg, enabled=True):
    cfg["risk"]["correlation_cap"] = {"enabled": enabled, "lookback": 60,
                                      "threshold": 0.85, "max_correlated": 2}
    return cfg


def _acct():
    return {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}


def test_pre_trade_checks_rejects_at_cap(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # trade-count/HALT files stay in tmp
    _cfg_with_cap(cfg)
    positions = {"QQQ": {"market_value": 1000.0}, "DIA": {"market_value": 1000.0}}
    bars_map = {s: _trend_bars(VARIED) for s in ("SPY", "QQQ", "DIA")}
    with pytest.raises(risk.RiskRejection, match="correlation cap"):
        risk.pre_trade_checks("buy", "SPY", 10, 20.0, _acct(), positions, cfg,
                              bars_map=bars_map)


def test_pre_trade_checks_passes_without_bars_map(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cfg_with_cap(cfg)
    positions = {"QQQ": {"market_value": 1000.0}, "DIA": {"market_value": 1000.0}}
    risk.pre_trade_checks("buy", "SPY", 10, 20.0, _acct(), positions, cfg)


def test_pre_trade_checks_disabled_cap_inert(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cfg_with_cap(cfg, enabled=False)
    positions = {"QQQ": {"market_value": 1000.0}, "DIA": {"market_value": 1000.0}}
    bars_map = {s: _trend_bars(VARIED) for s in ("SPY", "QQQ", "DIA")}
    risk.pre_trade_checks("buy", "SPY", 10, 20.0, _acct(), positions, cfg,
                          bars_map=bars_map)


# ---- cycle wiring: clone-bar positions block the new SPY entry ----

def test_cycle_blocks_correlated_entry(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    _cfg_with_cap(cfg)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    # FakeCycleBroker serves the SAME bars for every symbol -> corr 1.0 with
    # both open positions, and BUY_CLOSES ends in a crossover buy for SPY.
    broker = FakeCycleBroker(
        make_bars(BUY_CLOSES),
        positions={"QQQ": {"qty": 5, "market_value": 100.0},
                   "DIA": {"qty": 5, "market_value": 100.0}})
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()

    assert not any(o["symbol"] == "SPY" for o in broker.submitted)
    led = Ledger(cfg["memory"]["ledger_path"])
    spy = [r for r in led.all_records() if r["type"] == "decision"
           and r["symbol"] == "SPY"]
    assert spy and "correlation cap" in spy[-1]["detail"]
