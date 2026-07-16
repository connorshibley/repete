import strategy
from conftest import make_bars


def test_crossover_up_buys_when_flat(cfg, bars_up):
    sig = strategy.generate_signal("SPY", bars_up, cfg, holding=False)
    assert sig.action == "buy"
    assert "crossed above" in sig.reason
    assert sig.indicators["close"] == 20


def test_crossover_up_holds_when_already_holding(cfg, bars_up):
    sig = strategy.generate_signal("SPY", bars_up, cfg, holding=True)
    assert sig.action == "hold"


def test_crossover_down_sells_when_holding(cfg):
    bars = make_bars([10] * 6 + [11, 11, 11, 2])
    sig = strategy.generate_signal("SPY", bars, cfg, holding=True)
    assert sig.action == "sell"
    assert "crossed below" in sig.reason


def test_crossover_down_holds_when_flat(cfg):
    bars = make_bars([10] * 6 + [11, 11, 11, 2])
    sig = strategy.generate_signal("SPY", bars, cfg, holding=False)
    assert sig.action == "hold"


def test_no_crossover_holds(cfg):
    bars = make_bars([10] * 10)
    sig = strategy.generate_signal("SPY", bars, cfg, holding=False)
    assert sig.action == "hold"
    assert "no crossover" in sig.reason


def test_insufficient_history_holds(cfg):
    bars = make_bars([10, 11, 12])  # need slow_period + 1 = 6
    sig = strategy.generate_signal("SPY", bars, cfg, holding=False)
    assert sig.action == "hold"
    assert "insufficient history" in sig.reason
