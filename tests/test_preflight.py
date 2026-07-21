"""Preflight: a misconfigured system fails SAFE — nothing trades past it."""
import copy

import main
import preflight

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES

import yaml


def test_clean_config_passes(cfg, tmp_path):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    assert preflight.run(cfg) == []


def test_missing_risk_param_fails(cfg, tmp_path):
    cfg["memory"]["ledger_path"] = str(tmp_path / "l.jsonl")
    bad = copy.deepcopy(cfg)
    del bad["risk"]["max_order_value_usd"]
    assert any("max_order_value_usd" in f for f in preflight.run(bad))
    bad2 = copy.deepcopy(cfg)
    bad2["risk"]["risk_per_trade_pct"] = -1
    assert any("risk_per_trade_pct" in f for f in preflight.run(bad2))


def test_missing_env_key_fails(cfg, tmp_path, monkeypatch):
    cfg["memory"]["ledger_path"] = str(tmp_path / "l.jsonl")
    monkeypatch.delenv("ALPACA_API_KEY")
    assert any("ALPACA_API_KEY" in f for f in preflight.run(cfg))


def test_live_without_interlock_fails(cfg, tmp_path, monkeypatch):
    cfg["memory"]["ledger_path"] = str(tmp_path / "l.jsonl")
    cfg["mode"] = "live"
    monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
    assert any("interlock" in f for f in preflight.run(cfg))


def test_non_daily_timeframe_fails(cfg, tmp_path):
    cfg["memory"]["ledger_path"] = str(tmp_path / "l.jsonl")
    cfg["strategy"]["timeframe"] = "5Min"
    assert any("1Day" in f for f in preflight.run(cfg))


def test_corrupt_ledger_tail_fails(cfg, tmp_path):
    p = tmp_path / "memory" / "ledger.jsonl"
    p.parent.mkdir(parents=True)
    p.write_text('{"type": "event"}\n{"truncated mid-wri')
    cfg["memory"]["ledger_path"] = str(p)
    assert any("corruption" in f for f in preflight.run(cfg))


def test_cycle_aborts_on_preflight_failure(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["max_trades_per_day"] = 0  # invalid: must be positive
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    broker = FakeCycleBroker(make_bars(BUY_CLOSES))
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()
    assert broker.submitted == []          # nothing traded
    from ledger import Ledger
    recs = Ledger(cfg["memory"]["ledger_path"]).all_records()
    assert any(r.get("event") == "preflight_failure" for r in recs)
