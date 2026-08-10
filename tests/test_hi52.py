"""§59 — 52-week-high proximity (George & Hwang 2004), `strategies/hi52.py`.

The strategy is registered DIAGNOSTIC and ships `enabled: false`, so nothing
here can change live behaviour. What these tests exist to protect is the
MEANING of the numbers §59 will produce:

  * the ranking statistic is what the paper describes, not a momentum proxy
    wearing its name — §35/§37 already rejected the momentum ranking, so a
    hi52 that secretly ranks by return would be re-running a failed experiment
    under a new label;
  * the absolute floor is real, so the rule cannot buy the least-broken name in
    a universe that is 40% off its highs;
  * and the ETF-universe scoping is explicit, because the failure mode there is
    a SILENTLY SMALLER cross-section rather than an error.
"""
import copy
import sys

import pytest
import yaml

sys.path.insert(0, "src")
import strategies                                             # noqa: E402
from strategies import hi52                                   # noqa: E402

with open("config.yaml") as _f:
    CFG = yaml.safe_load(_f)

PARAMS = CFG["strategies"]["hi52"]

PIT_13 = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
          "SPY", "DIA", "QQQ", "IWM"]
PIT_15 = PIT_13[:9] + ["XLRE", "XLC"] + PIT_13[9:]


def series(closes, high_mult=1.0):
    """Bars whose highs are `high_mult` x their closes."""
    return [{"open": c, "high": c * high_mult, "low": c * 0.99, "close": c,
             "volume": 1_000_000, "timestamp": f"2024-01-{i % 28 + 1:02d}"}
            for i, c in enumerate(closes)]


def ramp(n, start=100.0, step=0.1):
    return series([start + i * step for i in range(n)])


# ------------------------------------------------------------------ proximity

def test_a_name_AT_its_high_scores_1():
    assert hi52.proximity(ramp(252), 252) == pytest.approx(1.0)


def test_a_name_HALFWAY_down_from_its_high_scores_half():
    bars = series([200.0] * 100 + [100.0] * 152)
    assert hi52.proximity(bars, 252) == pytest.approx(0.5)


def test_the_high_is_taken_over_the_WINDOW_not_over_all_history():
    """A peak that has aged out of the 52-week window is not the 52-week high.
    If this used all available bars the statistic would drift toward an
    all-time-high rule, which is a different and much rarer signal."""
    bars = series([500.0] + [100.0] * 300)
    assert hi52.proximity(bars, 252) == pytest.approx(1.0)      # peak aged out
    assert hi52.proximity(bars, 301) == pytest.approx(0.2)      # peak included


def test_proximity_is_scale_free():
    """Why no cross-sectional standardisation is needed: the ratio already
    means the same thing on a $30 fund and a $600 one."""
    cheap = series([30.0 * (1 + i * 0.001) for i in range(252)])
    dear = series([600.0 * (1 + i * 0.001) for i in range(252)])
    assert hi52.proximity(cheap, 252) == pytest.approx(hi52.proximity(dear, 252))


def test_proximity_returns_None_rather_than_a_number():
    assert hi52.proximity(ramp(251), 252) is None
    assert hi52.proximity(series([0.0] * 252), 252) is None


def test_it_can_never_exceed_1_because_todays_high_is_in_the_window():
    """Stated in the docstring and pinned here: a value over 1.0 would mean the
    bars are malformed, so no clamp is needed and none is applied. A clamp
    would hide bad data instead of letting it show."""
    bars = ramp(252)
    assert hi52.proximity(bars, 252) <= 1.0


# -------------------------------------------------------------------- ranking

def _universe():
    """Four funds at known distances from their own highs."""
    return {
        "AT_HIGH": ramp(300),                                    # 1.00
        "NEAR": series([100.0] * 150 + [95.0] * 150),            # 0.95
        "MID": series([100.0] * 150 + [80.0] * 150),             # 0.80
        "BROKEN": series([100.0] * 150 + [50.0] * 150),          # 0.50
    }


