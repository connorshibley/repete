"""The two schedulers must agree about which jobs exist.

Why this file exists
--------------------
There are two ways to run this agent on a schedule: launchd plists (this Mac,
what is actually deployed) and `scripts/scheduler.py` (the container). Until
2026-07-27 they had silently diverged. `scheduler.py` defined twelve jobs;
launchd ran seven of them. The five that existed only in the container path:

    open-cycle     the 09:35 entry pass, ADOPTED by gate §19a
    midday-scan    the noon alert sweep
    mark-book      the hourly position re-mark
    backup         nightly state backup
    restore-drill  weekly proof the backup restores

Nothing detected it. `docs/go_live_checklist.md` carried
`[x] Backups scheduled ... and restore drill passing` — true of a file, false
of the machine: exactly one backup existed and it had been taken by hand.
Tests in `test_mark_positions.py` and `test_opportunity_scan.py` assert against
`scheduler.py JOBS`, i.e. against the schedule that was not running.

This is the same shape as divergence #7 — "the running system is not the
reviewed system" — which is what `src/deploycheck.py` was built to catch. It
caught the code and missed the schedule.

What this file does NOT claim
-----------------------------
It does not require the two to be identical. launchd deliberately omits
`open-cycle`, `midday-scan` and `mark-book`; those are real scheduling
decisions for this host. What it requires is that the omissions are DECLARED
here, so adding a job to one scheduler and forgetting the other fails loudly
rather than silently.
"""
import importlib.util
import os
import plistlib
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

# Jobs that live in scheduler.py and are deliberately NOT on this Mac, each
# with the reason. Deleting a line here without deleting the job is what makes
# the test fail — which is the point.
CONTAINER_ONLY = {
    "open-cycle": "second daily entry pass; laptop runs the 15:45 cycle only",
    "midday-scan": "alerts only, never trades; noise on a personal machine",
    "mark-book": "hourly dashboard re-mark; cosmetic, needs an always-on host",
}

# launchd groups the two daily posts into one plist; scheduler.py splits them.
PLIST_COVERS = {
    "dailypost": {"plan-post", "review-post"},
    "restoredrill": {"restore-drill"},
    "newsbrain": {"news-brain"},
    "catchup": {"catch-up"},
    "learn": {"weekly-learn"},
}


def _scheduler_jobs():
    spec = importlib.util.spec_from_file_location(
        "sched_parity", os.path.join(SCRIPTS, "scheduler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {j[0] for j in mod.JOBS}


def _launchd_jobs():
    """Job names covered by the committed plist templates."""
    names = set()
    for f in os.listdir(SCRIPTS):
        if not (f.startswith("com.trading-agent.") and f.endswith(".plist")):
            continue
        stem = f[len("com.trading-agent."):-len(".plist")]
        names |= PLIST_COVERS.get(stem, {stem})
    return names


def test_every_scheduler_job_is_either_deployed_or_declared_container_only():
    """The assertion that would have caught the drift."""
    missing = _scheduler_jobs() - _launchd_jobs() - set(CONTAINER_ONLY)
    assert not missing, (
        f"{sorted(missing)} run in scripts/scheduler.py but have no launchd "
        f"plist and are not declared container-only. Either add a plist or "
        f"add an entry to CONTAINER_ONLY explaining why this host skips it.")


def test_no_launchd_job_is_missing_from_the_container():
    """The mirror image: a plist with no scheduler.py counterpart would run
    here and vanish the moment the agent moves to a server."""
    extra = _launchd_jobs() - _scheduler_jobs()
    assert not extra, (
        f"{sorted(extra)} have a launchd plist but no scripts/scheduler.py "
        f"job — they would silently stop when this moves off the laptop")


def test_backup_and_restore_drill_are_deployed_here():
    """Named explicitly, because these two were the costly omissions.

    Everything else that was container-only is cosmetic or a second entry
    pass. These two are the difference between having a recovery story and
    believing you have one.
    """
    jobs = _launchd_jobs()
    assert "backup" in jobs, "nightly backup is not scheduled on this host"
    assert "restore-drill" in jobs, "the restore drill is not scheduled here"


def test_container_only_declarations_are_real_jobs():
    """A stale exemption is worse than none — it would let a genuinely missing
    job hide behind the name of one that no longer exists."""
    stale = set(CONTAINER_ONLY) - _scheduler_jobs()
    assert not stale, (
        f"{sorted(stale)} are declared container-only but no longer exist in "
        f"scheduler.py — remove the exemption")


@pytest.mark.parametrize("name", ["backup", "restoredrill"])
def test_new_plists_are_valid_and_point_at_a_real_runner(name):
    path = os.path.join(SCRIPTS, f"com.trading-agent.{name}.plist")
    raw = open(path, "rb").read()
    assert b"{{AGENT_ROOT}}" in raw, (
        "plist must keep the placeholder — a hard-coded path silently no-ops "
        "when the checkout moves, which is how the track record was nearly "
        "lost once already")
    data = plistlib.loads(raw.replace(b"{{AGENT_ROOT}}", b"/tmp/agent"))
    assert data["Label"] == f"com.trading-agent.{name}"
    runner = [a for a in data["ProgramArguments"] if a.endswith(".sh")][0]
    local = os.path.join(SCRIPTS, os.path.basename(runner))
    assert os.path.exists(local), f"{runner} does not exist"
    assert os.access(local, os.X_OK), f"{runner} is not executable"


def test_runners_do_not_trade():
    """Backup and the drill touch state; neither may place an order."""
    for f in ("run_backup.sh", "run_restore_drill.sh"):
        body = open(os.path.join(SCRIPTS, f)).read()
        assert "src/main.py" not in body, f"{f} must not run a trading cycle"
