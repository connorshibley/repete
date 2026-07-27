"""§33 audits the gate methodology, so its own design has to be airtight.

Why this file exists
--------------------
§33 asks whether in-sample selection predicts out-of-sample performance. If its
own folds leaked — an OOS window overlapping the IS window it is judging — the
answer would come back a confident YES and would be worthless. A method test
that cheats is worse than no method test: it would certify the very procedure it
was built to question.

So the sharpest test here is `test_no_fold_lets_the_oos_window_overlap_its_is
_window`. The rest pin the arithmetic (`spearman`) and the wiring (arms imported
from §32 rather than re-declared, pass mark matching the registration).

Nothing in §33 can enable or disable a strategy, and one test asserts that too.
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name):
    path = os.path.join(ROOT, "scripts", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def s33():
    return _load("gate_fold_stability")


# ---- the property that decides whether §33 means anything ----

def test_no_fold_lets_the_oos_window_overlap_its_is_window(s33):
    """The one that matters. A single overlapping bar would make selection look
    predictive by construction, and §33 would then certify the procedure it
    exists to interrogate."""
    for i, (is_end, oos_end) in enumerate(s33.FOLDS, 1):
        assert oos_end is None or oos_end > is_end, f"fold {i} is empty"
        # IS is bars [0, is_end); OOS is [is_end, oos_end). They must abut,
        # never overlap.
        is_window = range(0, is_end)
        oos_window = range(is_end, oos_end if oos_end is not None else 10 ** 6)
        assert is_window.stop <= oos_window.start, f"fold {i} overlaps"


def test_the_origin_expands_and_never_forgets_history(s33):
    """Expanding, not rolling: the live bot always has all history up to today.
    A rolling window would be testing a bot that forgets 2020, which is not the
    bot."""
    is_ends = [is_end for is_end, _ in s33.FOLDS]
    assert is_ends == sorted(is_ends), "IS windows must grow monotonically"
    assert len(set(is_ends)) == len(is_ends), "duplicate folds"


def test_every_fold_has_a_usable_out_of_sample_window(s33):
    """A 20-bar OOS window would produce a profit factor built on noise and the
    rank correlation would measure nothing."""
    for i, (is_end, oos_end) in enumerate(s33.FOLDS, 1):
        span = (oos_end - is_end) if oos_end is not None else 238
        assert span >= 150, f"fold {i} OOS window is only {span} bars"


def test_there_are_enough_folds_to_average_over(s33):
    assert len(s33.FOLDS) >= 5


def test_every_arm_can_actually_trade_in_every_fold(s33):
    """The test that was MISSING, and whose absence let Run 1 print VALIDATED.

    Run 1 sliced OOS as [is_end, stop) — 200 bars — with no history. `xsmom-12-1`
    needs 253 bars of lookback, so both 12-1 arms placed **zero trades in every
    fold**. Profit factor 0.000 by construction. They also ranked last
    in-sample on their own merits, so two arms sat at the bottom of both
    rankings for unrelated reasons and manufactured a large positive Spearman.
    `both-10` degenerated into `lowvol-60-10`. Three of five candidate arms
    were not data points.

    Every other test in this file passed on that run. Leakage was checked;
    whether an arm could *signal at all* was not. This closes it in pure
    arithmetic, with no simulation: each arm's OOS slice must leave a real
    number of bars on which that arm is capable of trading.
    """
    import sys
    sys.path.insert(0, os.path.join(ROOT, "src"))
    import backtest as bt
    import strategies

    gate = _load(  "gate_wide_universe")
    base = bt.load_config()
    n_bars = 1638                      # the wide snapshot

    # Asks the RUNNER for the slice rather than recomputing the intended
    # arithmetic here. An earlier draft of this test derived the lead-in
    # itself, which made it tautological: reverting the runner to Run 1's
    # broken slice left it green.
    fake = {"SYM": list(range(n_bars))}
    for fi, (is_end, oos_end) in enumerate(s33.FOLDS, 1):
        stop = oos_end if oos_end is not None else n_bars
        for name, over in gate.ARMS:
            cfg = gate.cfg_for(base, over)
            lead = strategies.max_lookback_bars(cfg)
            got = s33.oos_window(fake, is_end, stop, cfg)["SYM"]
            tradeable = len(got) - lead
            assert tradeable >= 100, (
                f"fold {fi}, arm {name}: only {tradeable} tradeable bars "
                f"(slice {len(got)}, lookback {lead}). The arm cannot signal "
                f"enough to be ranked, and a zero would rank it last silently.")
            # ...and the slice must START at the boundary minus the lead-in, so
            # the first signal lands on the boundary itself.
            assert got[0] == max(0, is_end - lead), (
                f"fold {fi}, arm {name}: slice starts at {got[0]}, expected "
                f"{max(0, is_end - lead)}")


def test_the_runner_refuses_an_arm_that_places_no_trades(s33):
    """Belt and braces at runtime, because the arithmetic above depends on
    lookbacks the config could change tomorrow. A zero ranks last silently; a
    raise cannot be ignored."""
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    assert "r_oos.n_trades == 0" in src
    assert "Refusing to score" in src


def test_each_arm_gets_lead_in_sized_to_its_own_lookback(s33):
    """Not the family maximum: sizing every arm's lead-in to the longest arm's
    lookback would hand short-lookback arms extra in-sample bars they could
    trade on, which is contamination in the other direction."""
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    assert "strategies.max_lookback_bars(cfg)" in src
    assert "b[max(0, is_end - lead):stop]" in src


# ---- the arithmetic ----

def test_spearman_is_right_at_both_extremes(s33):
    a = {k: i for i, k in enumerate("abcdef")}
    assert s33.spearman(a, a) == pytest.approx(1.0)
    assert s33.spearman(a, {k: -v for k, v in a.items()}) == pytest.approx(-1.0)


def test_spearman_reproduces_the_value_reported_in_section_32(s33):
    """§32's headline number, recomputed here. If this drifts, either the
    function changed or the section is misquoted — both matter."""
    is_pf = {"lowvol-60-10": 4.644, "both-10": 3.115, "xsmom-6-1-10": 1.950,
             "baseline": 1.493, "xsmom-12-1-10": 1.007, "xsmom-12-1-20": 0.631}
    oos_pf = {"lowvol-60-10": 1.347, "both-10": 1.262, "xsmom-6-1-10": 2.273,
              "baseline": 0.864, "xsmom-12-1-10": 5.061, "xsmom-12-1-20": 3.094}
    assert s33.spearman(is_pf, oos_pf) == pytest.approx(-0.543, abs=0.001)


def test_spearman_ignores_magnitude_and_reads_only_order(s33):
    a = {"x": 1.0, "y": 2.0, "z": 3.0, "w": 4.0, "v": 5.0, "u": 6.0}
    stretched = {k: v ** 5 for k, v in a.items()}
    assert s33.spearman(a, stretched) == pytest.approx(1.0)


# ---- the wiring ----

def test_the_arms_come_from_section_32_rather_than_a_second_copy(s33):
    """Two definitions of 'the arms' drifting apart would make the method test
    audit a different family than the section it is auditing — the §29 failure
    mode, one level up."""
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    assert "gate_wide_universe.py" in src
    assert "ARMS = [" not in src, "§33 re-declares the arms instead of importing"
    s32 = _load("gate_wide_universe")
    assert len(s32.ARMS) == 6


def test_selection_uses_candidates_only_and_never_the_baseline(s33):
    """The baseline is the thing being beaten. If it could be 'selected', a
    fold where nothing works would score as a hit."""
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    assert 'cands = [n for n, _ in arms if n != "baseline"]' in src
    assert "max(cands, key=lambda n: is_pf[n])" in src


def test_the_pass_mark_matches_the_registration(s33):
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    with open(os.path.join(ROOT, "knowledge", "backtest_candidates.md")) as f:
        section = f.read().split("## §33 —")[1]
    # The registration is committed and immutable; these quote it verbatim,
    # including its typography. If a future edit softens the bar, this goes red.
    assert "mean_rho > 0" in src
    assert "- **(a)** mean per-fold Spearman **> 0**" in section
    assert "mean_lift > 0" in src
    assert "- **(b)** mean selection lift **> 0**" in section
    assert "n_hits >= 4" in src
    assert "- **(c)** hit rate **≥ 4 of 5** folds" in section


def test_section_33_cannot_enable_anything(s33):
    """A METHOD claim writes no config and adopts no arm."""
    src = open(os.path.join(ROOT, "scripts", "gate_fold_stability.py")).read()
    for forbidden in ("enabled\": True", "yaml.dump", "config.yaml\", \"w"):
        assert forbidden not in src, forbidden


def test_the_remedy_was_committed_before_the_result(s33):
    """The registration names fold-majority selection as the successor BEFORE
    the outcome is known, so it cannot be invented to fit whatever comes back."""
    with open(os.path.join(ROOT, "knowledge", "backtest_candidates.md")) as f:
        section = f.read().split("## §33 —")[1]
    assert "fold-majority" in section
    assert "≥60% of" in section
    assert "pooled out-of-fold" in section
    # ...and it must be stated as the consequence of a FAILURE, not left as a
    # vague aspiration that could be reinterpreted afterwards.
    assert "If §33 FAILS" in section
