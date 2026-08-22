"""The running trial count in the prose must match the register.

METHOD NOTE 2 in knowledge/backtest_candidates.md makes recording the running
trial count binding on every new section. It was honoured through §35 and
then abandoned for thirty-eight sections; the line read "through §11: ~11"
against 225 arm runs in git. A mandatory figure nobody reads is a ritual, not
a control — the same finding §60 made about the priors.

These tests make the figure the one thing the guard reads, exactly as
test_doc_counts.py does for the test count and the divergence tally.
"""
import pytest

import trial_tally as tt


def test_the_register_holds_what_the_audit_said_it_held():
    """Sanity anchor. If this ever drops the verdict file lost rows."""
    assert tt.total_trials() >= 225


def test_every_verdict_row_contributes_its_arms():
    rows = tt._rows(tt.VERDICTS)
    assert rows, "no verdicts — nothing to count"
    assert all(isinstance(r.get("arms"), dict) and r["arms"] for r in rows), (
        "a verdict row without an arms dict cannot be counted and should not "
        "exist — run_gate.py writes arms on every record")
    assert tt.total_trials() == sum(len(r["arms"]) for r in rows)


def test_the_prose_states_the_current_count():
    """THE PIN. Goes red the moment a new verdict lands without the prose
    moving, which is the drift this file exists to end."""
    stated = tt.stated_in_prose()
    assert stated is not None, (
        "knowledge/backtest_candidates.md no longer states a trial count in "
        'the form "Trial count through §NN: N arm runs"')
    sec, n = stated
    assert sec == tt.latest_section(), (
        f"prose counts through §{sec} but the latest verdict is §"
        f"{tt.latest_section()} — record the running count, METHOD NOTE 2")
    assert n == tt.trials_through(sec), (
        f"prose says {n} arm runs through §{sec}; the register says "
        f"{tt.trials_through(sec)}")


def test_through_is_monotone_and_ends_at_total():
    secs = sorted(tt.trials_by_section())
    running = [tt.trials_through(s) for s in secs]
    assert running == sorted(running)
    assert running[-1] == tt.total_trials()


def test_a_rerun_counts_as_a_trial():
    """s35/s43d/s48d/s50d each carry two verdict rows at one spec hash.
    Re-running and looking is not free of selection just because the spec
    did not change, so both rows' arms count."""
    by_id = {}
    for r in tt._rows(tt.VERDICTS):
        by_id.setdefault(r["id"], []).append(r)
    reran = [i for i, rs in by_id.items() if len(rs) > 1]
    assert reran, "expected at least one re-run spec in the register"
    per_id = sum(len(r["arms"]) for i in reran for r in by_id[i])
    per_spec = sum(len(by_id[i][0]["arms"]) for i in reran)
    assert per_id > per_spec
    # and the module counts the larger number
    counted = sum(1 for r in tt.arm_runs_from_verdicts() if r["spec_id"] in reran)
    assert counted == per_id


def test_the_log_delta_is_reported_not_hidden():
    """trial_report.py must print the gap between verdicts and the log. A
    runner that forgets to log must be visible, not silently absorbed."""
    import subprocess
    import sys
    out = subprocess.run(
        [sys.executable, str(tt.ROOT / "scripts" / "trial_report.py")],
        capture_output=True, text=True, cwd=str(tt.ROOT))
    assert out.returncode == 0, out.stderr
    assert "delta (verdicts - log)" in out.stdout
    assert "FLOOR, not N" in out.stdout, (
        "the report must state that the count excludes pre-register trials")


@pytest.mark.parametrize("sid,sec", [("s43d", 43), ("s72a", 72), ("s35", 35)])
def test_section_parse(sid, sec):
    assert tt._section_of(sid) == sec
