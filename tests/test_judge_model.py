"""The simulated judge (§29) — the backtest half of divergence #8.

The tests that matter most here are the two structural ones at the bottom.
Everything else checks the distribution; those two check that the fix cannot
itself become the next divergence.
"""
import json
import os

import pytest

import judge_model


CAL = {
    "n_judged_buys": 146,
    "n_sized": 144,
    "min_sample": 50,
    "veto_rate": 0.013699,
    "downsize_rate": 0.534722,
    "mean_scale": 0.752083,
    "scale_histogram": {"0.4": 5, "0.5": 49, "0.6": 15, "0.7": 6,
                        "0.8": 2, "1.0": 67},
}


@pytest.fixture(autouse=True)
def _clear_cache():
    judge_model._cache = None
    yield
    judge_model._cache = None


@pytest.fixture
def cal_file(tmp_path, monkeypatch):
    p = tmp_path / "judge_calibration.json"
    p.write_text(json.dumps(CAL))
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(p))
    return p


def _cfg(enabled=True, salt="judge-v1"):
    return {"backtest": {"judge_model": {"enabled": enabled, "salt": salt}}}


# ---- the off switch ----

def test_disabled_is_a_perfect_no_op(cal_file):
    """Every gate run before 2026-07-26 must still reproduce."""
    for sym in ("SPY", "LLY", "AMD"):
        assert judge_model.scale_for(sym, "2025-03-04", _cfg(enabled=False)) == 1.0
        assert judge_model.apply(37, sym, "2025-03-04", _cfg(enabled=False)) == 37


def test_disabled_does_not_even_need_a_calibration(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(tmp_path / "nope.json"))
    assert judge_model.apply(10, "SPY", "2025-01-02", _cfg(enabled=False)) == 10


# ---- the distribution ----

def test_downsize_rate_matches_the_live_ledger(cal_file):
    """The whole point: sim must cut about as often as live does."""
    scales = [judge_model.scale_for("SYM%d" % i, "2025-01-%02d" % (i % 28 + 1),
                                    _cfg()) for i in range(4000)]
    cut = sum(1 for s in scales if 0.0 < s < 1.0) / len(scales)
    assert abs(cut - CAL["downsize_rate"]) < 0.03, cut


def test_mean_scale_matches_the_live_ledger(cal_file):
    scales = [judge_model.scale_for("SYM%d" % i, "2025-01-%02d" % (i % 28 + 1),
                                    _cfg()) for i in range(4000)]
    non_veto = [s for s in scales if s > 0]
    mean = sum(non_veto) / len(non_veto)
    assert abs(mean - CAL["mean_scale"]) < 0.03, mean


def test_vetoes_are_modelled_as_skips_not_tiny_positions(cal_file):
    """A veto removes the trade. Folding it into the haircut histogram would
    have let vetoed trades through at 10% size — wrong, and silently."""
    scales = [judge_model.scale_for("SYM%d" % i, "2025-02-01", _cfg())
              for i in range(4000)]
    vetoed = [s for s in scales if s == 0.0]
    assert vetoed, "no veto ever drawn"
    assert abs(len(vetoed) / len(scales) - CAL["veto_rate"]) < 0.02
    assert not [s for s in scales if 0.0 < s < 0.4], "scale below the observed floor"


def test_only_observed_scales_are_ever_produced(cal_file):
    seen = {judge_model.scale_for("S%d" % i, "2025-03-03", _cfg())
            for i in range(3000)}
    assert seen <= {0.0, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0}, seen


# ---- determinism, which gate re-runs depend on ----

def test_same_inputs_give_the_same_verdict_every_time(cal_file):
    a = [judge_model.scale_for("AMD", "2025-06-02", _cfg()) for _ in range(50)]
    assert len(set(a)) == 1


def test_verdict_does_not_depend_on_evaluation_order(cal_file):
    """Adding a symbol to the universe must not change another's verdict.

    This is why the draw is a hash rather than a PRNG sequence: a PRNG would
    make every result depend on how many symbols were scanned first, so a
    universe change would silently rewrite unrelated history.
    """
    solo = judge_model.scale_for("NVDA", "2025-06-02", _cfg())
    for filler in range(200):
        judge_model.scale_for("FILL%d" % filler, "2025-06-02", _cfg())
    assert judge_model.scale_for("NVDA", "2025-06-02", _cfg()) == solo


