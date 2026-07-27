"""§32's arms must differ in ONE thing: the strategy.

Why this file exists
--------------------
A factor gate is only measuring a factor if everything else is held still. Two
ways this one could quietly measure something else:

1. **Unequal risk blocks.** The shipped config sizes at `risk_per_trade_pct:
   8.0`, which is impossible for a 50-name book — cash runs out around position
   twelve. If the baseline kept 8.0 while candidates ran at 2.0, §32 would be
   measuring a sizing change wearing a factor's clothes, and the write-up would
   credit the factor.

2. **Leftover incumbent strategies.** If a candidate arm's strategy override
   MERGED into the shipped block instead of replacing it, `ma_crossover` and
   `tsmom` would still be trading underneath. A momentum arm would then be
   scored on trades momentum never generated.

Neither would raise, fail a check, or look wrong in the output. Both would
produce a clean, plausible, wrong verdict — which is the failure mode this repo
keeps finding and is the reason §32's runner is committed rather than run as a
heredoc.

The arm definitions themselves are pre-registered in backtest_candidates.md
§32. These tests assert the runner implements what was registered; they do not
get to change it.
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_runner():
    path = os.path.join(ROOT, "scripts", "gate_wide_universe.py")
    spec = importlib.util.spec_from_file_location("gate_wide_universe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_runner()


@pytest.fixture(scope="module")
def base_cfg():
    import yaml
    with open(os.path.join(ROOT, "config.yaml")) as f:
        return yaml.safe_load(f)


# ---- the registered family, unchanged ----

def test_the_arm_family_matches_the_registration(gate):
    """K in the Bonferroni correction is len(ARMS). Adding a seventh arm after
    the fact without re-registering would weaken the correction silently."""
    names = [n for n, _ in gate.ARMS]
    assert names == ["baseline", "xsmom-12-1-10", "xsmom-12-1-20",
                     "xsmom-6-1-10", "lowvol-60-10", "both-10"]
    assert names[0] == "baseline", "baseline must be first"


def test_the_registration_and_the_runner_agree_on_the_arms(gate):
    """The section is the contract; the script implements it."""
    with open(os.path.join(ROOT, "knowledge",
                           "backtest_candidates.md")) as f:
        doc = f.read()
    section = doc[doc.index("## §32 —"):]
    for name, _ in gate.ARMS:
        assert name in section, f"{name} is in the runner but not registered"


# ---- property 1: one risk block ----

def test_every_arm_gets_the_identical_risk_block(gate, base_cfg):
    """The one that stops this measuring a sizing change."""
    blocks = [gate.cfg_for(base_cfg, over)["risk"] for _, over in gate.ARMS]
    first = blocks[0]
    for key in gate.SHARED_RISK:
        values = {b[key] for b in blocks}
        assert len(values) == 1, f"{key} differs across arms: {values}"
        assert first[key] == gate.SHARED_RISK[key]


def test_the_baseline_is_not_left_on_the_shipped_sizing(gate, base_cfg):
    """Boundary pair against the bug: the shipped 8.0 must NOT survive into the
    baseline arm, or the baseline is a different experiment from its rivals."""
    assert base_cfg["risk"]["risk_per_trade_pct"] == 8.0, (
        "shipped config changed — re-check that §32's shared block still "
        "differs from it, which is the whole point of this test")
    baseline = gate.cfg_for(base_cfg, None)
    assert baseline["risk"]["risk_per_trade_pct"] == 2.0


def test_the_swing_guard_is_not_touched_by_any_arm(gate, base_cfg):
    """Invariant 3. §32 may move sizing and caps; it may not turn this into a
    day-trading test."""
    for _, over in gate.ARMS:
        cfg = gate.cfg_for(base_cfg, over)
        assert cfg["risk"]["min_holding_days"] >= 2
        assert cfg["strategy"]["timeframe"] == "1Day"


def test_the_shared_block_does_not_disable_a_safety_rail(gate, base_cfg):
    """Raising heat and the trade cap is registered. Zeroing the drawdown
    breaker or the loss limit is not."""
    for _, over in gate.ARMS:
        r = gate.cfg_for(base_cfg, over)["risk"]
        assert r["max_drawdown_pct"] > 0
        assert r["daily_loss_limit_pct"] > 0
        assert r["max_portfolio_heat_pct"] > 0


# ---- property 2: candidate arms run ONLY their own strategies ----

def test_a_candidate_arm_replaces_the_incumbent_rather_than_merging(gate, base_cfg):
    """If this merged, ma_crossover and tsmom would still be trading underneath
    a 'momentum' arm and the write-up would credit momentum for their trades."""
    for name, over in gate.ARMS:
        cfg = gate.cfg_for(base_cfg, over)
        if over is None:
            continue
        assert set(cfg["strategies"]) == set(over), (
            f"{name} carries strategies it did not ask for: "
            f"{set(cfg['strategies']) - set(over)}")


def test_each_candidate_enables_exactly_what_its_name_says(gate, base_cfg):
    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import strategies
    expected = {
        "xsmom-12-1-10": {"xsmom"}, "xsmom-12-1-20": {"xsmom"},
        "xsmom-6-1-10": {"xsmom"}, "lowvol-60-10": {"lowvol"},
        "both-10": {"xsmom", "lowvol"},
    }
    for name, over in gate.ARMS:
        if over is None:
            continue
        cfg = gate.cfg_for(base_cfg, over)
        on = {n for n, _ in strategies.enabled(cfg)}
        assert on == expected[name], f"{name}: enabled {on}"


def test_the_baseline_runs_the_shipped_ensemble(gate, base_cfg):
    """The baseline's job is to isolate the factor, so it must be the incumbent
    ensemble on the wide universe — not an empty config that trades nothing."""
    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import strategies
    on = {n for n, _ in strategies.enabled(gate.cfg_for(base_cfg, None))}
    assert on, "baseline enables no strategies — it would trade nothing"
    assert on == {n for n, _ in strategies.enabled(base_cfg)}


def test_cfg_for_does_not_mutate_the_config_it_was_given(gate, base_cfg):
    """Six arms run in a loop off one base config. An in-place update would
    make arm N inherit arm N-1's overrides — and the results would look
    plausible."""
    import copy
    before = copy.deepcopy(base_cfg)
    for _, over in gate.ARMS:
        gate.cfg_for(base_cfg, over)
    assert base_cfg == before


# ---- the pass mark is the registered one ----

def test_the_significance_test_is_the_edge_test_not_the_capacity_one(gate):
    """§27 was a CAPACITY claim and used comp.not_worse. §32 is an EDGE claim
    and must use comp.significant — the weaker test would pass an arm that
    merely failed to be worse."""
    src = open(os.path.join(ROOT, "scripts", "gate_wide_universe.py")).read()
    assert "comp.significant" in src
    assert "comp.not_worse" not in src
    assert "n_comparisons=len(ARMS)" in src


def test_the_trade_floor_is_the_registered_thirty(gate):
    src = open(os.path.join(ROOT, "scripts", "gate_wide_universe.py")).read()
    assert "n_trades >= 30" in src, (
        "§32 registered a 30-trade floor, raised from §31's 15")
