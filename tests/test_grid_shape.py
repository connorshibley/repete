"""§55. `no_interior_optimum` — §23's written lesson, executed.

THE ACCEPTANCE TEST IS `test_it_fails_on_s23s_real_four_arms`. A rule that
cannot catch the case it was written for is decoration, so the first thing this
file does is feed it §23's actual published profit factors and require a FAIL.
Everything else here exists to stop it failing on cases it must NOT catch — a
guard that rejects everything is as useless as one that rejects nothing.

§23's grid, verbatim from `knowledge/backtest_candidates.md`:

    baseline (no filter)   PF 1.955
    rvol >= 1.5            PF 2.898   <- IS-selected winner, all six clauses passed
    rvol >= 2.0            PF 2.240
    rvol >= 2.5            PF 1.705   <- below baseline

The winning cell cleared every deterministic clause by a wide margin. The
SHAPE is what says it was noise, and nothing in the runner could see the shape.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import gatespec as gs                                          # noqa: E402
import run_gate                                                # noqa: E402

# §23's four arms, in threshold order. The real numbers, not a reconstruction.
S23_PF = [1.955, 2.898, 2.240, 1.705]
S23_ORDER = ["baseline", "rvol1.5", "rvol2.0", "rvol2.5"]


def _summary(**over):
    s = {"total_return_pct": 10.0, "profit_factor": 1.2,
         "max_drawdown_pct": 8.0, "n_trades": 100, "n_symbols_traded": 40,
         "avg_deployment_pct": 50.0, "buy_hold_return_pct": 9.0}
    s.update(over)
    return s


def _arms_from(values, order=None, metric="profit_factor"):
    # First arm is `baseline` because evaluate() reads it unconditionally —
    # every comparative clause is expressed relative to it.
    order = order or ["baseline"] + [f"a{i}" for i in range(1, len(values))]
    return {n: (_summary(**{metric: v}), [1.0] * 60, 1.0)
            for n, v in zip(order, values)}


def _spec(clause, arms_names):
    return {
        "id": "t", "claim": "DIAGNOSTIC", "title": "t",
        "snapshot": {"path": "b.gz", "sha256": "a" * 64},
        "bonferroni_k": 15,
        "arms": [{"name": n} for n in arms_names],
        "clauses": [clause],
        "prior": "The response shape is the thing being read here, not the peak.",
        "failure_modes": ["a grid too narrow to bracket the effect"],
    }


def _score(values, order=None, metric="profit_factor", better="high"):
    # First arm is `baseline` because evaluate() reads it unconditionally —
    # every comparative clause is expressed relative to it.
    order = order or ["baseline"] + [f"a{i}" for i in range(1, len(values))]
    clause = {"id": "f", "rule": "no_interior_optimum",
              "metric": metric, "order": order}
    if better != "high":
        clause["better"] = better
    arms = _arms_from(values, order, metric)
    spec = _spec(clause, order)
    out = run_gate.evaluate(spec, arms, order[0], resamples=50)
    return out["clauses"][0]


# ---- the acceptance test ---------------------------------------------------

def test_it_fails_on_s23s_real_four_arms():
    """The case the rule was written for. If this ever passes, the rule is
    decoration and the §55 write-up is wrong."""
    got = _score(S23_PF, S23_ORDER)
    assert got["pass"] is False
    assert "INTERIOR" in got["detail"]
    assert "rvol1.5" in got["detail"]
    assert "fitted parameter" in got["detail"]


def test_s23s_winning_cell_would_still_clear_every_other_clause():
    """Why the shape rule is needed at all, pinned rather than asserted in
    prose: the arm that fails above beats the baseline on the metrics the
    runner already had. The existing clauses cannot see what is wrong here."""
    assert max(S23_PF) == S23_PF[1]
    assert S23_PF[1] > S23_PF[0] + 0.10      # §23's stricter EDGE PF bar
    assert S23_PF[3] < S23_PF[0]             # and it ends BELOW baseline


# ---- what must NOT fail ----------------------------------------------------

@pytest.mark.parametrize("label,values", [
    ("monotone increasing", [1.0, 1.4, 1.9, 2.3]),
    ("monotone decreasing", [2.3, 1.9, 1.4, 1.0]),
    ("flat", [1.5, 1.5, 1.5, 1.5]),
    ("peak at the first arm", [3.0, 1.2, 1.1, 1.0]),
    ("peak at the last arm", [1.0, 1.1, 1.2, 3.0]),
    ("noisy but peaking at an end", [2.9, 1.1, 1.8, 1.2]),
])
def test_shapes_that_are_not_evidence_of_fitting_pass(label, values):
    """An END optimum means the grid failed to BRACKET the effect — a reason to
    widen the grid, not evidence of a fitted parameter. Making this rule a
    monotonicity requirement would have rejected all six."""
    got = _score(values)
    assert got["pass"] is True, f"{label}: {got['detail']}"


def test_a_flat_response_is_reported_as_flat_not_as_an_end_peak():
    """Cosmetic in the pass mark, load-bearing in the write-up: "flat" and
    "the peak landed at an end" are different findings."""
    assert "flat" in _score([1.5, 1.5, 1.5])["detail"]
    assert "at an end" in _score([1.0, 1.5, 2.0])["detail"]


# ---- direction -------------------------------------------------------------

def test_a_lower_is_better_metric_looks_for_an_interior_TROUGH():
    """max_drawdown_pct peaking in the middle is unremarkable; TROUGHING in the
    middle is the same tell as an interior PF peak. Scoring it with the default
    would read half the metrics backwards."""
    dd = [9.0, 3.0, 6.0, 8.0]                     # trough at index 1
    assert _score(dd, metric="max_drawdown_pct", better="low")["pass"] is False
    # The identical numbers under the default direction pass, because the MAX
    # is at an end. Same data, opposite verdict — which is exactly why `better`
    # is validated rather than defaulted silently.
    assert _score(dd, metric="max_drawdown_pct")["pass"] is True


def test_monotone_drawdown_passes_under_the_low_direction():
    assert _score([9.0, 7.0, 5.0, 3.0], metric="max_drawdown_pct",
                  better="low")["pass"] is True


# ---- cannot-measure is a FAIL, never a skip --------------------------------

def test_an_arm_missing_the_metric_fails_rather_than_skips():
    """§33 RUN 1 printed VALIDATED because checks that quietly did not run read
    as checks that passed."""
    order = ["baseline", "a1", "a2"]
    arms = _arms_from([1.0, 2.0, 3.0], order)
    arms["a1"][0].pop("profit_factor")
    spec = _spec({"id": "f", "rule": "no_interior_optimum",
                  "metric": "profit_factor", "order": order}, order)
    got = run_gate.evaluate(spec, arms, "baseline", resamples=50)["clauses"][0]
    assert got["pass"] is False
    assert "cannot score" in got["detail"] and "a1" in got["detail"]


def test_an_arm_absent_from_the_run_fails_rather_than_skips():
    order = ["baseline", "a1", "a2"]
    arms = _arms_from([1.0, 2.0, 3.0], order)
    del arms["a2"]
    spec = _spec({"id": "f", "rule": "no_interior_optimum",
                  "metric": "profit_factor", "order": order}, order)
    got = run_gate.evaluate(spec, arms, "baseline", resamples=50)["clauses"][0]
    assert got["pass"] is False and "cannot score" in got["detail"]


# ---- registration-time validation ------------------------------------------

def _validate(clause, arm_names=("baseline", "a1", "a2", "a3")):
    gs.validate(_spec(clause, list(arm_names)))


def test_a_two_arm_order_is_refused_because_it_could_never_fail():
    """With two positions there is no interior index, so the clause would pass
    unconditionally — a check that cannot fail is worse than no check."""
    with pytest.raises(ValueError, match="at least THREE"):
        _validate({"id": "f", "rule": "no_interior_optimum",
                   "metric": "profit_factor", "order": ["baseline", "a1"]})


def test_an_unknown_metric_is_refused_at_registration_not_after_the_run():
    """§39 crashed with IndexError AFTER a 388-second run had computed every
    number. A typo deserves to surface while the spec is being frozen."""
    with pytest.raises(ValueError, match="needs a `metric`"):
        _validate({"id": "f", "rule": "no_interior_optimum",
                   "metric": "sharpe", "order": ["baseline", "a1", "a2"]})


def test_an_order_naming_a_nonexistent_arm_is_refused():
    with pytest.raises(ValueError, match="arms that do not exist"):
        _validate({"id": "f", "rule": "no_interior_optimum",
                   "metric": "profit_factor",
                   "order": ["baseline", "a1", "ghost"]})


def test_a_repeated_arm_in_the_order_is_refused():
    with pytest.raises(ValueError, match="repeats an arm"):
        _validate({"id": "f", "rule": "no_interior_optimum",
                   "metric": "profit_factor",
                   "order": ["baseline", "a1", "a1"]})


def test_a_bad_direction_is_refused():
    with pytest.raises(ValueError, match="must be `high` or `low`"):
        _validate({"id": "f", "rule": "no_interior_optimum",
                   "metric": "profit_factor", "better": "middle",
                   "order": ["baseline", "a1", "a2"]})


def test_a_valid_clause_registers():
    _validate({"id": "f", "rule": "no_interior_optimum",
               "metric": "profit_factor",
               "order": ["baseline", "a1", "a2", "a3"]})


def test_the_rule_is_a_grid_rule_and_not_a_comparative_one():
    """GRID rules read every arm; COMPARATIVE rules read the candidate against
    baseline. Filing it in the wrong bucket would trigger the two-arm
    requirement instead of the three-arm one."""
    assert "no_interior_optimum" in gs.GRID_RULES
    assert "no_interior_optimum" in gs.CLAUSE_RULES
    assert "no_interior_optimum" not in gs.COMPARATIVE_RULES
    assert "no_interior_optimum" not in gs.SELF_RULES