def test_prepare_ranks_nearest_the_high_first():
    ctx = hi52.prepare(_universe(), PARAMS)
    order = sorted(ctx["ranks"], key=ctx["ranks"].get)
    assert order == ["AT_HIGH", "NEAR", "MID", "BROKEN"]
    assert ctx["n"] == 4


def test_it_ranks_by_PROXIMITY_and_not_by_RETURN():
    """The test that keeps this from being xsmom under another name. Two funds:
    one has doubled and then given half of it back, the other has crept up 2%
    and sits on its high. Momentum ranks the first higher; George & Hwang rank
    the second higher, and that inversion IS the paper's claim.

    §35 and §37 rejected the momentum ranking outright. If this module ranked
    the same way, §59 would be re-running a failed experiment with a new label
    and spending real compute to do it."""
    universe = {
        "BIG_GAIN_OFF_HIGH": series([100.0] * 100 + [200.0] * 50 + [150.0] * 150),
        "SMALL_GAIN_AT_HIGH": series([100.0 + i * 0.0067 for i in range(300)]),
    }
    ctx = hi52.prepare(universe, PARAMS)
    assert ctx["ranks"]["SMALL_GAIN_AT_HIGH"] < ctx["ranks"]["BIG_GAIN_OFF_HIGH"]
    # ... and the return ordering really is the other way round, or the test
    # above would be trivially true.
    assert (universe["BIG_GAIN_OFF_HIGH"][-1]["close"]
            > universe["SMALL_GAIN_AT_HIGH"][-1]["close"])


def test_a_symbol_with_short_history_is_EXCLUDED_not_ranked_last():
    """Same rule as xsmom's. Inventing a rank for a name with 100 bars would
    put it on one scale with a name that has three years, and it would then
    compete for the top quarter on data that cannot support the statistic."""
    universe = {**_universe(), "NEWLY_LISTED": ramp(100)}
    ctx = hi52.prepare(universe, PARAMS)
    assert "NEWLY_LISTED" not in ctx["ranks"]
    assert ctx["n"] == 4


# -------------------------------------------------------------------- signals

def _ctx(universe=None):
    return hi52.prepare(universe or _universe(), PARAMS)


def test_the_top_fraction_at_its_high_is_bought():
    sig = hi52.generate("AT_HIGH", ramp(300), PARAMS, False, _ctx())
    assert sig.action == "buy"
    assert sig.strategy == "hi52"
    assert sig.indicators["proximity_pct"] == pytest.approx(100.0, abs=0.5)


def test_the_ABSOLUTE_floor_binds_even_at_rank_one():
    """The clause that stops this buying the leaders of a bear market. Every
    member of this universe is far off its high; the best of them is still 30%
    down, and a purely relative rule would buy it.

    Without the floor, the paper's mechanism — anchoring at the high — is not
    what the code implements, and §59 would be scoring a relative-strength rule
    while the write-up claimed a George & Hwang replication."""
    broken = {name: series([100.0] * 150 + [px] * 150)
              for name, px in [("A", 70.0), ("B", 60.0), ("C", 55.0),
                               ("D", 50.0)]}
    ctx = hi52.prepare(broken, PARAMS)
    assert ctx["ranks"]["A"] == 0                      # A IS the top of the rank
    sig = hi52.generate("A", broken["A"], PARAMS, False, ctx)
    assert sig.action == "hold"
    assert sig.indicators["proximity_pct"] == pytest.approx(70.0)


def test_a_holding_that_leaves_the_top_half_is_sold():
    sig = hi52.generate("BROKEN", _universe()["BROKEN"], PARAMS, True, _ctx())
    assert sig.action == "sell"


def test_a_holding_still_inside_the_top_half_is_kept():
    sig = hi52.generate("NEAR", _universe()["NEAR"], PARAMS, True, _ctx())
    assert sig.action == "hold"


