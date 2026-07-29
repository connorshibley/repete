"""W2-1 — the simulated judge must be DECLARED by every new registration, and
a run that claims it must prove the model actually fired.

The defect this file exists to stop
-----------------------------------
§29 built `src/judge_model.py` to close the live half of divergence #8, and
shipped it `enabled: false` in config.yaml — correctly, so gates frozen before
that date still reproduce. Nothing then turned it on. `scripts/run_gate.py`
never touched the flag, and no file under `research/specs/` mentioned it.

So §35, §37, §38, §39, §40 and §41 were every one of them scored against a bot
with **no judge**, while the live bot cuts 58.1% of its buys and vetoes 2.4%.
That went unnoticed for three days for one reason: a judge-on run and a
judge-off run are indistinguishable from the outside. Both print numbers.

Two properties are therefore load-bearing here, and neither is "the flag
exists":

1. **A new spec cannot be frozen without answering the question.** Not
   defaulted, not inferred — written in the file, before any data is seen.
2. **A run that says `judge_model: true` must be able to show the work.** The
   model counts what it did, and the runner refuses to write a verdict when
   those counters say it did nothing. A verdict labelled judge-on and measured
   judge-off is worse than no verdict, because it looks like divergence #8 was
   closed.

Absence is not `false`. A spec that predates the field ran judge-less by
accident; a spec that says `false` chose to. Flattening those two into one
value would erase the entire finding, so `judge_setting` returns None for the
first and the runner banners it.
"""
import json
import subprocess
import sys
import os

import pytest

import gatespec as gs
import judge_model as jm

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
import run_gate                                              # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spec(**over):
    spec = {
        "id": "t9", "claim": "EDGE", "title": "a test claim",
        "snapshot": {"path": "bars.json.gz", "sha256": "a" * 64},
        "judge_model": True,
        "arms": [{"name": "baseline"}, {"name": "cand"}],
        "clauses": [{"id": "a", "rule": "pf_gt_baseline"}],
        "prior": "EDGE claims are 0 for 10; this almost certainly fails too.",
        "failure_modes": ["survivorship bias inflates the candidate"],
    }
    spec.update(over)
    return spec


# ---- the spec field ----

def test_judge_model_must_be_a_bool_not_a_truthy_string():
    """`judge_model: "no"` is truthy in Python and would silently turn the
    model ON in a spec whose author meant the opposite."""
    with pytest.raises(gs.SpecError, match="judge_model"):
        gs.validate(_spec(judge_model="no"))
    with pytest.raises(gs.SpecError, match="judge_model"):
        gs.validate(_spec(judge_model=1))


def test_both_bools_are_accepted():
    gs.validate(_spec(judge_model=True))
    gs.validate(_spec(judge_model=False))


def test_a_spec_predating_the_field_still_validates():
    """§35-§41 must keep loading and re-executing byte-identically. A freeze
    that retroactively invalidates its own record is not a freeze."""
    old = _spec()
    del old["judge_model"]
    gs.validate(old)


def test_absent_is_not_false():
    """The distinction the whole finding rests on: `false` is a choice, absence
    is not knowing the question existed."""
    off = _spec(judge_model=False)
    legacy = _spec()
    del legacy["judge_model"]

    assert run_gate.judge_setting(off) is False
    assert run_gate.judge_setting(legacy) is None
    assert run_gate.judge_setting(_spec(judge_model=True)) is True


def test_declaring_the_judge_changes_the_frozen_hash():
    """The setting is part of the claim, not of the machine's ambient config.
    If it did not enter the hash, a spec could be re-run judge-on after being
    registered judge-off and still pass the freeze check."""
    on = gs.canonical_sha256(_spec(judge_model=True))
    off = gs.canonical_sha256(_spec(judge_model=False))
    legacy = _spec()
    del legacy["judge_model"]
    assert len({on, off, gs.canonical_sha256(legacy)}) == 3


# ---- registration refuses an undeclared spec ----

def test_register_gate_refuses_a_spec_without_the_field(tmp_path):
    spec = _spec(id="t9")
    del spec["judge_model"]
    specs = tmp_path / "research" / "specs"
    specs.mkdir(parents=True)
    import yaml
    (specs / "t9.yaml").write_text(yaml.safe_dump(spec))

    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "register_gate.py"),
         "t9", "--registrations", str(tmp_path / "reg.jsonl"),
         "--verdicts", str(tmp_path / "v.jsonl")],
        cwd=str(tmp_path), capture_output=True, text=True)

    assert r.returncode != 0, "an undeclared spec was frozen"
    assert "judge_model" in (r.stdout + r.stderr)
    assert not (tmp_path / "reg.jsonl").exists(), (
        "REFUSING must write nothing — a partial registration is a frozen "
        "pass mark nobody meant to freeze")


# ---- the counters ----

def _cfg(on: bool):
    return {"backtest": {"judge_model": {"enabled": on, "salt": "judge-v1"}}}


def test_a_disabled_model_counts_nothing_and_changes_nothing():
    """The identity property every gate before W2-1 depends on."""
    jm.reset_stats()
    cfg = _cfg(False)
    for i in range(50):
        assert jm.apply(100, f"S{i}", "2024-01-02", cfg) == 100
    assert jm.stats() == {"sized": 0, "cut": 0, "vetoed": 0, "zeroed": 0}


