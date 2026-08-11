"""§62 — unloved-sector swing on the SPDR funds, `strategies/swing_sectors.py`.

The strategy ships `enabled: false`, so nothing here can change live
behaviour. What these tests protect is the meaning of what §62 will measure,
and the two properties the intraday scanner leans on:

  * `assess()` is the SINGLE implementation of the entry conditions and zone —
    `generate()` (the cycle, the gate) and `swing_scan.py` (the live quote)
    both call it, so a condition tested here is tested for every caller;
  * exits never depend on the cross-section, so a held fund that leaves the
    universe or the ranking can always still be exited (the unexitable-position
    trap `strategies.prepare_one` documents).
"""
import copy
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

sys.path.insert(0, "src")
import strategies                                             # noqa: E402
from strategies import swing_sectors                          # noqa: E402

with open("config.yaml") as _f:
    CFG = yaml.safe_load(_f)

PARAMS = CFG["strategies"]["swing_sectors"]


def bars_from_closes(closes, spread=0.5):
    """Bars at ±spread around each close, with real ISO `ts` values — the key
    the live fetch writes and `_held_days` reads (meanrev's contract)."""
    t0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [{"open": c, "high": c + spread, "low": c - spread, "close": c,
             "volume": 1_000_000, "ts": (t0 + timedelta(days=i)).isoformat()}
            for i, c in enumerate(closes)]


