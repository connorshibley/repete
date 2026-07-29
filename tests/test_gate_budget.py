"""§27 gate runner (scripts/gate_budget.py) — the parts that decide a verdict.

Two properties are load-bearing and neither is obvious from reading the script:

1. **Arms move BOTH knobs.** The effective budget is set by
   `risk_per_trade_pct` (notional path: tsmom, ma_crossover) AND
   `max_order_value_usd` (which clamps the risk_sizing path: meanrev — §11
   found risk_pct 1.0/2.0/5.0 byte-identical there for exactly this reason).
   An arm that moved one knob would silently test one strategy.

2. **The monotonicity check is the §23 trap-detector.** A lone interior peak is
   how §23 was caught. If this function is wrong, the trap is unguarded.
"""
import importlib.util
import os



def load():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "gate_budget.py")
    spec = importlib.util.spec_from_file_location("gate_budget", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gb = load()


# ---------------- the arm grid ----------------

def test_baseline_is_first_and_matches_the_shipped_config():
    """A LIVE gate harness must measure against the bot that actually exists.

    §29 (2026-07-26) retired this one: it tests two levers that no longer hold
    their §27 values. The pre-registered ARMS grid is deliberately NOT edited to
    match — rewriting a pre-registration after the fact to keep a test green
    would falsify the record that gives every verdict in
    backtest_candidates.md its weight.

    So the guard becomes conditional rather than deleted. While a harness is
    live, its baseline must equal the shipped config; once superseded, it must
    say so out loud. What stays forbidden is the silent third state — a harness
    still presenting itself as current while measuring a bot that no longer
    exists.
    """
    import backtest as bt
    name, rpt, cap = gb.ARMS[0]
    assert name == "baseline"

    superseded = getattr(gb, "SUPERSEDED_BY", None)
    if superseded:
        assert isinstance(superseded, str) and superseded.strip(), (
            "SUPERSEDED_BY must NAME what replaced this gate, not just be truthy")
        return

    cfg = bt.load_config()
    assert rpt == cfg["risk"]["risk_per_trade_pct"]
    assert cap == cfg["risk"]["max_order_value_usd"], (
        "the baseline arm must BE the live config; if it drifts, every arm is "
        "measured against a bot that does not exist")


def test_every_candidate_arm_raises_the_budget():
    _, base_rpt, base_cap = gb.ARMS[0]
    for name, rpt, cap in gb.ARMS[1:]:
        assert rpt > base_rpt, f"{name} does not raise risk_per_trade_pct"
        assert cap >= base_cap, f"{name} lowers the order cap"


def test_the_cap_never_clamps_the_notional_budget_in_a_candidate_arm():
    """If max_order_value_usd stays below equity x risk_pct, the arm silently
    reduces to the baseline for the notional strategies — §19b's "byte-identical
    arms measured nothing" failure, in a new costume."""
    equity = 100_000.0
    for name, rpt, cap in gb.ARMS[1:]:
        assert cap >= equity * rpt / 100, (
            f"{name}: cap ${cap} clamps a ${equity * rpt / 100:,.0f} budget")


def test_arm_names_are_unique():
    names = [n for n, _, _ in gb.ARMS]
    assert len(names) == len(set(names))


# ---------------- cfg_for ----------------

def test_cfg_for_sets_both_knobs_and_does_not_mutate_the_original():
    import backtest as bt
    base = bt.load_config()
    before = (base["risk"]["risk_per_trade_pct"], base["risk"]["max_order_value_usd"])
    out = gb.cfg_for(base, 2.5, 2500)
    assert out["risk"]["risk_per_trade_pct"] == 2.5
    assert out["risk"]["max_order_value_usd"] == 2500
    assert (base["risk"]["risk_per_trade_pct"],
            base["risk"]["max_order_value_usd"]) == before, "leaked into caller's cfg"


def test_cfg_for_deep_copies_nested_risk_blocks():
    """A shallow copy would let one arm's edit bleed into the next arm."""
    import backtest as bt
    base = bt.load_config()
    a = gb.cfg_for(base, 2.0, 2000)
    b = gb.cfg_for(base, 3.0, 3000)
    a["risk"]["brackets"]["enabled"] = "TAMPERED"
    assert b["risk"]["brackets"]["enabled"] != "TAMPERED"
    assert base["risk"]["brackets"]["enabled"] != "TAMPERED"


# ---------------- monotonicity (§23) ----------------

def test_rising_series_is_monotone():
    assert gb.monotone([1.0, 2.0, 3.0, 4.0]) is True


def test_falling_series_is_monotone():
    assert gb.monotone([4.0, 3.0, 2.0, 1.0]) is True


def test_smooth_interior_peak_is_monotone():
    """Rises then falls with no reversal — a plausible real effect."""
    assert gb.monotone([1.0, 3.0, 5.0, 4.0, 2.0]) is True


def test_lone_interior_spike_is_not_monotone():
    """THE §23 signature, and the case the first draft of monotone() missed.

    This series IS unimodal — 1.0 -> 1.1 -> 9.0 then 9.0 -> 1.2 -> 1.0 — so a
    unimodality-only check passes it. That is exactly the shape §23 was caught
    by, which made the original guard decorative."""
    assert gb.monotone([1.0, 1.1, 9.0, 1.2, 1.0]) is False


def test_a_zigzag_is_not_monotone():
    assert gb.monotone([1.0, 5.0, 2.0, 6.0, 3.0]) is False


def test_peak_at_either_end_is_monotone():
    """The true optimum may sit outside the grid — a reason to widen it, not
    evidence of overfitting."""
    assert gb.monotone([9.0, 1.0, 1.1, 1.2]) is True
    assert gb.monotone([1.0, 1.1, 1.2, 9.0]) is True


def test_flat_series_is_monotone():
    assert gb.monotone([2.0, 2.0, 2.0]) is True


def test_short_series_is_accepted():
    assert gb.monotone([]) is True
    assert gb.monotone([1.0]) is True
    assert gb.monotone([1.0, 2.0]) is True


def test_negative_returns_do_not_confuse_it():
    # A genuine spike among negatives: peak beats its neighbour by 2.7 while
    # every other arm sits within 0.2 of the next.
    assert gb.monotone([-3.0, -2.9, -0.1, -2.8, -3.0]) is False
    # A gentle hump is NOT a spike: the lead (0.5) is well inside the spread
    # of the remaining arms (2.0). Smooth variation is what we want to accept.
    assert gb.monotone([-1.0, -0.5, -2.0, -3.0]) is True
    assert gb.monotone([-3.0, -2.0, -0.5, -1.0]) is True


# ---------------- the watch list ----------------

def test_watch_names_are_in_the_configured_universe():
    """§26 identified these as unreachable at the baseline budget. If one is
    later dropped from the universe, this test says so instead of the gate
    quietly reporting 'no' for a symbol it never could have traded."""
    import backtest as bt
    universe = set(bt.load_config()["symbols"])
    for w in gb.WATCH:
        assert w in universe, f"{w} is on the §27 watch list but not in symbols:"
