"""Param-gated candidate rules: tsmom index-regime gate and the generic
max_entries_per_cycle cap. With params absent, behavior must be identical
to the pre-candidate code — only a passed enablement gate flips config."""
import yaml

import backtest
import main
from ledger import Ledger
from strategies import tsmom

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES

UP = [10 + i for i in range(10)]        # steady uptrend
DOWN = [20 - i for i in range(10)]      # steady downtrend


# ---------------------------------------------------------------- tsmom gate

def test_prepare_inactive_without_param():
    assert tsmom.prepare({"SPY": make_bars(DOWN)}, {})["index_ok"] is True
    assert tsmom.prepare({}, {"index_sma_period": 0})["index_ok"] is True


def test_prepare_blocks_when_index_below_sma():
    ctx = tsmom.prepare({"SPY": make_bars(DOWN)}, {"index_sma_period": 5})
    assert ctx["index_ok"] is False


def test_prepare_allows_when_index_above_sma():
    ctx = tsmom.prepare({"SPY": make_bars(UP)}, {"index_sma_period": 5})
    assert ctx["index_ok"] is True


def test_prepare_fails_open_on_short_index_history():
    ctx = tsmom.prepare({"SPY": make_bars(DOWN[:3])}, {"index_sma_period": 5})
    assert ctx["index_ok"] is True


def test_gate_blocks_entry_but_never_exit():
    params = {"momentum_bars": 3, "skip_bars": 0, "trend_sma_period": 5,
              "index_sma_period": 5}
    bars = make_bars(UP)
    blocked = tsmom.generate("QQQ", bars, params, holding=False,
                             cross_section={"index_ok": False,
                                            "index_symbol": "SPY",
                                            "index_sma_period": 5})
    assert blocked.action == "hold" and "index gate" in blocked.reason

    allowed = tsmom.generate("QQQ", bars, params, holding=False,
                             cross_section={"index_ok": True})
    assert allowed.action == "buy"

    # exits are owner-only and never gated: a broken trend sells regardless
    exit_sig = tsmom.generate("QQQ", make_bars(DOWN), params, holding=True,
                              cross_section={"index_ok": False})
    assert exit_sig.action == "sell"


def test_generate_without_cross_section_unchanged():
    params = {"momentum_bars": 3, "skip_bars": 0, "trend_sma_period": 5}
    assert tsmom.generate("QQQ", make_bars(UP), params,
                          holding=False).action == "buy"


# ------------------------------------------------- entry cap: backtest side

def _two_symbol_bars():
    closes = BUY_CLOSES + [20, 20]  # cross fires, then bars exist to fill on
    return {"AAA": make_bars(closes), "BBB": make_bars(closes)}


def test_simulate_cap_limits_entries_per_day(cfg):
    cfg["risk"]["max_trades_per_day"] = 5
    capped = backtest.simulate(_two_symbol_bars(), cfg,
                               params={"fast_period": 3, "slow_period": 5,
                                       "max_entries_per_cycle": 1,
                                       "stop_atr_mult": 0,
                                       "take_profit_atr_mult": 0})
    uncapped = backtest.simulate(_two_symbol_bars(), cfg,
                                 params={"fast_period": 3, "slow_period": 5,
                                         "stop_atr_mult": 0,
                                         "take_profit_atr_mult": 0})
    assert uncapped.n_trades == 2
    assert capped.n_trades == 1


# ----------------------------------------------------- entry cap: live side

def test_live_cycle_respects_entry_cap(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["symbols"] = ["SPY", "QQQ"]
    cfg["risk"]["max_trades_per_day"] = 5
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["strategies"] = {"ma_crossover": {"enabled": True, "priority": 1,
                                          "fast_period": 3, "slow_period": 5,
                                          "max_entries_per_cycle": 1}}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    broker = FakeCycleBroker(make_bars(BUY_CLOSES))  # both symbols signal buy
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)

    main.run_cycle()

    assert len(broker.submitted) == 1  # second entry blocked by the cap
    led = Ledger(cfg["memory"]["ledger_path"])
    holds = [r for r in led.all_records() if r["type"] == "decision"
             and r["action"] == "hold"]
    assert any("max_entries_per_cycle" in str(r["indicators"]) for r in holds)
