"""xsmom's short leg: the mirror image of its long leg, and nothing more.

The 130/30 design confines every short in this repo to this one strategy, so
`xsmom.enabled: false` restores today's bot exactly and the leg stays
attributable. What lands here is deliberately symmetric:

    long   buy   when pct_rank <  buy_top_fraction      and mom > 0
           sell  when pct_rank >= exit_below_fraction
    short  short when pct_rank >= 1 - short_bottom_fraction and mom < 0
           cover when pct_rank <  exit_below_fraction

Every test below is a BOUNDARY PAIR: the short-side assertion sits beside the
long-side twin it mirrors, because the long path is what production actually
runs and "the short case is right" is worth nothing if the long case moved to
make it so.

`n = 4` throughout, which makes pct_rank land exactly on 0.00 / 0.25 / 0.50 /
0.75 — the four thresholds these params use. Inequalities are tested ON the
threshold, not near it: `>=` versus `>` is the difference between shorting the
bottom quartile and shorting the bottom quartile minus one name, and no fixture
that only samples the interior can tell them apart.

NOTHING SHORTS IN PRODUCTION. xsmom ships `enabled: false`; arming
`short_bottom_fraction` arms the leg for the Phase 3 DIAGNOSTIC, which must be
registered and run before `enabled: true` is even a question.
"""
import os

import pytest
import yaml

import preflight
from strategies import xsmom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")

PARAMS = {"rank_lookback_bars": 231, "skip_bars": 21,
          "buy_top_fraction": 0.25, "exit_below_fraction": 0.50,
          "short_bottom_fraction": 0.25}


def _ctx(momentum):
    """A 4-name cross-section, ranked strongest first, so `momentum` maps
    directly onto ranks 0..3 and pct_rank onto 0.00/0.25/0.50/0.75."""
    ordered = sorted(momentum, key=momentum.get, reverse=True)
    return {"ranks": {s: i for i, s in enumerate(ordered)},
            "returns": dict(momentum), "n": len(ordered)}


#: Strongest to weakest, and the weakest is the only one with NEGATIVE momentum.
MOM = {"AAA": 0.40, "BBB": 0.20, "CCC": 0.05, "DDD": -0.30}
CTX = _ctx(MOM)


def _gen(symbol, holding=False, side=None, params=None, ctx=None):
    return xsmom.generate(symbol, [], params or PARAMS, holding,
                          ctx or CTX, position_side=side)


# ---------------------------------------------------------------------------
# ENTRY — both conditions mirrored, both tested on the threshold.
# ---------------------------------------------------------------------------

def test_the_weakest_name_with_negative_momentum_is_shorted():
    sig = _gen("DDD")
    assert sig.action == "short"
    assert "laggard" in sig.reason


def test_the_strongest_name_with_positive_momentum_is_still_bought():
    """The twin. This is the branch production runs today; if it moved, the
    short leg was not added, it was substituted."""
    sig = _gen("AAA")
    assert sig.action == "buy"
    assert "leader" in sig.reason


def test_a_bottom_quartile_name_with_POSITIVE_momentum_is_not_shorted():
    """`mom < 0` is mirrored from the buy's `mom > 0` on purpose. Dropping it
    would short the weakest quarter of a universe that is rising as a whole —
    a relative-weakness bet expressed as an absolute-direction position, which
    is a different claim from the one the long leg makes."""
    ctx = _ctx({"AAA": 0.40, "BBB": 0.30, "CCC": 0.20, "DDD": 0.10})
    assert _gen("DDD", ctx=ctx).action == "hold"


def test_a_top_quartile_name_with_NEGATIVE_momentum_is_not_bought():
    """The twin of the momentum-sign condition, on the long side."""
    ctx = _ctx({"AAA": -0.10, "BBB": -0.20, "CCC": -0.30, "DDD": -0.40})
    assert _gen("AAA", ctx=ctx).action == "hold"


def test_the_short_threshold_is_inclusive_at_exactly_one_minus_the_fraction():
    """pct_rank == 0.75 == 1 - short_bottom_fraction. `>=` admits it; `>`
    would not, and would silently short one name fewer than configured in
    every universe whose size divides evenly."""
    assert CTX["ranks"]["DDD"] / CTX["n"] == 0.75
    assert _gen("DDD").action == "short"


def test_the_buy_threshold_is_exclusive_at_exactly_the_fraction():
    """The twin, and the asymmetry is REAL, not an oversight: the buy uses
    `<` and the short uses `>=`, so a name exactly on the buy threshold is not
    bought while a name exactly on the short threshold is shorted. Both are
    unchanged from how the long leg already read; this pins them together so
    the pair cannot be "tidied" into agreement."""
    assert CTX["ranks"]["BBB"] / CTX["n"] == 0.25
    assert _gen("BBB").action == "hold"


def test_a_mid_ranked_name_is_neither_bought_nor_shorted():
    assert _gen("CCC").action == "hold"


# ---------------------------------------------------------------------------
# The switch — 0 means no short leg, and that is the shipped-today state of
# every strategy but this one.
# ---------------------------------------------------------------------------

def test_short_bottom_fraction_zero_emits_no_short():
    params = {**PARAMS, "short_bottom_fraction": 0}
    assert _gen("DDD", params=params).action == "hold"


