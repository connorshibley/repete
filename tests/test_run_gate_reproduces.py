"""The runner must produce the same verdict however many workers ran it.

Why this file exists
--------------------
`run_gate.py --workers N` fans arms out across processes. That is the one place
the Bizon's 64 cores pay off, since arms are embarrassingly parallel — but a
research verdict that depends on scheduling is not a verdict.

Two properties are pinned here:

  * **Determinism.** Arms are collected back into SPEC order, never completion
    order, and `significance.compare` carries a fixed seed. `workers=1` and
    `workers=2` must agree exactly.
  * **Clause evaluation.** Each pass-mark rule does what the registration says
    in prose. The rules are executed rather than paraphrased precisely so this
    can be tested; `evaluate()` is pure given arm summaries, so it needs no
    bars.

The sharpest one is `test_an_arm_with_no_closed_trades_fails_rather_than_skips`.
§33 RUN 1 printed VALIDATED because two arms placed zero trades and their
silence read as absence rather than failure. "We could not measure" and "it did
not pass" are different facts, and only one of them should let a claim through.

The full-scale reproduction of §35 against the real 500-symbol snapshot is not
here — it takes minutes and needs a 19 MB file. It is recorded in
`research/verdicts.jsonl` and checked against
`knowledge/backtest_candidates.md` in the PR.
"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import gatespec as gs
import run_gate


def _spec(**over):
    spec = {
        "id": "t", "claim": "EDGE", "title": "t",
        "snapshot": {"path": "b.gz", "sha256": "a" * 64},
        "bonferroni_k": 8,
        "arms": [{"name": "baseline"}, {"name": "cand"}],
        "clauses": [{"id": "b", "rule": "pf_gt_baseline"},
                    {"id": "c", "rule": "maxdd_within", "pp": 1.0},
                    {"id": "d", "rule": "min_trades", "n": 30}],
        "prior": "EDGE claims are 0 for 8; this one probably fails as well.",
        "failure_modes": ["survivorship bias"],
    }
    spec.update(over)
    return spec


def _summary(**over):
    s = {"total_return_pct": 10.0, "profit_factor": 1.2,
         "max_drawdown_pct": 8.0, "n_trades": 100, "n_symbols_traded": 40,
         "avg_deployment_pct": 50.0, "buy_hold_return_pct": 9.0}
    s.update(over)
    return s


def _arms(base=None, cand=None, base_pnls=None, cand_pnls=None):
    # `is None`, not `or` — an EMPTY pnl list is the case
    # test_an_arm_with_no_closed_trades_fails_rather_than_skips exists to
    # exercise, and `cand_pnls or [...]` would silently substitute 60 trades
    # for it. The first version of this helper did exactly that and the test
    # passed for the wrong reason.
    return {
        "baseline": (_summary(**(base or {})),
                     [1.0] * 60 if base_pnls is None else base_pnls, 1.0),
        "cand": (_summary(**(cand or {})),
                 [1.0] * 60 if cand_pnls is None else cand_pnls, 1.0),
    }


def _fake_run_arm(job):
    """Module level so it can be pickled into a worker process. A local
    function cannot cross the process boundary, which is itself a useful
    reminder that anything `run_arms` fans out has to be picklable."""
    _, arm, *_ = job
    seed = sum(ord(c) for c in arm["name"])
    return (arm["name"], _summary(profit_factor=1.0 + seed / 1000),
            [float(seed % 7)] * 50, 0.0)


# ---- clause rules do what the prose says ----

def test_pf_gt_baseline_is_strict():
    """'strictly greater'. An equal PF is not an improvement, and a candidate
    that merely ties should not clear a bar written as `>`."""
    arms = _arms(base={"profit_factor": 1.2}, cand={"profit_factor": 1.2})
    out = run_gate.evaluate(_spec(clauses=[{"id": "b",
                                            "rule": "pf_gt_baseline"}]),
                            arms, "cand", resamples=50)
    assert out["clauses"][0]["pass"] is False


def test_maxdd_within_allows_exactly_the_registered_headroom():
    """`<=` baseline + pp. Boundary tested on purpose: an off-by-one here
    quietly changes what every registration meant by 'within 1.0pp'."""
    spec = _spec(clauses=[{"id": "c", "rule": "maxdd_within", "pp": 1.0}])
    at_limit = _arms(base={"max_drawdown_pct": 8.0},
                     cand={"max_drawdown_pct": 9.0})
    assert run_gate.evaluate(spec, at_limit, "cand", 50)["clauses"][0]["pass"]
    over = _arms(base={"max_drawdown_pct": 8.0},
                 cand={"max_drawdown_pct": 9.01})
    assert not run_gate.evaluate(spec, over, "cand", 50)["clauses"][0]["pass"]


def test_min_trades_is_inclusive():
    spec = _spec(clauses=[{"id": "d", "rule": "min_trades", "n": 30}])
    assert run_gate.evaluate(spec, _arms(cand={"n_trades": 30}),
                             "cand", 50)["clauses"][0]["pass"]
    assert not run_gate.evaluate(spec, _arms(cand={"n_trades": 29}),
                                 "cand", 50)["clauses"][0]["pass"]


def test_an_arm_with_no_closed_trades_fails_rather_than_skips():
    """§33 RUN 1 printed VALIDATED because two arms placed zero trades and the
    silence was read as absence rather than failure. 'We could not measure' and
    'it did not pass' are different facts."""
    spec = _spec(clauses=[{"id": "e", "rule": "significantly_better"}])
    out = run_gate.evaluate(spec, _arms(cand_pnls=[]), "cand", resamples=50)
    assert out["clauses"][0]["pass"] is False
    assert "no closed trades" in out["clauses"][0]["detail"]


def test_edge_and_capacity_read_different_verdicts_from_one_comparison():
    """`significantly_better` (EDGE) and `not_worse` (CAPACITY) are different
    bars. §31 needed the first and §27 the second — bending one runner to serve
    both is what the registrations warn against."""
    arms = _arms(base_pnls=[1.0] * 80, cand_pnls=[1.05] * 80)
    edge = run_gate.evaluate(_spec(clauses=[{"id": "e",
                                             "rule": "significantly_better"}]),
                             arms, "cand", resamples=200)
    cap = run_gate.evaluate(_spec(claim="CAPACITY",
                                  clauses=[{"id": "e", "rule": "not_worse"}]),
                            arms, "cand", resamples=200)
    assert edge["clauses"][0]["rule"] != cap["clauses"][0]["rule"]
    assert cap["clauses"][0]["pass"] is True     # a tiny gain is 'not worse'


def test_a_verdict_passes_only_when_every_clause_passes():
    spec = _spec()
    good = _arms(base={"profit_factor": 1.0}, cand={"profit_factor": 2.0,
                                                    "max_drawdown_pct": 8.0,
                                                    "n_trades": 100})
    assert run_gate.evaluate(spec, good, "cand", 50)["passed"] is True
    bad = copy.deepcopy(good)
    bad["cand"][0]["n_trades"] = 5
    assert run_gate.evaluate(spec, bad, "cand", 50)["passed"] is False


# ---- determinism ----

def _fake_bars(n=40):
    return {f"S{i}": [{"ts": f"2020-01-{d:02d}T00:00:00+00:00", "o": 10.0,
                       "h": 10.5, "l": 9.5, "c": 10.0 + (i + d) % 5,
                       "v": 1_000_000} for d in range(1, 28)]
            for i in range(n)}


def test_results_are_rekeyed_into_spec_order():
    """The property that makes parallelism safe. Baseline must be `baseline`
    whichever worker finished first — every clause is expressed relative to it,
    so a slipped mapping would invert the pass mark silently.

    Written against `in_spec_order` directly, with results deliberately
    SHUFFLED. The first version of this test drove `run_arms` end to end and
    passed whether or not the reordering existed, because
    `multiprocessing.Pool.map` already preserves input order — the guard was
    never exercised and a negative control proved it: deleting the reordering
    left all ten tests green.
    """
    arms = [{"name": "baseline"}, {"name": "a"}, {"name": "b"}]
    done = [("b", _summary(profit_factor=3.0), [3.0], 0.0),
            ("baseline", _summary(profit_factor=1.0), [1.0], 0.0),
            ("a", _summary(profit_factor=2.0), [2.0], 0.0)]
    out = run_gate.in_spec_order(done, arms)
    assert list(out) == ["baseline", "a", "b"]
    assert out["baseline"][0]["profit_factor"] == 1.0


def test_a_missing_arm_is_an_error_not_a_silent_gap():
    """If an arm produced nothing, saying so beats returning a dict whose
    `baseline` key is absent and letting `evaluate` raise a KeyError three
    frames later."""
    with pytest.raises(SystemExit, match="produced no result"):
        run_gate.in_spec_order([("a", _summary(), [1.0], 0.0)],
                               [{"name": "baseline"}, {"name": "a"}])


@pytest.mark.parametrize("workers", [1, 2])
def test_run_arms_returns_spec_order_at_any_worker_count(workers, monkeypatch):
    spec = _spec(arms=[{"name": "baseline"}, {"name": "a"}, {"name": "b"}])
    monkeypatch.setattr(run_gate, "run_arm", _fake_run_arm)
    out = run_gate.run_arms(spec, {}, {}, {}, 100_000.0, workers)
    assert list(out) == ["baseline", "a", "b"]


def test_one_worker_and_two_workers_agree(monkeypatch):
    spec = _spec(arms=[{"name": "baseline"}, {"name": "a"}, {"name": "b"}])
    monkeypatch.setattr(run_gate, "run_arm", _fake_run_arm)
    one = run_gate.run_arms(spec, {}, {}, {}, 100_000.0, 1)
    two = run_gate.run_arms(spec, {}, {}, {}, 100_000.0, 2)
    assert [(k, v[0], v[1]) for k, v in one.items()] == \
           [(k, v[0], v[1]) for k, v in two.items()]

    a = run_gate.evaluate(spec, one, "a", resamples=200)
    b = run_gate.evaluate(spec, two, "a", resamples=200)
    assert a["clauses"] == b["clauses"] and a["passed"] == b["passed"]


def test_the_bonferroni_k_from_the_spec_reaches_the_comparison():
    """K=8 vs K=1 is the difference between a 99.38% and a 95% interval. If the
    spec's K were ignored, every EDGE verdict here would be measured against a
    bar looser than the one registered."""
    arms = _arms(base_pnls=[0.0] * 80, cand_pnls=[1.0] * 80)
    out = run_gate.evaluate(_spec(bonferroni_k=8,
                                  clauses=[{"id": "e",
                                            "rule": "significantly_better"}]),
                            arms, "cand", resamples=300)
    assert "Bonferroni K=8" in out["clauses"][0]["detail"]
