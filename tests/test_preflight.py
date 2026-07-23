"""Preflight: a misconfigured system fails SAFE — nothing trades past it."""
import copy
import os

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


# ---- regime block validation (a bad period used to crash mid-cycle) ----

def test_regime_block_validated(cfg):
    import copy
    base = copy.deepcopy(cfg)
    base.setdefault("learning", {})["regime"] = {"sma_period": 50,
                                                 "vol_period": 20}
    assert not [f for f in preflight.run(base) if "regime" in f]

    bad = copy.deepcopy(base)
    bad["learning"]["regime"]["vol_period"] = 1        # divides by zero
    assert any("vol_period" in f for f in preflight.run(bad))

    missing = copy.deepcopy(base)
    del missing["learning"]["regime"]
    assert any("learning.regime block missing" in f
               for f in preflight.run(missing))


def test_ledger_tail_check_follows_sqlite_backend(cfg, tmp_path, monkeypatch):
    """Under storage.backend: sqlite the tail check must inspect the LIVE store,
    not a stale ledger.jsonl. Preflight runs before store.configure(), so it
    resolves the backend itself without mutating the global one."""
    import copy
    import store as store_mod
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    # A corrupt JSONL file that must be IGNORED when the backend is sqlite.
    with open("memory/ledger.jsonl", "w") as f:
        f.write("{ this is not json\n")
    c = copy.deepcopy(cfg)
    c["memory"]["ledger_path"] = "memory/ledger.jsonl"
    c["storage"] = {"backend": "sqlite", "sqlite_path": "memory/agent.db"}
    store_mod.configure(None)                     # global stays jsonl
    try:
        fails = preflight.run(c)
        assert not any("ledger tail" in f for f in fails), fails
        assert store_mod.current_backend() == "jsonl"   # no global mutation
    finally:
        store_mod.configure(None)


def test_ledger_tail_check_still_catches_jsonl_corruption(cfg, tmp_path,
                                                          monkeypatch):
    import copy
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    with open("memory/ledger.jsonl", "w") as f:
        f.write('{"type":"event"}\n{ truncated\n')
    c = copy.deepcopy(cfg)
    c["memory"]["ledger_path"] = "memory/ledger.jsonl"
    assert any("ledger tail" in f for f in preflight.run(c))