def test_salt_redraws_without_changing_the_distribution(cal_file):
    a = [judge_model.scale_for("S%d" % i, "2025-04-01", _cfg(salt="judge-v1"))
         for i in range(2000)]
    b = [judge_model.scale_for("S%d" % i, "2025-04-01", _cfg(salt="other"))
         for i in range(2000)]
    assert a != b, "salt had no effect"
    cut_a = sum(1 for s in a if 0 < s < 1) / len(a)
    cut_b = sum(1 for s in b if 0 < s < 1) / len(b)
    assert abs(cut_a - cut_b) < 0.05


# ---- mirroring the live truncation at main.py:623 ----

def test_apply_truncates_toward_zero_like_live(cal_file, monkeypatch):
    monkeypatch.setattr(judge_model, "scale_for", lambda *a, **k: 0.5)
    assert judge_model.apply(7, "SPY", "2025-01-02", _cfg()) == 3   # not 3.5, not 4


def test_a_downsize_that_truncates_to_zero_skips_the_trade(cal_file, monkeypatch):
    """The judge may only SHRINK (invariant #2). Rounding a fractional intent
    up would trade MORE than the judge sanctioned.

    Scale 0.6 on 1 share, deliberately, not 0.5: Python's round() is banker's
    rounding, so round(1*0.5) is already 0 and a mutation from int() to round()
    would slip past this assertion unnoticed. At 0.6, int() gives 0 (skip) and
    round() gives 1 — a 40% downsize silently executed at FULL size. The
    negative-control run on 2026-07-26 caught exactly that hole in an earlier
    version of this test.
    """
    monkeypatch.setattr(judge_model, "scale_for", lambda *a, **k: 0.6)
    assert judge_model.apply(1, "LLY", "2025-01-02", _cfg()) == 0
    # and the ordinary 0.5 case still skips
    monkeypatch.setattr(judge_model, "scale_for", lambda *a, **k: 0.5)
    assert judge_model.apply(1, "LLY", "2025-01-02", _cfg()) == 0


def test_apply_never_invents_shares_from_nothing(cal_file):
    assert judge_model.apply(0, "SPY", "2025-01-02", _cfg()) == 0
    assert judge_model.apply(-3, "SPY", "2025-01-02", _cfg()) == -3


# ---- fail loudly, never fall back to "no haircut" ----

def test_missing_calibration_raises_instead_of_silently_disabling(monkeypatch, tmp_path):
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(tmp_path / "absent.json"))
    with pytest.raises(judge_model.CalibrationError):
        judge_model.scale_for("SPY", "2025-01-02", _cfg())


def test_undersized_calibration_is_refused(tmp_path, monkeypatch):
    small = dict(CAL, n_judged_buys=9)
    p = tmp_path / "c.json"
    p.write_text(json.dumps(small))
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(p))
    with pytest.raises(judge_model.CalibrationError):
        judge_model.scale_for("SPY", "2025-01-02", _cfg())


def test_empty_histogram_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(dict(CAL, scale_histogram={})))
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(p))
    with pytest.raises(judge_model.CalibrationError):
        judge_model.scale_for("SPY", "2025-01-02", _cfg())


# ---- STRUCTURAL: the fix must not become divergence #9 ----

def _src(name):
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "src", name)) as f:
        return f.read()


def test_the_live_path_never_imports_the_simulated_judge():
    """main.py already applies the REAL judge at main.py:623.

    Importing this module there would cut every live order twice — a ninth
    sim/live divergence created by the code fixing the eighth. The guard is a
    test rather than a comment because comments do not fail CI.
    """
    assert "judge_model" not in _src("main.py")


def test_risk_py_never_imports_the_simulated_judge():
    """risk.py is SHARED by the live bot and the backtester.

    A haircut applied inside size_order() would reach production through the
    shared path, which is exactly the mechanism that produced divergences #1-#6.
    """
    assert "judge_model" not in _src("risk.py")


def test_the_shipped_calibration_is_real_and_current():
    """Guards against a hand-edited calibration drifting from the ledger."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(here, "knowledge", "judge_calibration.json")
    assert os.path.exists(p), "run scripts/calibrate_judge.py --write"
    cal = json.load(open(p))
    assert cal["n_judged_buys"] >= cal["min_sample"]
    assert 0.0 < cal["mean_scale"] <= 1.0
    assert cal["scale_histogram"], "empty histogram"
    assert cal.get("source_ledger_sha256_16"), "no provenance on the calibration"
