"""Per-strategy stop width (2026-08-11, for swing_sectors §62).

`risk.bracket_prices` and `risk.unprotectable_entry` both take `strategy=`;
a strategy's own `stop_atr_mult` REPLACES the global `risk.brackets`
multipliers (base and high-vol both). The owner's "accept volatility" is
expressed here, per-trade — and the two functions must answer from the same
number, because the second exists to predict when the first returns None.
"""
import sys

import yaml

sys.path.insert(0, "src")
import risk                                                   # noqa: E402

with open("config.yaml") as _f:
    SHIPPED = yaml.safe_load(_f)


def _cfg(strategies=None, high_vol=0):
    c = {"risk": {"brackets": {"enabled": True, "stop_atr_mult": 2.0,
                               "take_profit_atr_mult": 0}}}
    if high_vol:
        c["risk"]["brackets"]["stop_atr_mult_high_vol"] = high_vol
    if strategies:
        c["strategies"] = strategies
    return c


# ------------------------------------------------------------ bracket_prices

def test_no_override_is_byte_identical_to_before():
    """Every pre-existing strategy sets no stop_atr_mult, so passing its name
    must change nothing — pinned as a value, not a diff."""
    assert risk.bracket_prices(100.0, 2.0, _cfg()) == (96.0, None)
    assert risk.bracket_prices(100.0, 2.0, _cfg({"tsmom": {"enabled": True}}),
                               strategy="tsmom") == (96.0, None)


def test_the_override_widens_the_stop():
    cfg = _cfg({"swing_sectors": {"stop_atr_mult": 3.5}})
    stop, _ = risk.bracket_prices(100.0, 2.0, cfg, strategy="swing_sectors")
    assert stop == 93.0                        # 100 − 3.5·2, not 100 − 2·2


def test_the_override_beats_the_high_vol_multiplier():
    """The strategy priced its own volatility; the vol bucket must not
    re-tighten (or re-widen) a stop the override already chose."""
    cfg = _cfg({"swing_sectors": {"stop_atr_mult": 3.5}}, high_vol=3.0)
    stop, _ = risk.bracket_prices(100.0, 2.0, cfg, vol_bucket="high",
                                  strategy="swing_sectors")
    assert stop == 93.0                        # 3.5, not the high-vol 3.0


def test_an_unnamed_strategy_still_gets_the_high_vol_stop():
    cfg = _cfg({"swing_sectors": {"stop_atr_mult": 3.5}}, high_vol=3.0)
    assert risk.bracket_prices(100.0, 2.0, cfg, vol_bucket="high")[0] == 94.0


def test_the_short_geometry_inverts_with_the_override_too():
    cfg = _cfg({"swing_sectors": {"stop_atr_mult": 3.5}})
    stop, _ = risk.bracket_prices(100.0, 2.0, cfg, direction="short",
                                  strategy="swing_sectors")
    assert stop == 107.0                       # above entry, 3.5·ATR away


# ------------------------------------------------------- unprotectable_entry

def test_unprotectable_consults_the_override_boundary_pair():
    """Entry $8, ATR $2.5: the global 2× stop lands at $3 (protectable), the
    3.5× override at −$0.75 (not). If unprotectable_entry read only the
    global multipliers this entry would pass the guard and then degrade to a
    stopless market order — the exact inversion the guard exists to refuse."""
    cfg = _cfg({"swing_sectors": {"stop_atr_mult": 3.5}})
    assert risk.unprotectable_entry(8.0, 2.5, cfg) is False
    assert risk.unprotectable_entry(8.0, 2.5, cfg,
                                    strategy="swing_sectors") is True


def test_unprotectable_still_uses_the_widest_global_without_an_override():
    cfg = _cfg(high_vol=3.0)
    assert risk.unprotectable_entry(7.0, 2.5, cfg) is True     # 3.0·2.5 > 7
    assert risk.unprotectable_entry(8.0, 2.5, cfg) is False    # 3.0·2.5 < 8


# ------------------------------------------------------------ shipped config

def test_shipped_swing_stop_is_3_5_and_untrailed():
    """The wide stop must not be quietly ratcheted by the chandelier trail:
    trailing at 3·ATR would re-tighten what the 3.5·ATR stop deliberately
    leaves loose. trailing_strategies is the opt-in list; swing stays out."""
    assert SHIPPED["strategies"]["swing_sectors"]["stop_atr_mult"] == 3.5
    assert "swing_sectors" not in \
        SHIPPED["risk"]["brackets"]["trailing_strategies"]


def test_shipped_config_reaches_bracket_prices():
    """End-to-end on the real config, not a fixture: the override arrives."""
    stop, _ = risk.bracket_prices(100.0, 2.0, SHIPPED,
                                  strategy="swing_sectors")
    assert stop == 93.0
    base_stop, _ = risk.bracket_prices(100.0, 2.0, SHIPPED, strategy="tsmom")
    assert base_stop == 96.0
