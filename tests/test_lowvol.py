"""§32's new strategy: prove it ranks the right way round and stays off.

Why this file exists
--------------------
`lowvol` is the second arm family in §32. A cross-sectional strategy has one
catastrophic failure mode that is invisible in aggregate results: **ranking in
the wrong direction.** A momentum strategy sorted backwards buys losers; a
low-volatility strategy sorted backwards buys the noisiest names in the
universe. Either would still produce a plausible-looking equity curve, still
trade, still clear a trade-count check — and would be measuring the opposite of
the registered claim.

So the first tests here are direction tests, stated as boundary pairs: the calm
name is bought and the wild name is not, differing only in volatility.

The last test is the one that matters operationally — §32 is pre-registered, not
adopted, so this must ship disabled.
"""
import math

import pytest

import strategies
from strategies import lowvol


def _bars(daily_moves, start=100.0):
    """Bars whose closes follow `daily_moves` (fractional daily returns)."""
    out, price = [], start
    for i, m in enumerate(daily_moves):
        price *= (1 + m)
        out.append({"ts": f"2026-01-{i + 1:03d}T21:00:00Z", "open": price,
                    "high": price, "low": price, "close": round(price, 6),
                    "volume": 1_000_000})
    return out


def _alternating(amplitude, n=80):
    """A saw-tooth of +/- amplitude: volatility scales with amplitude, and the
    price ends roughly where it started, so return does not confound the test."""
    return [amplitude if i % 2 == 0 else -amplitude / (1 + amplitude)
            for i in range(n)]


CALM = _bars(_alternating(0.001))     # ~0.1% daily swings
MID = _bars(_alternating(0.010))      # ~1%
WILD = _bars(_alternating(0.040))     # ~4%

PARAMS = {"vol_period": 60, "buy_bottom_fraction": 0.34,
          "exit_above_fraction": 0.67}
UNIVERSE = {"CALM": CALM, "MID": MID, "WILD": WILD, "MID2": MID}


# ---- direction: the whole point ----

def test_the_calmest_name_ranks_first_and_the_wildest_last():
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    assert ctx["ranks"]["CALM"] == 0
    assert ctx["ranks"]["WILD"] == ctx["n"] - 1
    assert ctx["vols"]["CALM"] < ctx["vols"]["MID"] < ctx["vols"]["WILD"]


def test_calm_is_bought_and_wild_is_not():
    """The boundary pair. Same universe, same params, same holding state — the
    only difference is which symbol is asked about."""
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    assert lowvol.generate("CALM", CALM, PARAMS, False, ctx).action == "buy"
    assert lowvol.generate("WILD", WILD, PARAMS, False, ctx).action != "buy"


def test_a_holding_is_sold_once_it_becomes_one_of_the_noisy_ones():
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    assert lowvol.generate("WILD", WILD, PARAMS, True, ctx).action == "sell"
    assert lowvol.generate("CALM", CALM, PARAMS, True, ctx).action == "hold"


def test_a_backwards_ranking_would_fail_this_file():
    """Stated as an executable claim rather than a comment: the calmest symbol
    must rank strictly ahead of the wildest, so a sort-order inversion cannot
    leave this file green."""
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    assert ctx["ranks"]["CALM"] < ctx["ranks"]["WILD"]


# ---- the fractions actually bind ----

def test_a_tighter_buy_fraction_excludes_a_name_a_looser_one_admits():
    """Boundary pair on the parameter, not the data. With 4 names, 0.34 admits
    rank 0 only; 0.60 admits ranks 0 and 1."""
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    second = [s for s, r in ctx["ranks"].items() if r == 1][0]
    tight = {**PARAMS, "buy_bottom_fraction": 0.24}
    loose = {**PARAMS, "buy_bottom_fraction": 0.60}
    assert lowvol.generate(second, UNIVERSE[second], tight, False, ctx).action != "buy"
    assert lowvol.generate(second, UNIVERSE[second], loose, False, ctx).action == "buy"


# ---- degrades quietly, like every other strategy here ----

def test_thin_history_is_excluded_rather_than_guessed():
    short = {"CALM": CALM[:5], "MID": MID, "WILD": WILD, "MID2": MID}
    ctx = lowvol.prepare(short, PARAMS)
    assert "CALM" not in ctx["ranks"]
    assert lowvol.generate("CALM", CALM[:5], PARAMS, False, ctx).action == "hold"


def test_a_tiny_or_missing_cross_section_never_trades():
    assert lowvol.generate("CALM", CALM, PARAMS, False, None).action == "hold"
    assert lowvol.generate("CALM", CALM, PARAMS, False,
                           {"n": 2, "ranks": {}, "vols": {}}).action == "hold"


def test_a_zero_volatility_series_does_not_divide_by_zero():
    """A halted or synthetic name with a flat close has vol 0 and must be
    dropped, not ranked first with an infinite Sharpe."""
    flat = _bars([0.0] * 80)
    ctx = lowvol.prepare({**UNIVERSE, "FLAT": flat}, PARAMS)
    assert "FLAT" not in ctx["ranks"]


# ---- one definition of volatility ----

def test_it_uses_the_same_vol_function_as_the_sizing_rail():
    """Two definitions of 'volatility' drifting apart is the §29 failure mode
    wearing a different hat: preflight and risk.py disagreed about what
    max_order_value_usd: 0 meant and the bot stopped trading for a day."""
    import risk
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    for sym in ("CALM", "MID", "WILD"):
        expect = risk.realized_annual_vol(UNIVERSE[sym], PARAMS["vol_period"])
        assert math.isclose(ctx["vols"][sym], expect, rel_tol=1e-12)


# ---- registered, not adopted ----

def test_it_is_in_the_registry_and_obeys_the_strategy_contract():
    assert strategies.REGISTRY["lowvol"] is lowvol
    for attr in ("NAME", "NEEDS_CROSS_SECTION", "required_lookback",
                 "prepare", "generate"):
        assert hasattr(lowvol, attr), attr
    assert lowvol.NEEDS_CROSS_SECTION is True


def test_the_shipped_config_does_not_enable_it():
    """§32 is pre-registered, not adopted. A strategy is never adopted by
    enabling it and seeing what happens."""
    import os
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    entry = (cfg.get("strategies") or {}).get("lowvol") or {}
    assert not entry.get("enabled"), (
        "lowvol is enabled in the shipped config — its gate has not passed")
    assert "lowvol" not in [n for n, _ in strategies.enabled(cfg)]


@pytest.mark.parametrize("frac", [0.0, 1.0])
def test_degenerate_fractions_do_not_raise(frac):
    ctx = lowvol.prepare(UNIVERSE, PARAMS)
    p = {**PARAMS, "buy_bottom_fraction": frac, "exit_above_fraction": frac}
    lowvol.generate("CALM", CALM, p, False, ctx)
    lowvol.generate("CALM", CALM, p, True, ctx)
