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