def test_an_unrankable_symbol_HOLDS_rather_than_selling():
    """A position whose symbol dropped out of the cross-section must not be
    exited on that basis — but it must also not be trapped. `prepare_one` adds
    back whatever the strategy HOLDS, which is what makes the hold safe; this
    pins the strategy's half of that contract."""
    sig = hi52.generate("UNKNOWN", ramp(300), PARAMS, True, _ctx())
    assert sig.action == "hold"
    assert "insufficient history" in sig.reason


def test_a_tiny_cross_section_produces_no_signal():
    sig = hi52.generate("A", ramp(300), PARAMS, False,
                        hi52.prepare({"A": ramp(300)}, PARAMS))
    assert sig.action == "hold"


def test_it_never_emits_a_short():
    """Long-only structurally, not by preference: `simulate()` refuses short
    signals and `risk.trail_stop` is long-only. The short leg lives in xsmom
    alone by the §53 design. Swept across every state the strategy has."""
    universe = _universe()
    ctx = _ctx(universe)
    actions = {hi52.generate(sym, universe[sym], PARAMS, holding, ctx).action
               for sym in universe for holding in (True, False)}
    assert actions <= {"buy", "sell", "hold"}
    assert not hasattr(hi52, "NEEDS_POSITION_SIDE")


# ------------------------------------------- the universe trap on ETF-only runs

def test_exclude_etfs_SHRINKS_the_certified_universe_and_does_not_error():
    """The trap §59a-d's arms must override, measured rather than asserted.

    `etfs:` names eight of the fifteen certified funds. With `exclude_etfs:
    true` the ranking silently drops to 7 of 15 — and to 5 of 13 in the earliest
    period — losing SPY, which is the benchmark those specs score against.

    It does not empty and it does not raise. n stays above the `< 4` guard, so
    the strategy runs and reports trades, and `buy_top_fraction: 0.25` quietly
    becomes "the best 1 of 7" instead of "the best 3 of 15". A different
    strategy under the same name, with nothing in the output saying so."""
    for names, kept in ((PIT_13, 5), (PIT_15, 7)):
        cfg = copy.deepcopy(CFG)
        cfg["symbols"] = names
        assert len(strategies.universe_for(cfg, "hi52")) == kept
        assert "SPY" not in strategies.universe_for(cfg, "hi52")


def test_the_arm_override_restores_the_whole_certified_universe():
    """What §59a-d actually set. Both halves are pinned, because a test of only
    the override would pass just as well if `exclude_etfs` did nothing at all."""
    for names in (PIT_13, PIT_15):
        cfg = copy.deepcopy(CFG)
        cfg["symbols"] = names
        cfg["strategies"]["hi52"]["exclude_etfs"] = False
        assert strategies.universe_for(cfg, "hi52") == set(names)


def test_the_38_name_book_still_excludes_its_own_ETFs():
    """The other direction, and the reason the default is `true`: ranking XLK
    against AAPL is a category error — the basket is partly made of the name it
    is being compared with. §49 measured 11 of 13 over-threshold correlation
    pairs involving an ETF."""
    universe = strategies.universe_for(CFG, "hi52")
    assert universe == set(CFG["symbols"]) - set(CFG["etfs"])
    assert len(universe) == 30


# ------------------------------------------------------------- shipped state

def test_it_ships_disabled():
    """§52 freezes EDGE on data/snapshots/ and §57's pre-committed reading rule
    closes data/pit/, so there is no venue where this could currently earn a
    verdict that licenses enabling it. Only the owner enables a strategy
    (CLAUDE.md invariant 2)."""
    assert CFG["strategies"]["hi52"]["enabled"] is False
    assert "hi52" not in {n for n, _ in strategies.enabled(CFG)}


def test_adding_it_does_not_change_how_much_history_live_fetches():
    """252 + 1 = 253, which is exactly what the disabled xsmom already asked
    for. If this had raised the fetch, every strategy's warmup would have moved
    and no §1-§57 number would still describe the running bot."""
    assert hi52.required_lookback(PARAMS) == 253
    assert strategies.max_lookback_bars(CFG) == 253
