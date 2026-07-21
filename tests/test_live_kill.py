"""Pre-registered live kill criteria: a strategy whose LIVE record breaches
PF < pf_below over >= min_trades closed trades stops entering. Exits always
run — retiring a strategy must never strand its open positions."""
import yaml

import main
import risk
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES

KILL_CFG = {"risk": {"live_kill": {"enabled": True, "min_trades": 3,
                                   "pf_below": 0.8}}}


def _closed(strategy, pnls):
    return [{"strategy": strategy, "pnl": p} for p in pnls]


def test_stats_and_kill_threshold():
    losers = _closed("ma_crossover", [-100, -100, 50])   # PF 0.25, n=3
    assert risk.strategy_live_stats(losers, "ma_crossover")["pf"] == 0.25
    assert risk.live_kill_blocked(losers, "ma_crossover", KILL_CFG)


def test_no_kill_below_min_trades_or_good_pf():
    few = _closed("ma_crossover", [-100, -100])          # n=2 < 3
    assert risk.live_kill_blocked(few, "ma_crossover", KILL_CFG) is None
    good = _closed("ma_crossover", [100, 100, -50])      # PF 4.0
    assert risk.live_kill_blocked(good, "ma_crossover", KILL_CFG) is None
    # other strategies' losses never kill this one
    other = _closed("tsmom", [-100, -100, -100])
    assert risk.live_kill_blocked(other, "ma_crossover", KILL_CFG) is None


def test_disabled_never_kills():
    losers = _closed("ma_crossover", [-100] * 10)
    off = {"risk": {"live_kill": {"enabled": False}}}
    assert risk.live_kill_blocked(losers, "ma_crossover", off) is None
    assert risk.live_kill_blocked(losers, "ma_crossover", {"risk": {}}) is None


def test_cycle_blocks_entry_for_killed_strategy(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["live_kill"] = {"enabled": True, "min_trades": 3,
                                "pf_below": 0.8}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    led = Ledger(cfg["memory"]["ledger_path"])
    for i in range(3):  # 3 live losers for ma_crossover -> PF 0
        tid = led.log_decision("SPY", "buy", f"old {i}", {}, None,
                               executed=True, entry_price=100.0, qty=10,
                               strategy="ma_crossover")
        led.close_trade(tid, 95.0, -50.0, -5.0)
    broker = FakeCycleBroker(make_bars(BUY_CLOSES))  # fresh crossover buy
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()

    assert broker.submitted == []
    last = [r for r in led.all_records() if r["type"] == "decision"][-1]
    assert not last["executed"] and "live kill" in last["detail"]
