import risk


def test_bracket_prices_math(cfg):
    # entry 100, ATR 2: stop = 100 - 2*2 = 96, tp = 100 + 3*2 = 106
    assert risk.bracket_prices(100.0, 2.0, cfg) == (96.0, 106.0)


def test_bracket_prices_rounded_to_cents(cfg):
    stop, tp = risk.bracket_prices(100.0, 1.234, cfg)
    assert stop == 97.53 and tp == 103.7


def test_bracket_prices_none_when_disabled(cfg):
    cfg["risk"]["brackets"]["enabled"] = False
    assert risk.bracket_prices(100.0, 2.0, cfg) is None


def test_bracket_prices_none_without_brackets_section(cfg):
    del cfg["risk"]["brackets"]  # old config.yaml keeps working
    assert risk.bracket_prices(100.0, 2.0, cfg) is None


def test_bracket_prices_none_when_atr_missing(cfg):
    assert risk.bracket_prices(100.0, None, cfg) is None
    assert risk.bracket_prices(100.0, 0.0, cfg) is None


def test_bracket_prices_none_when_stop_not_positive(cfg):
    # entry 3, ATR 2, stop_mult 2 -> stop = -1: degrade to plain market order
    assert risk.bracket_prices(3.0, 2.0, cfg) is None


def test_stop_only_when_tp_mult_zero(cfg):
    cfg["risk"]["brackets"]["take_profit_atr_mult"] = 0
    assert risk.bracket_prices(100.0, 2.0, cfg) == (96.0, None)