def decline(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def deep_stabilized(n_peak=60, n_fall=160, n_base=60):
    """Peak at 130, fall to 78 (~40% drawdown), then a gently RISING base at
    ~78-80: every entry condition except the price test holds at the end."""
    closes = ([130.0] * n_peak + decline(130.0, 78.0, n_fall)
              + decline(78.0, 80.0, n_base))
    return bars_from_closes(closes)


def falling_knife():
    """Same depth, still falling at the end — condition 2 must refuse it."""
    return bars_from_closes([130.0] * 60 + decline(130.0, 78.0, 220))


def shallow(px=100.0):
    """Flat near its high — never a laggard at min_drawdown_pct 12."""
    return bars_from_closes([px] * 280)


def universe(extra=None):
    """A rankable cross-section: 1 candidate, 1 knife, 5 shallow funds."""
    bars = {"CAND": deep_stabilized(), "KNIFE": falling_knife()}
    for i in range(5):
        bars[f"FLAT{i}"] = shallow(100.0 + i)
    bars.update(extra or {})
    return bars


def ctx(extra=None):
    """Cross-section over the standard universe, with `extra` symbols merged
    in (an entry named like an existing fixture OVERRIDES it)."""
    return swing_sectors.prepare(universe(extra), PARAMS, CFG)


# ------------------------------------------------------------- drawdown_pct

def test_a_fund_AT_its_high_has_zero_drawdown():
    bars = bars_from_closes([100.0] * 260, spread=0.0)
    assert swing_sectors.drawdown_pct(bars, 252) == pytest.approx(0.0)


def test_drawdown_is_measured_from_the_window_HIGH_not_the_close():
    bars = bars_from_closes([200.0] * 10 + [100.0] * 250, spread=0.0)
    assert swing_sectors.drawdown_pct(bars, 252) == pytest.approx(50.0)


def test_a_peak_outside_the_window_is_not_the_high():
    """252 bars of 100 after the 200-peak aged out: drawdown must read ~0,
    not 50 — a statistic over 'all history' would never let a sector finish
    repairing."""
    bars = bars_from_closes([200.0] * 10 + [100.0] * 252, spread=0.0)
    assert swing_sectors.drawdown_pct(bars, 252) == pytest.approx(0.0)


def test_insufficient_history_is_None_never_a_number():
    assert swing_sectors.drawdown_pct(bars_from_closes([100.0] * 50), 252) is None


# ------------------------------------------------------------------ prepare

def test_laggards_are_the_deepest_and_clear_the_floor():
    c = ctx()
    assert set(c["laggards"]) == {"CAND", "KNIFE"}, (
        "only the two deep-drawdown fixtures qualify — if a FLAT symbol is "
        "here the min_drawdown_pct floor is dead; if CAND/KNIFE are missing "
        "the fixture no longer builds a 40% drawdown and every entry test "
        "below is vacuous")
    assert c["drawdown"]["KNIFE"] > 12.0 and c["drawdown"]["CAND"] > 12.0


def test_the_deepest_fund_ranks_first():
    c = ctx()
    assert c["laggards"][0] == "KNIFE"       # still falling => deeper than CAND


def test_insufficient_history_is_excluded_from_the_ranking():
    c = ctx({"YOUNG": bars_from_closes([50.0] * 40)})
    assert "YOUNG" not in c["drawdown"]
    assert c["n"] == 7, "the 7 standard fixtures must still rank"


def test_the_floor_can_empty_the_laggard_list():
    """A universe of shallow funds nominates NOTHING. Without min_drawdown_pct
    the top-4 rule would always nominate something — the hi52 min_proximity
    lesson pointing the other way."""
    bars = {f"FLAT{i}": shallow(100.0 + i) for i in range(7)}
    assert swing_sectors.prepare(bars, PARAMS, CFG)["laggards"] == []


# ------------------------------------------------------------------- assess

def test_assess_arms_the_stabilized_laggard():
    zone = swing_sectors.assess("CAND", universe()["CAND"], PARAMS, ctx())
    assert zone is not None
    assert zone["zone_low"] == pytest.approx(zone["sma"])
    assert zone["zone_high"] == pytest.approx(
        zone["sma"] + PARAMS["entry_zone_atr_mult"] * zone["atr"])


def test_assess_refuses_the_falling_knife():
    """KNIFE is the DEEPEST laggard and still refused: below its base SMA.
    Vacuity guard on the fixture — it must be in the laggard list, or this
    test would pass because the symbol never ranked at all."""
    c = ctx()
    assert "KNIFE" in c["laggards"]
    assert swing_sectors.assess("KNIFE", universe()["KNIFE"], PARAMS, c) is None


def test_assess_refuses_a_non_laggard():
    c = ctx()
    assert c["drawdown"]["FLAT0"] < PARAMS["min_drawdown_pct"]
    assert swing_sectors.assess("FLAT0", universe()["FLAT0"], PARAMS, c) is None


def test_assess_refuses_a_thin_cross_section():
    """min_ranked is an outage floor: 2 rankable funds is a data problem, not
    a market reading."""
    bars = {"CAND": deep_stabilized(), "FLAT0": shallow()}
    thin = swing_sectors.prepare(bars, PARAMS, CFG)
    assert thin["n"] < PARAMS["min_ranked"]
    assert swing_sectors.assess("CAND", bars["CAND"], PARAMS, thin) is None


def test_assess_refuses_a_missing_cross_section():
    assert swing_sectors.assess("CAND", universe()["CAND"], PARAMS, None) is None


def test_assess_refuses_a_falling_base():
    """Close above the SMA but the SMA itself lower than base_slope_bars ago:
    a ONE-BAR pop inside a decline, not a base. (A multi-bar flat pop would
    itself drag the SMA up and pass the slope test — the first version of
    this fixture did exactly that and tested nothing.)"""
    closes = [130.0] * 60 + decline(130.0, 78.0, 219) + [84.0]
    bars = bars_from_closes(closes)
    c = ctx({"POP": bars})
    if "POP" not in c["laggards"]:               # fixture guard, not outcome
        pytest.fail("POP fixture fell out of the laggard ranking — the "
                    "falling-base condition was never reached")
    from strategies.base import sma
    base_now = sma(closes, PARAMS["stabilize_sma_period"])
    assert closes[-1] > base_now, \
        "fixture must be ABOVE its SMA so only the slope test can refuse it"
    assert base_now < sma(closes[:-PARAMS["base_slope_bars"]],
                          PARAMS["stabilize_sma_period"]), \
        "fixture's SMA must actually be falling"
    assert swing_sectors.assess("POP", bars, PARAMS, c) is None


# ------------------------------------------------------------------ entries

def test_a_close_inside_the_zone_is_a_buy():
    bars = universe()["CAND"]
    sig = swing_sectors.generate("CAND", bars, PARAMS, holding=False,
                                 cross_section=ctx())
    assert sig.action == "buy"
    assert sig.indicators["zone_low"] <= sig.indicators["close"] \
        <= sig.indicators["zone_high"]


def test_a_close_ABOVE_the_zone_is_armed_not_bought():
    """Extended above the base = chasing the bounce; the strategy waits for
    the pullback. This armed state is exactly what swing_scan watches."""
    bars = universe()["CAND"]
    bars = bars[:-1] + [dict(bars[-1], close=86.0, high=86.5, low=85.5)]
    sig = swing_sectors.generate("CAND", bars, PARAMS, holding=False,
                                 cross_section=ctx({"CAND": bars}))
    assert sig.action == "hold"
    assert "armed" in sig.reason
    assert sig.indicators["close"] > sig.indicators["zone_high"], \
        "fixture must actually sit above the zone or this asserts nothing"


def test_no_conditions_no_entry():
    sig = swing_sectors.generate("FLAT0", universe()["FLAT0"], PARAMS,
                                 holding=False, cross_section=ctx())
    assert sig.action == "hold"


# -------------------------------------------------------------------- exits

def _hold(bars, entry_ts=None, cross_section=None):
    return swing_sectors.generate("HELD", bars, PARAMS, holding=True,
                                  cross_section=cross_section,
                                  entry_ts=entry_ts)


def test_recovered_drawdown_exits():
    bars = bars_from_closes([130.0] * 60 + decline(130.0, 90.0, 100)
                            + decline(90.0, 126.0, 120))
    dd = swing_sectors.drawdown_pct(bars, PARAMS["high_lookback_bars"])
    assert dd <= PARAMS["recovered_drawdown_pct"], "fixture must have recovered"
    sig = _hold(bars)
    assert sig.action == "sell" and "thesis complete" in sig.reason


def test_a_failed_base_exits():
    bars = bars_from_closes([100.0] * 270 + decline(100.0, 90.0, 10))
    sig = _hold(bars)
    assert sig.action == "sell" and "stabilization failed" in sig.reason


def test_the_time_stop_exits():
    bars = bars_from_closes([130.0] * 60 + decline(130.0, 100.0, 100)
                            + [100.0] * 120)
    late = (datetime.fromisoformat(bars[-1]["ts"])
            - timedelta(days=PARAMS["max_hold_days"])).isoformat()
    sig = _hold(bars, entry_ts=late)
    assert sig.action == "sell" and "time stop" in sig.reason


def test_a_young_position_with_no_exit_condition_holds():
    bars = bars_from_closes([130.0] * 60 + decline(130.0, 100.0, 100)
                            + [100.0] * 120)
    recent = (datetime.fromisoformat(bars[-1]["ts"])
              - timedelta(days=3)).isoformat()
    assert _hold(bars, entry_ts=recent).action == "hold"


def test_exits_need_no_cross_section():
    """The unexitable-position trap: every exit above ran with
    cross_section=None. Pin it explicitly — a held fund that has left the
    ranking must still be closeable."""
    bars = bars_from_closes([130.0] * 60 + decline(130.0, 90.0, 100)
                            + decline(90.0, 126.0, 120))
    assert _hold(bars, cross_section=None).action == "sell"


# ------------------------------------------------- contract and shipped config

def test_the_dispatch_passes_entry_ts():
    """NEEDS_ENTRY_TS is declared, so `strategies.generate` must thread
    entry_ts through — the max-hold rule measures nothing otherwise (the
    `if name == "meanrev"` trap this flag exists to prevent)."""
    assert swing_sectors.NEEDS_ENTRY_TS is True
    bars = bars_from_closes([130.0] * 60 + decline(130.0, 100.0, 100)
                            + [100.0] * 120)
    late = (datetime.fromisoformat(bars[-1]["ts"])
            - timedelta(days=PARAMS["max_hold_days"])).isoformat()
    cfg = copy.deepcopy(CFG)
    sig = strategies.generate("swing_sectors", "HELD", bars, cfg,
                              holding=True, entry_ts=late)
    assert sig.action == "sell" and "time stop" in sig.reason


def test_required_lookback_matches_the_drawdown_window():
    assert swing_sectors.required_lookback(PARAMS) \
        == PARAMS["high_lookback_bars"] + 1


def test_shipped_disabled_and_on_its_own_universe():
    assert PARAMS["enabled"] is False, \
        "enabling swing_sectors is a §62 owner decision, not a code change"
    assert PARAMS["universe"] == strategies.SECTOR_ETFS_UNIVERSE
    assert strategies.universe_for(CFG, "swing_sectors") \
        == set(CFG["sector_etfs"])
    assert len(CFG["sector_etfs"]) == 11


def test_the_core_universe_did_not_widen():
    """The reason sector_etfs is its own list: adding a fund for swing must
    never hand the three gated strategies new names."""
    assert len(CFG["symbols"]) == 38
    for name in ("ma_crossover", "tsmom", "meanrev"):
        assert strategies.universe_for(CFG, name) == set(CFG["symbols"])


# --------------------------------------------------- preflight convictions

def _preflight(mutate):
    import preflight
    cfg = copy.deepcopy(CFG)
    mutate(cfg)
    return preflight.run(cfg)


def test_preflight_accepts_the_shipped_sector_etfs():
    fails = _preflight(lambda c: None)
    assert not any("sector_etfs" in f for f in fails)


def test_preflight_convicts_a_duplicate_sector_etf():
    """universe_for builds a SET, so a duplicate collapses without a sound —
    the cross-section quietly shrinks below what the gate was run on."""
    fails = _preflight(lambda c: c["sector_etfs"].append("XLE"))
    assert any("duplicate" in f and "sector_etfs" in f for f in fails)


def test_preflight_convicts_a_fund_that_is_also_a_stock_sector_member():
    """A basket ranked as a peer of its own constituents is the §49 category
    error arriving through a config edit — reclaim would score XLE inside
    the Energy sector median."""
    fails = _preflight(lambda c: c["sectors"]["Energy"].append("XLE"))
    assert any("§49" in f or "constituents" in f for f in fails)


def test_preflight_convicts_the_universe_key_with_no_list():
    fails = _preflight(lambda c: c.pop("sector_etfs"))
    assert any("no\nsector_etfs" in f.replace("no ", "no\n") or
               "sector_etfs: list is configured" in f for f in fails)


def test_preflight_still_convicts_an_unknown_universe_key():
    fails = _preflight(
        lambda c: c["strategies"]["swing_sectors"].__setitem__(
            "universe", "sectr_etfs"))
    assert any("not a known" in f for f in fails)


def test_the_swing_stop_is_wider_than_the_global_one():
    """'Accept volatility' lives HERE, per-trade — pinned so a later edit
    cannot quietly make the swing stop tighter than the default while the
    docstrings keep claiming otherwise."""
    assert PARAMS["stop_atr_mult"] > CFG["risk"]["brackets"]["stop_atr_mult"]
