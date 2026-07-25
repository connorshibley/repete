"""Deployment-drift guard (src/deploycheck.py) — §26 divergence #7.

The guard exists because production ran 57 commits behind the reviewed code for
three days and nothing noticed. So the tests that matter are not "does it read
git correctly" — they are:

1. **It can never break a cycle.** Every layer degrades to unknown. A monitoring
   module that raises has made things worse than the problem it watches.
2. **An unknown is NOT an alert.** Containers ship without `.git`; alerting on
   that would fire daily and train the owner to ignore the channel — which is
   precisely how divergence #7 would go unnoticed a second time.
3. **It actually fires on real drift.** A guard never seen to fire is not a
   guard. The §25 split detector was only accepted after being shown to trip on
   an injected split; same standard here.
"""
import subprocess

import pytest

import deploycheck as dc


# ---------------- 1. it can never break a cycle ----------------

@pytest.mark.parametrize("boom", [
    FileNotFoundError("no git binary"),
    subprocess.TimeoutExpired("git", 5),
    OSError("disk gone"),
    Exception("something unforeseen"),
])
def test_status_never_raises_whatever_git_does(monkeypatch, boom):
    def explode(*a, **k):
        raise boom
    monkeypatch.setattr(dc.subprocess, "run", explode)
    monkeypatch.delenv(dc.SHA_ENV, raising=False)

    st = dc.status()
    assert st == {"sha": None, "sha_source": "unknown",
                  "config_dirty": None, "behind": None}


def test_non_zero_git_exit_is_unknown_not_an_error(monkeypatch):
    """Detached HEAD, no upstream ref, not a repo — all just 'cannot tell'."""
    monkeypatch.setattr(dc.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 128, "", "fatal"))
    monkeypatch.delenv(dc.SHA_ENV, raising=False)
    assert dc.running_sha() == (None, "unknown")
    assert dc.config_dirty() is None
    assert dc.behind_upstream() is None


def test_unparseable_behind_count_is_unknown(monkeypatch):
    monkeypatch.setattr(dc.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "not-a-number", ""))
    assert dc.behind_upstream() is None


def test_git_calls_are_bounded_by_a_timeout(monkeypatch):
    """A hung git must not hold a trading cycle open."""
    seen = {}

    def fake(*a, **k):
        seen.update(k)
        return subprocess.CompletedProcess(a, 0, "abc", "")
    monkeypatch.setattr(dc.subprocess, "run", fake)
    dc._git("rev-parse", "HEAD")
    assert seen.get("timeout") == dc._TIMEOUT


# ---------------- the sha, and containers ----------------

def test_env_sha_wins_over_git(monkeypatch):
    """The image ships without .git, so the baked-in sha must take precedence."""
    monkeypatch.setenv(dc.SHA_ENV, "deadbeefcafe")
    monkeypatch.setattr(dc.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "0123456789", ""))
    assert dc.running_sha() == ("deadbeefcafe", "env")


def test_blank_env_sha_falls_through_to_git(monkeypatch):
    monkeypatch.setenv(dc.SHA_ENV, "   ")
    monkeypatch.setattr(dc.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "abc123", ""))
    assert dc.running_sha() == ("abc123", "git")


# ---------------- 3. it fires on real drift ----------------

def _st(sha="a" * 40, source="git", dirty=False, behind=0):
    return {"sha": sha, "sha_source": source,
            "config_dirty": dirty, "behind": behind}


def test_clean_checkout_is_silent():
    assert dc.drift_message(_st()) == ""


def test_dirty_config_alerts_and_says_why():
    """THE case that actually happened: a running config.yaml that is not the
    committed one — max_open_positions 5 against a repo that gated 8."""
    msg = dc.drift_message(_st(dirty=True))
    assert msg
    assert "config.yaml differs" in msg
    assert "gated" in msg
    assert "divergence #7" in msg, "the alert should point at the write-up"


def test_being_behind_upstream_alerts_with_the_count():
    msg = dc.drift_message(_st(behind=57))
    assert "57 commit(s) behind" in msg


def test_both_problems_are_reported_together():
    msg = dc.drift_message(_st(dirty=True, behind=57))
    assert "config.yaml differs" in msg and "57 commit(s) behind" in msg


def test_the_running_sha_is_in_the_alert():
    """An alert that does not say WHICH build is running cannot be acted on."""
    msg = dc.drift_message(_st(sha="1234567890ab" + "f" * 28, dirty=True))
    assert "1234567890ab" in msg


# ---------------- 2. an unknown is NOT an alert ----------------

def test_unknown_state_never_alerts():
    """A container legitimately has no .git. Alerting on that fires every day
    and mutes the channel — the exact failure mode that let #7 persist."""
    assert dc.drift_message({"sha": None, "sha_source": "unknown",
                             "config_dirty": None, "behind": None}) == ""


def test_unknown_behind_with_clean_config_is_silent():
    assert dc.drift_message(_st(behind=None)) == ""


def test_threshold_is_respected():
    assert dc.drift_message(_st(behind=1)) != ""
    assert dc.drift_message(_st(behind=1), behind_threshold=5) == ""
    assert dc.drift_message(_st(behind=5), behind_threshold=5) != ""


# ---------------- it is read-only ----------------

def test_module_cannot_trade_or_write_the_ledger():
    """Structural, like opportunity_scan's no-order test. A monitoring module
    that can write is a monitoring module that can cause the incident.

    Checks CALL syntax, not bare words — an earlier version grepped for
    "checkout" and "commit" and tripped on the prose "the checkout is behind"
    and "57 commit(s) behind". A test that fails on its own docstring teaches
    people to weaken the test."""
    import inspect
    src = inspect.getsource(dc)
    for verb in ("submit_order(", "log_decision(", "log_event(",
                 "market_order(", "bracket_market_order("):
        assert verb not in src, f"deploycheck must not be able to {verb[:-1]}"


def test_only_read_only_git_subcommands_are_used():
    """The rigorous version of the check above: whatever the prose says, these
    are the only subcommands the module can actually invoke. `fetch`, `pull`,
    `checkout` and `reset` all mutate and none may appear here."""
    import inspect
    src = inspect.getsource(dc)
    allowed = {"rev-parse", "status", "rev-list"}
    used = {line.split('_git("')[1].split('"')[0]
            for line in src.splitlines() if '_git("' in line}
    assert used, "found no _git() call sites — has the call syntax changed?"
    assert used <= allowed, f"unexpected git subcommand: {used - allowed}"
