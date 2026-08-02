"""A test run must never page a human.

Why this file exists (2026-08-02)
---------------------------------
In one evening the suite delivered EIGHT real notifications to the owner's
phone. Three came from `scripts/halt.py` drills, one from the kill-switch alert
added in #75, and four were "deployment drift" banners fired **by the test
suite itself**.

Those four are worth understanding, because the obvious diagnosis is wrong. The
live drift check is deliberately deduped — *"ONE alert per day. A per-cycle
alert on a condition that persists for days is how an alert channel gets muted"*
— and that dedupe works. But `deploycheck._repo_root()` resolves from the
module's own path, so a test running in `tmp_path` still inspects the REAL
repository, while its fresh tmp ledger means the once-per-day check never sees a
prior `deploy_drift` event. Every suite run rang the bell.

So the hole was never the dedupe. It was that tests could reach the delivery
channel at all.

The danger of the fix is the opposite one: a guard that silences alerts is a
guard that can silence REAL alerts. So every test here is paired — suppressed
under test, delivering when not — and `tests/test_alerting.py` keeps exercising
real channel selection behind an explicit opt-in.
"""
import subprocess
import sys

import pytest

import alerting


@pytest.fixture
def delivering(monkeypatch):
    """Undo suppression, as the delivery-focused test files do."""
    monkeypatch.setenv(alerting.FORCE_ENV, "1")
    monkeypatch.delenv(alerting.SUPPRESS_ENV, raising=False)


# ------------------------------------------------------- suppressed under test

def test_send_is_suppressed_while_pytest_is_running():
    """The default state of every test in this repo."""
    assert alerting.send("title", "body") == "suppressed"


def test_heartbeat_ping_is_suppressed_while_pytest_is_running(monkeypatch):
    """Sharper than the banner case, and the reason this is not cosmetic.

    A ping means "the cycle completed". A suite run reaching healthchecks.io
    from a laptop would hold the monitor GREEN while the real deployment was
    dead — defeating the one check designed to survive the host dying, and
    doing it silently. Noise is the failure above; this would be a false
    all-clear.
    """
    monkeypatch.setenv(alerting.PING_ENV, "https://hc-ping.example/abc")
    assert alerting.heartbeat_ping(True) == "suppressed"
    assert alerting.heartbeat_ping(False) == "suppressed"


def test_no_test_can_reach_the_desktop_or_the_network(delivering):
    """conftest._no_real_alerts is the second layer, independent of the env
    guard: it replaces the delivery primitives with raisers, so even a caller
    that has opted OUT of suppression still cannot reach a human by accident."""
    with pytest.raises(AssertionError, match="desktop notification"):
        alerting._macos_banner("t", "m")
    with pytest.raises(AssertionError, match="alert webhook"):
        alerting._post_json("https://example.invalid", b"{}", {})


# ------------------------------------- but the guard must not silence PRODUCTION

def test_without_the_guard_a_real_channel_is_still_chosen(delivering,
                                                          monkeypatch):
    """THE test that keeps this from becoming a global mute.

    If suppression leaked into production the bot would fail exactly as designed
    while telling nobody — the failure `src/alerting.py` was written to end. So
    prove the other direction: with the flags cleared, `send()` still selects a
    channel and still calls it.
    """
    calls = []
    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/xyz")
    monkeypatch.setattr(alerting, "_post_json",
                        lambda *a, **k: calls.append(a) or True)
    assert alerting.send("t", "m") == "webhook"
    assert calls, "the webhook was never actually called"


def test_the_off_switch_works_outside_pytest_too(monkeypatch):
    """`REPETE_ALERTS_OFF` is what a manual drill sets. Proven in a SUBPROCESS,
    because inside pytest `PYTEST_CURRENT_TEST` would suppress anyway and the
    test would pass without the variable doing anything."""
    prog = (
        "import os, sys; sys.path.insert(0, 'src');"
        "os.environ.pop('PYTEST_CURRENT_TEST', None);"
        "import alerting;"
        "print(alerting._suppressed())"
    )
    root = __file__.rsplit("/tests/", 1)[0]

    off = subprocess.run([sys.executable, "-c", prog], cwd=root,
                         capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin",
                              alerting.SUPPRESS_ENV: "1"})
    assert off.stdout.strip() == alerting.SUPPRESS_ENV, off.stderr

    on = subprocess.run([sys.executable, "-c", prog], cwd=root,
                        capture_output=True, text=True,
                        env={"PATH": "/usr/bin:/bin"})
    assert on.stdout.strip() == "None", on.stderr


@pytest.mark.parametrize("value,suppressed", [
    ("1", True), ("true", True), ("yes", True), ("anything", True),
    ("", False), ("0", False), ("false", False), ("no", False),
])
def test_the_off_switch_reads_falsey_values_as_off(monkeypatch, value,
                                                   suppressed):
    """`REPETE_ALERTS_OFF=0` must mean OFF, not "the variable is present".
    Getting that backwards is how a stale export silences a live bot."""
    monkeypatch.setenv(alerting.FORCE_ENV, "0")      # not forcing
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv(alerting.SUPPRESS_ENV, value)
    # PYTEST_CURRENT_TEST is re-set by pytest per phase, so assert on the
    # env-var branch directly rather than on the whole predicate.
    assert alerting._truthy(alerting.SUPPRESS_ENV) is suppressed


def test_a_suppressed_alert_is_still_logged(caplog):
    """Silence must be a recorded fact, not an absence. Otherwise the guard is
    indistinguishable from a broken channel the next time someone asks why no
    alert arrived."""
    import logging
    with caplog.at_level(logging.INFO, logger="alerting"):
        alerting.send("KILL SWITCH could not flatten", "SPY, QQQ still open")
    joined = " ".join(r.message for r in caplog.records)
    assert "suppressed" in joined
    assert "KILL SWITCH could not flatten" in joined
