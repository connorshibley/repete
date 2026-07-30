"""§44 must face §41's bar, not an easier one.

§41 was REJECTED on `maxdd_within pp 3.0` in three periods of four, and its
write-up closed with a commitment:

    "`pp: 3.0` will not be widened and §41 re-scored. The mark was frozen before
    the data existed, and re-scoring a rejected candidate against a loosened bar
    is the move this entire programme exists to prevent."

§44 is the legitimate follow-up §41 described — a NEW candidate (the decay paired
with halved per-trade sizing) rather than a second look at the old one. That
distinction only holds while the bar stays put. If a later edit widens `pp`, or
swaps `maxdd_within` for something softer, §44 silently becomes the re-score §41
promised would never happen, and the write-up would still read as principled.

The canonical-hash freeze already prevents editing a REGISTERED spec — `run_gate`
refuses on mismatch. This file guards the gap the hash does not cover: a spec
file edited BEFORE registration, or a fresh §46 that quietly relaxes the same
clause while citing §41's rejection in its prior.

Deliberately not asserted here: whether §44 passed. That belongs to the verdict
record, and a test that encoded the outcome would have to be edited when the
answer changed — which is the failure mode, not a guard against it.
"""
import glob
import os

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPECS = os.path.join(REPO, "research", "specs")

S44 = sorted(glob.glob(os.path.join(SPECS, "s44*.yaml")))


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _clause(spec, rule):
    for c in spec["clauses"]:
        if c["rule"] == rule:
            return c
    return None


def test_the_s44_family_exists_and_is_four_periods():
    assert len(S44) == 4, (
        f"expected s44a-d, found {[os.path.basename(p) for p in S44]} — a "
        f"conjunction that lost a period is period selection")


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_maxdd_bar_is_s41s_unwidened(path):
    """The commitment, machine-checked."""
    spec = _load(path)
    c = _clause(spec, "maxdd_within")
    assert c is not None, (
        "s44 dropped the maxdd_within clause entirely. §41 was rejected on it; "
        "removing it is a wider loosening than raising pp would have been.")
    assert c["pp"] == 3.0, (
        f"maxdd_within pp is {c['pp']}, not §41's 3.0. §41's write-up committed "
        f"in terms that this bar would not be widened and the claim re-scored. "
        f"If a different allowance is genuinely wanted, it is a different claim "
        f"with its own registration — not an edit to this one.")


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_deployment_floor_is_s41s(path):
    """§41's other structural clause. Lowering it would let a candidate pass by
    barely investing, which is the degenerate case it was written to block."""
    spec = _load(path)
    c = _clause(spec, "deployment_at_least")
    assert c is not None and c["pct"] == 25.0, (
        "the 25.0% deployment floor came from §40's published baselines and is "
        "what stops 'made no trades, took no drawdown' from reading as a pass")


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_the_candidate_is_the_arm_that_cuts_sizing(path):
    """The claim is decay PLUS reduced sizing. If the scored arm were the plain
    decay arm, §44 would be re-running §41 against the same bar — the exact
    re-score its own header disclaims."""
    spec = _load(path)
    assert spec.get("candidate") == "decay_half", (
        "with three arms and no explicit candidate, run_gate scores arms[1] — "
        "the decay CONTROL — and §44 would silently become §41 again")
    arm = next(a for a in spec["arms"] if a["name"] == "decay_half")
    assert arm["set"]["risk.risk_per_trade_pct"] == 4.0
    assert arm["set"]["risk.drawdown_decay.enabled"] is True


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_the_control_arm_holds_sizing_at_the_shipped_value(path):
    """The control exists to separate 'the sizing cut helped' from 'the
    simulator changed underneath us' — §41 ran judge-OFF and W2-1 turned the
    judge on. A control that also moved sizing would measure neither."""
    spec = _load(path)
    ctrl = next(a for a in spec["arms"] if a["name"] == "decay")
    assert "risk.risk_per_trade_pct" not in ctrl["set"], (
        "the decay control also changes sizing — it is no longer a control")
    assert ctrl["set"]["risk.drawdown_decay.grace_bars"] == 10
    assert ctrl["set"]["risk.drawdown_decay.halflife_bars"] == 20


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_the_return_clause_is_exposure_matched(path):
    """§41's failure_modes warned that any write-up quoting the raw return
    difference was misreading the spec, because more deployment means more
    exposure to the market's own direction. A bare `beats_buy_hold` or a raw
    return comparison would build that error into the pass mark itself."""
    spec = _load(path)
    assert _clause(spec, "beats_exposure_matched") is not None, (
        "§44 exists to ask whether return improved AT MATCHED DRAWDOWN; without "
        "an exposure-matched clause it asks whether more exposure earned more")


@pytest.mark.parametrize("path", S44, ids=lambda p: os.path.basename(p))
def test_the_judge_is_modelled(path):
    """§41 ran judge-OFF, which §42 showed is worth 1.7x-7.5x executions and a
    lower profit factor. Re-asking on the corrected simulator is half the point
    of §44 existing at all."""
    assert _load(path)["judge_model"] is True


def test_all_four_share_one_pass_mark():
    """A conjunction whose members are scored differently is four claims wearing
    one name — and would let a period be dropped by weakening only its spec."""
    marks = []
    for p in S44:
        spec = _load(p)
        marks.append(sorted((c["rule"], c.get("pp"), c.get("n"), c.get("pct"))
                            for c in spec["clauses"]))
    assert all(m == marks[0] for m in marks), (
        "the four s44 specs do not carry identical clauses")


def test_the_sizing_cut_is_a_single_pre_specified_value():
    """No sweep. Trying 2.0, 3.0 and 4.0 and reporting the best is what §34
    retired; the guard is that exactly one value appears across the family."""
    values = {next(a for a in _load(p)["arms"] if a["name"] == "decay_half")
              ["set"]["risk.risk_per_trade_pct"] for p in S44}
    assert values == {4.0}, (
        f"more than one sizing was tried across the family ({values}) — that is "
        f"a sweep, and the best of it is not a result")