def test_an_enabled_model_counts_what_it_did():
    jm.reset_stats()
    cfg = _cfg(True)
    out = [jm.apply(100, f"S{i}", "2024-01-02", cfg) for i in range(200)]
    s = jm.stats()

    assert s["sized"] == 200
    assert s["cut"] > 0, "the model sized 200 entries and cut none of them"
    assert s["vetoed"] > 0, "veto has a 2.4% live rate and never fired in 200"
    # The counters must describe the same events the outputs show.
    assert sum(1 for q in out if q == 0) == s["vetoed"] + s["zeroed"]
    assert sum(1 for q in out if q < 100) == s["cut"] + s["vetoed"]


def test_stats_are_a_copy_not_the_live_dict():
    jm.reset_stats()
    snap = jm.stats()
    jm.apply(100, "AAA", "2024-01-02", _cfg(True))
    assert snap["sized"] == 0, "a stashed snapshot mutated underneath the caller"


def test_a_vetoed_entry_is_not_double_counted_as_a_cut():
    """`vetoed` and `cut` partition the acted-upon set. If a veto also counted
    as a cut, the runner's refusal threshold would be measuring a number twice
    and could pass on a model that only ever vetoed."""
    jm.reset_stats()
    cfg = _cfg(True)
    for i in range(300):
        jm.apply(100, f"S{i}", "2024-01-02", cfg)
    s = jm.stats()
    assert s["cut"] + s["vetoed"] <= s["sized"]


def test_zero_qty_is_passed_through_untouched():
    """A rail already refused this entry. Sizing it again would count an event
    that never happened."""
    jm.reset_stats()
    assert jm.apply(0, "AAA", "2024-01-02", _cfg(True)) == 0
    assert jm.stats()["sized"] == 0


# ---- the runner's refusal ----

def _arms(sized, cut, vetoed):
    js = {"sized": sized, "cut": cut, "vetoed": vetoed, "zeroed": 0}
    summary = {"total_return_pct": 1.0, "profit_factor": 1.0,
               "max_drawdown_pct": 1.0, "n_trades": 50}
    return {"baseline": (summary, [1.0], 0.0, js),
            "cand": (summary, [1.0], 0.0, dict(js))}


def _refuse(judged):
    """The runner's condition, extracted so it can be exercised without a
    300-second backtest. Kept identical to run_gate.py by
    test_the_runner_still_contains_this_check."""
    total = sum(js["sized"] for js in judged.values())
    acted = sum(js["cut"] + js["vetoed"] for js in judged.values())
    return total == 0 or acted == 0


def test_the_runner_refuses_a_judge_on_run_where_the_model_never_acted():
    """THE CHECK THAT MAKES THE FLAG MEAN SOMETHING.

    The failure that actually happened, one layer up: the flag says on, the
    machinery says nothing was cut. Without this the runner writes a verdict
    claiming divergence #8 was modelled when it was not.
    """
    assert _refuse({n: js for n, (_, _, _, js) in _arms(0, 0, 0).items()}), (
        "a run that sized nothing must be refused")
    assert _refuse({n: js for n, (_, _, _, js) in _arms(500, 0, 0).items()}), (
        "a run that sized 500 entries and cut none of them must be refused — "
        "that is judge-off wearing a judge-on label")
    assert not _refuse(
        {n: js for n, (_, _, _, js) in _arms(500, 290, 12).items()})


def test_the_runner_still_contains_this_check():
    """A meta-assertion. `_refuse` above is a copy, and a copy can drift from
    the original silently — which would leave this file green while the runner
    lost the guard entirely. Pin the real source instead.
    """
    src = open(os.path.join(REPO, "scripts", "run_gate.py")).read()
    assert 'acted = sum(js["cut"] + js["vetoed"] for js in judged.values())' in src
    assert "if total == 0 or acted == 0:" in src
    assert "REFUSING to record" in src
    # It must refuse BEFORE the verdict row is appended, or the false record
    # exists on disk regardless.
    assert src.index("REFUSING to record") < src.index('with open(args.verdicts, "a")')


def test_the_verdict_records_the_setting():
    src = open(os.path.join(REPO, "scripts", "run_gate.py")).read()
    assert '"judge_model": judge,' in src, (
        "verdicts.jsonl must say which regime produced each row, or a future "
        "session cannot tell a §41 number from a post-W2-1 one")
    assert '"judge_stats": judged if judge else None,' in src


# ---- the calibration is current ----

def test_the_shipped_calibration_is_not_the_stale_pre_29_one():
    """n=146 / observed_to 2026-07-23 was the file §29 wrote and nothing had
    refreshed it since, while the ledger kept growing. A calibration that
    silently ages is a simulator that silently diverges."""
    with open(os.path.join(REPO, "knowledge",
                           "judge_calibration.json")) as f:
        cal = json.load(f)
    assert cal["n_judged_buys"] >= 164, cal["n_judged_buys"]
    assert cal["scale_histogram"], "an empty histogram sizes everything at 1.0"
    assert cal["n_judged_buys"] >= cal["min_sample"]


def test_gutting_the_declaration_would_fail_this_file():
    """Meta-assertion, per repo practice: name what a gutted implementation
    would look like so this file cannot pass against one."""
    src = open(os.path.join(REPO, "scripts", "register_gate.py")).read()
    assert 'if "judge_model" not in spec:' in src
    assert "REFUSING to register" in src
