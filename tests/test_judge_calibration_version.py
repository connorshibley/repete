"""A calibration must say WHICH judge it describes, and be refused if it doesn't.

`judge_model` replays the live judge as a distribution fitted from real
decisions. There were already two guards against silently running the judge
OFF — `_STATS` counters, and `run_gate.py` refusing a judge-on verdict with
zero activity. There were NONE against running it ON with a distribution
fitted to a judge that no longer exists.

That is the more dangerous direction. A judge-off run at least reports numbers
somebody might question; a judge-on run against a stale fit reports numbers
that look exactly right.

CAUSAL, NOT TEMPORAL. "Is this calibration old?" is the wrong question — a fit
from six months ago is valid if the judge has not changed, and yesterday's is
worthless if it has. `backtest.judge_model.context_version` names the judge's
input regime; the calibration records what it was fitted under; a mismatch
refuses.

The cost of a bump is real and that is deliberate: the current fit took 34 days
to reach n=250 against a min_sample of 50. Changing what the judge reads should
feel expensive, because it is.
"""
import json

import pytest

import judge_model

BASE = {"n_judged_buys": 250, "min_sample": 50,
        "scale_histogram": {"0.5": 200, "1.0": 50}}


def _cal(tmp_path, **over):
    d = dict(BASE)
    d.update(over)
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_a_matching_version_loads(tmp_path):
    cal = judge_model.load_calibration(
        _cal(tmp_path, fitted_context_version=1), expect_version=1)
    assert cal["n_judged_buys"] == 250


def test_a_mismatched_version_is_refused(tmp_path):
    with pytest.raises(judge_model.CalibrationError) as e:
        judge_model.load_calibration(
            _cal(tmp_path, fitted_context_version=1), expect_version=2)
    assert "context_version" in str(e.value)
    assert "calibrate_judge.py --write" in str(e.value), (
        "a refusal that does not say how to fix it is a wall, not a guard")


def test_a_MISSING_version_is_a_mismatch_not_a_pass(tmp_path):
    """THE ONE THAT MATTERS. A file predating versioning cannot vouch for
    which judge it saw. Treating 'unknown' as 'fine' is the collapse this
    repo keeps finding — absent must not read as agreeing."""
    with pytest.raises(judge_model.CalibrationError):
        judge_model.load_calibration(_cal(tmp_path), expect_version=1)


def test_no_expectation_means_no_check(tmp_path):
    """Tooling that only wants to READ the file — calibrate_judge, tests —
    must not be blocked by a check meant for the simulator path."""
    cal = judge_model.load_calibration(_cal(tmp_path), expect_version=None)
    assert cal["n_judged_buys"] == 250


def test_the_existing_sample_floor_still_bites(tmp_path):
    """The version check must not have displaced the older guard."""
    with pytest.raises(judge_model.CalibrationError) as e:
        judge_model.load_calibration(
            _cal(tmp_path, n_judged_buys=10, fitted_context_version=1),
            expect_version=1)
    assert "min_sample" in str(e.value)


def test_the_shipped_calibration_matches_the_shipped_config():
    """The pair that actually runs. If these ever disagree, every gate with
    --judge-model is describing a bot that does not exist."""
    import os

    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, "config.yaml")))
    declared = ((cfg.get("backtest") or {}).get("judge_model") or {}).get(
        "context_version")
    assert declared is not None, (
        "config no longer declares backtest.judge_model.context_version, so "
        "nothing can detect a stale fit")

    cal = json.load(open(os.path.join(root, "knowledge",
                                      "judge_calibration.json")))
    fitted = cal.get("fitted_context_version")
    jcfg = (cfg.get("backtest") or {}).get("judge_model") or {}
    pending = bool(jcfg.get("calibration_refit_pending"))

    if fitted == declared:
        assert not pending, (
            "calibration_refit_pending is still true but the versions now "
            "agree — clear the flag, or the next real mismatch is invisible "
            "because this one was left set")
        return

    # A MISMATCH IS A LEGITIMATE STATE, and it is the state a context bump
    # puts the repo into. Re-earning a fit needs new live decisions: the
    # current one took 34 days to reach n=250 against min_sample 50. What is
    # NOT legitimate is a mismatch nobody declared.
    assert pending, (
        f"the shipped calibration was fitted under {fitted!r} but config "
        f"declares {declared!r}, and nothing declares that gap. Either re-fit "
        f"with `python scripts/calibrate_judge.py --write`, put the version "
        f"back, or set calibration_refit_pending: true to say the gap is "
        f"known.")
    assert jcfg.get("enabled") is not True, (
        "judge_model is ENABLED while its calibration is known-stale. Every "
        "haircut it applies describes a judge that no longer exists — and "
        "unlike a judge-off run, the numbers look right.")