def test_short_bottom_fraction_absent_emits_no_short():
    """Every caller predating the short leg — and preflight's own account guard
    keys off this name being truthy, so absent must mean the same as 0."""
    params = {k: v for k, v in PARAMS.items() if k != "short_bottom_fraction"}
    assert _gen("DDD", params=params).action == "hold"


def test_the_long_leg_is_unaffected_by_the_switch_being_off():
    """The twin: turning the short leg off must not turn anything else off."""
    params = {**PARAMS, "short_bottom_fraction": 0}
    assert _gen("AAA", params=params).action == "buy"


# ---------------------------------------------------------------------------
# EXIT — cover mirrors sell, and neither may fire on the other's position.
# ---------------------------------------------------------------------------

def test_a_short_that_recovers_into_the_top_half_is_covered():
    sig = _gen("AAA", holding=True, side="short")
    assert sig.action == "cover"
    assert "covering" in sig.reason


def test_a_long_that_falls_out_of_the_top_half_is_sold():
    """The twin, and the production branch."""
    sig = _gen("DDD", holding=True, side="long")
    assert sig.action == "sell"
    assert "faded" in sig.reason


def test_a_short_that_is_still_weak_is_held():
    assert _gen("DDD", holding=True, side="short").action == "hold"


def test_a_long_that_is_still_strong_is_held():
    assert _gen("AAA", holding=True, side="long").action == "hold"


def test_the_exit_threshold_splits_the_two_sides_at_exactly_the_same_rank():
    """pct_rank == 0.50 == exit_below_fraction. The long uses `>=` and the
    short uses `<`, so this ONE rank is an exit for the long and a hold for the
    short — the two branches partition the line with no gap and no overlap.
    A fixture sampling either side of 0.50 could not distinguish that from
    both branches using `>=`, which would cover a short exactly when it should
    not."""
    assert CTX["ranks"]["CCC"] / CTX["n"] == 0.50
    assert _gen("CCC", holding=True, side="long").action == "sell"
    assert _gen("CCC", holding=True, side="short").action == "hold"


def test_a_held_short_is_never_shorted_again():
    """No pyramiding, and it is structural rather than conditional: the entry
    branches live under `else` of `if holding`, so a held name cannot reach
    them at all. Without that, DDD — bottom quartile, negative momentum — would
    re-signal "short" every single cycle it stayed weak, which is exactly the
    condition under which a short is held."""
    assert _gen("DDD", holding=True, side="short").action == "hold"


def test_a_held_long_is_never_bought_again():
    """The twin."""
    assert _gen("AAA", holding=True, side="long").action == "hold"


# ---------------------------------------------------------------------------
# The record — what the ledger sees.
# ---------------------------------------------------------------------------

def test_a_short_signal_marks_its_side_in_the_indicators():
    assert _gen("DDD").indicators.get("side") == "short"


def test_a_cover_signal_marks_its_side_too():
    assert _gen("AAA", holding=True, side="short").indicators.get("side") == "short"


def test_a_long_signals_indicators_gain_no_new_key():
    """The byte-identical pin on the record. Indicators are written into
    memory/ledger.jsonl, which is append-only and is the source every view
    renders from; adding a key to the LONG path would change every row this bot
    writes from now on, for a leg that is switched off."""
    assert set(_gen("AAA").indicators) == {
        "rank", "universe", "pct_rank", "momentum_pct"}


def test_a_sell_signals_indicators_gain_no_new_key_either():
    assert set(_gen("DDD", holding=True, side="long").indicators) == {
        "rank", "universe", "pct_rank", "momentum_pct"}


# ---------------------------------------------------------------------------
# The SHIPPED config — armed, and still unable to trade.
# ---------------------------------------------------------------------------

@pytest.fixture
def shipped():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def test_the_shipped_short_fraction_mirrors_the_buy_fraction(shipped):
    """0.25 is the SYMMETRIC value, chosen before any run scored it — a
    pre-registered starting point, not a fitted result. Pinning it against
    buy_top_fraction says where it came from."""
    xs = shipped["strategies"]["xsmom"]
    assert xs["short_bottom_fraction"] == xs["buy_top_fraction"] == 0.25


def test_the_shipped_config_still_ships_xsmom_disabled(shipped):
    assert shipped["strategies"]["xsmom"]["enabled"] is False


def test_no_other_shipped_strategy_configures_a_short_leg(shipped):
    """The 130/30 design confines shorts to xsmom so the leg is attributable
    and one flag reverts it. A second strategy picking up
    short_bottom_fraction would break both properties silently."""
    shorting = [n for n, p in shipped["strategies"].items()
                if (p or {}).get("short_bottom_fraction")]
    assert shorting == ["xsmom"]


def test_the_armed_but_disabled_leg_cannot_abort_a_cycle_on_a_cash_account(shipped):
    """The live-safety proof, and the reason arming the fraction now is not a
    production change. preflight's account guard convicts an ENABLED strategy
    configured to short; xsmom is disabled, so a broker with shorting off still
    starts. If this ever goes red, the live bot stops trading entirely."""
    assert preflight.run_account_checks(
        shipped, {"shorting_enabled": False}) == []


def test_the_same_guard_still_fires_once_the_leg_is_enabled(shipped):
    """The twin, and the thing that makes the test above a measurement rather
    than a vacuous pass: flip enabled and the refusal appears."""
    shipped["strategies"]["xsmom"]["enabled"] = True
    fails = preflight.run_account_checks(shipped, {"shorting_enabled": False})
    assert any("xsmom" in f for f in fails)
