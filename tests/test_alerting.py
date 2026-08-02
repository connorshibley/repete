"""Alerting that survives the laptop (src/alerting.py).

The failure this guards against is quiet by nature: on a Linux host the old
osascript-only notifier turned every alert into a log line nobody reads, so the
watchdog looked healthy while being useless. These tests assert the channel
actually chosen, not just that nothing raised.
"""

import pytest

import alerting

# Captured at import, BEFORE conftest._no_real_alerts swaps them per-test. This
# file tests the delivery primitives themselves, so it needs the genuine ones —
# see clean_env below.
_REAL_BANNER = alerting._macos_banner
_REAL_POST = alerting._post_json


class FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    monkeypatch.delenv(alerting.PING_ENV, raising=False)
    # THIS FILE IS THE ONE PLACE THAT OPTS OUT OF ALERT SUPPRESSION.
    #
    # `send()` and `heartbeat_ping()` refuse to deliver while
    # `PYTEST_CURRENT_TEST` is set (2026-08-02, after the suite paged the owner
    # eight times in one evening). Every other test wants exactly that. These
    # tests exist to assert WHICH CHANNEL is chosen, so blanket suppression
    # would turn the whole file green while testing nothing — the shape of the
    # 2026-07-27 audit failure, where a null risk engine left 13 tests passing.
    #
    # Delivery still cannot escape: conftest._no_real_alerts stubs
    # `_macos_banner` and `_post_json` to RAISE, and each test below installs
    # its own fake. The env is opted out of; the wire is not.
    #
    # Deleting PYTEST_CURRENT_TEST does not work — pytest re-sets it for every
    # phase of every test, so it is back before the call runs. Hence the
    # explicit FORCE flag.
    monkeypatch.setenv(alerting.FORCE_ENV, "1")
    monkeypatch.delenv(alerting.SUPPRESS_ENV, raising=False)
    # And put the real primitives back, since conftest replaced them with
    # raisers. Nothing reaches a human anyway: every test below either stubs
    # `urlopen`/`subprocess` underneath them or asserts on a failure path. What
    # is restored is the CODE UNDER TEST, not the ability to notify.
    monkeypatch.setattr(alerting, "_macos_banner", _REAL_BANNER)
    monkeypatch.setattr(alerting, "_post_json", _REAL_POST)


# ---------------- send() ----------------

def test_webhook_is_preferred_when_configured(monkeypatch):
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = req.data.decode()
        return FakeResponse(200)

    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/abc")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    assert alerting.send("title", "message") == "webhook"
    assert sent["url"] == "https://hooks.example/abc"
    assert "message" in sent["body"]


def test_falls_back_to_desktop_when_no_webhook(monkeypatch):
    monkeypatch.setattr(alerting, "_macos_banner", lambda t, m: True)
    assert alerting.send("t", "m") == "desktop"


def test_webhook_failure_falls_back_to_desktop(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/abc")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", boom)
    monkeypatch.setattr(alerting, "_macos_banner", lambda t, m: True)
    assert alerting.send("t", "m") == "desktop"


def test_linux_container_with_no_webhook_reports_log_only(monkeypatch):
    """The exact pre-fix situation: no osascript, no webhook. It must be
    reported as log-only rather than quietly claiming success."""
    def no_osascript(*a, **k):
        raise FileNotFoundError("osascript")

    monkeypatch.setattr(alerting.subprocess, "run", no_osascript)
    assert alerting.send("t", "m") == "log-only"


def test_non_2xx_webhook_is_not_treated_as_delivered(monkeypatch):
    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/abc")
    monkeypatch.setattr(alerting.urllib.request, "urlopen",
                        lambda req, timeout=None: FakeResponse(500))
    monkeypatch.setattr(alerting, "_macos_banner", lambda t, m: False)
    assert alerting.send("t", "m") == "log-only"


def test_send_never_raises(monkeypatch):
    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/abc")
    monkeypatch.setattr(alerting.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(alerting.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    assert alerting.send("t", "m") == "log-only"


def test_webhook_url_is_never_logged(monkeypatch, caplog):
    """The webhook URL is a credential — a failure must not leak it."""
    secret = "https://hooks.example/SUPERSECRETTOKEN"
    monkeypatch.setenv(alerting.WEBHOOK_ENV, secret)
    monkeypatch.setattr(alerting.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr(alerting, "_macos_banner", lambda t, m: True)
    with caplog.at_level("DEBUG"):
        alerting.send("t", "m")
    assert "SUPERSECRETTOKEN" not in caplog.text


# ---------------- heartbeat_ping() ----------------

def test_ping_disabled_without_url():
    assert alerting.heartbeat_ping() == "disabled"


def test_ping_hits_the_configured_url(monkeypatch):
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        return FakeResponse(200)

    monkeypatch.setenv(alerting.PING_ENV, "https://hc.example/uuid")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    assert alerting.heartbeat_ping() == "pinged"
    assert seen["url"] == "https://hc.example/uuid"


def test_failed_cycle_pings_the_fail_endpoint(monkeypatch):
    """A cycle that raised must be recorded as a FAILURE, not merely omitted —
    otherwise a broken cycle and a late one look identical to the monitor."""
    seen = {}

    def fake_urlopen(url, timeout=None):
        seen["url"] = url
        return FakeResponse(200)

    monkeypatch.setenv(alerting.PING_ENV, "https://hc.example/uuid/")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    assert alerting.heartbeat_ping(success=False) == "pinged"
    assert seen["url"] == "https://hc.example/uuid/fail"


def test_ping_failure_never_raises(monkeypatch):
    monkeypatch.setenv(alerting.PING_ENV, "https://hc.example/uuid")
    monkeypatch.setattr(alerting.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert alerting.heartbeat_ping() == "failed"


# ---------------- wiring ----------------

def test_watchdog_notify_delegates_to_alerting(monkeypatch):
    """All four alert call sites go through watchdog.notify, so this is the
    single seam that decides whether a server-side alert reaches a human."""
    import watchdog
    calls = []
    monkeypatch.setattr(alerting, "send",
                        lambda t, m: calls.append((t, m)) or "webhook")
    assert watchdog.notify("title", "body") == "webhook"
    assert calls == [("title", "body")]