def _calibrate_judge():
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "scripts"))
    import calibrate_judge
    return calibrate_judge, root


def test_the_version_reader_handles_a_missing_config():
    calibrate_judge, root = _calibrate_judge()
    import os
    assert calibrate_judge._context_version(
        os.path.join(root, "config.yaml")) is not None
    assert calibrate_judge._context_version("/nonexistent.yaml") is None, (
        "an unreadable config must yield None, not a default that would "
        "stamp a calibration as belonging to a regime it never saw")


def test_the_writer_actually_CALLS_the_reader_it_was_given():
    """THE TEST THAT WAS MISSING, and a mutation found it.

    The first version of this file asserted that `_context_version()` WORKS.
    It never asserted the writer USES it — so replacing the assignment with a
    hardcoded `= 1` survived every test here. A calibration would then stamp
    itself version 1 forever, and the guard would wave through a fit taken
    under version 5.

    That is the same failure src/dashboard.py:546 records: on 2026-08-20 the
    badge JS was changed to `var cls='green'` while both thresholds stayed
    defined and interpolated, and every badge test asserted they were PRESENT
    rather than READ. All of them passed.

    An AST check, not a grep: the literal `1` appears all over this file.
    """
    import ast
    calibrate_judge, _ = _calibrate_judge()
    src = ast.parse(open(calibrate_judge.__file__).read())

    assigned_from = []
    for node in ast.walk(src):
        if not isinstance(node, ast.Assign):
            continue
        for tgt in node.targets:
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "fitted_context_version"):
                assigned_from.append(node.value)

    assert assigned_from, (
        "nothing assigns fitted_context_version — a re-fit would produce a "
        "calibration that cannot say which judge it saw")
    for value in assigned_from:
        assert isinstance(value, ast.Call), (
            "fitted_context_version is assigned a literal rather than being "
            "read from config. Every re-fit would stamp the same number "
            "regardless of the regime it measured.")
        fn = value.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        assert name == "_context_version", (
            f"fitted_context_version comes from {name!r}, not the config reader")


# ---- the simulator path must ASK for the version -------------------------

def test_scale_for_refuses_a_calibration_from_a_different_judge(tmp_path,
                                                                monkeypatch):
    """M2's real target. The guard is only worth anything if the live
    simulator path actually passes `expect_version` — a `load_calibration()`
    with no argument silently opts out of the whole check."""
    monkeypatch.setattr(judge_model, "_cache", None, raising=False)
    monkeypatch.setattr(judge_model, "_CAL_PATH",
                        _cal(tmp_path, fitted_context_version=1))
    cfg = {"backtest": {"judge_model": {"enabled": True, "salt": "s",
                                        "context_version": 2}}}
    with pytest.raises(judge_model.CalibrationError):
        judge_model.scale_for("AAPL", "2026-08-23T00:00:00", cfg)


def test_scale_for_works_when_the_versions_agree(tmp_path, monkeypatch):
    monkeypatch.setattr(judge_model, "_cache", None, raising=False)
    monkeypatch.setattr(judge_model, "_CAL_PATH",
                        _cal(tmp_path, fitted_context_version=2))
    cfg = {"backtest": {"judge_model": {"enabled": True, "salt": "s",
                                        "context_version": 2}}}
    s = judge_model.scale_for("AAPL", "2026-08-23T00:00:00", cfg)
    assert 0.0 <= s <= 1.0


def test_a_disabled_judge_model_still_short_circuits(tmp_path, monkeypatch):
    """Disabled must return 1.0 without touching the calibration at all, so
    every pre-2026-07-26 gate reproduces byte-identically."""
    monkeypatch.setattr(judge_model, "_cache", None, raising=False)
    monkeypatch.setattr(judge_model, "_CAL_PATH", "/nonexistent.json")
    cfg = {"backtest": {"judge_model": {"enabled": False, "context_version": 9}}}
    assert judge_model.scale_for("AAPL", "2026-08-23T00:00:00", cfg) == 1.0
