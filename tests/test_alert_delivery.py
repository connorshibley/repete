"""Does an alert actually REACH a human? (2026-07-30)

Two independent failures made the answer "no" while every surface said "yes":

  1. `send()` posted one fixed body to any URL. Slack requires `text`, Discord
     requires `content`; both 400. `send()` then falls back to a macOS banner
     and returns "desktop", which on the owner's laptop is indistinguishable
     from success.
  2. `src/watchdog.py` — the PRIMARY alerter — had no `load_dotenv()`, and
     launchd hands a process a bare environment. So `ALERT_WEBHOOK_URL` was
     invisible to it no matter what `.env` said.

(2) is why the last test here runs the real entrypoint as a subprocess with a
CLEARED environment and a local HTTP receiver, rather than asserting that the
source contains `load_dotenv`. A grep passes on a call that never runs; the
bug was precisely a call that never ran.
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import alerting

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_REAL_BANNER = alerting._macos_banner
_REAL_POST = alerting._post_json


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    monkeypatch.delenv(alerting.PING_ENV, raising=False)
    # Same opt-out as tests/test_alerting.py — this file tests webhook SHAPING
    # and delivery, which blanket suppression would turn green while proving
    # nothing. See src/alerting.FORCE_ENV. `urlopen` is stubbed per test, so
    # restoring the real primitives restores the code under test, not the
    # ability to reach anyone.
    monkeypatch.setenv(alerting.FORCE_ENV, "1")
    monkeypatch.delenv(alerting.SUPPRESS_ENV, raising=False)
    monkeypatch.setattr(alerting, "_macos_banner", _REAL_BANNER)
    monkeypatch.setattr(alerting, "_post_json", _REAL_POST)


# ---------------- _shape(): what each destination will accept ----------------

def test_ntfy_posts_to_the_root_with_the_topic_in_the_body():
    """The rewrite that makes ntfy render a notification instead of raw JSON.

    Posting this body to https://ntfy.sh/<topic> returns 200 and shows the JSON
    as the message text — a "working" webhook that is unreadable on a phone.
    """
    url, payload = alerting._shape(
        "https://ntfy.sh/repete-alerts-7f3a91c2", "title", "message")
    assert url == "https://ntfy.sh/"
    assert payload["topic"] == "repete-alerts-7f3a91c2"
    assert payload["title"] == "title"
    assert payload["message"] == "message"


def test_ntfy_subdomain_is_recognised():
    url, payload = alerting._shape("https://eu.ntfy.sh/topicname", "t", "m")
    assert url == "https://eu.ntfy.sh/"
    assert payload["topic"] == "topicname"


def test_discord_gets_content_which_is_the_field_it_requires():
    url, payload = alerting._shape(
        "https://discord.com/api/webhooks/123/abc", "the title", "the body")
    assert url == "https://discord.com/api/webhooks/123/abc"
    assert "content" in payload
    assert "the title" in payload["content"]
    assert "the body" in payload["content"]
    assert "title" not in payload      # the old shape is what Discord rejected


def test_slack_gets_text_which_is_the_field_it_requires():
    _, payload = alerting._shape(
        "https://hooks.slack.com/services/T0/B0/xyz", "the title", "the body")
    assert "text" in payload
    assert "the title" in payload["text"]
    assert "content" not in payload


def test_an_unknown_host_keeps_the_original_contract():
    """Back-compat is load-bearing: n8n/Zapier catch-hooks and every existing
    test in test_alerting.py depend on the old body reaching an unrecognised
    endpoint unchanged."""
    url, payload = alerting._shape("https://hooks.example/abc", "t", "m")
    assert url == "https://hooks.example/abc"
    assert payload == {"title": "t", "message": "m", "source": alerting.SOURCE}


def test_a_discord_lookalike_host_is_not_treated_as_discord():
    """`/api/webhooks/` in the path is not enough — the host has to match, or a
    generic receiver that happens to use that path gets a Discord body."""
    _, payload = alerting._shape(
        "https://evil.example/api/webhooks/1/2", "t", "m")
    assert payload == {"title": "t", "message": "m", "source": alerting.SOURCE}


def test_every_shape_carries_the_source_so_three_bots_stay_distinguishable():
    """One channel serves trading-agent, repete1 and repete2. An alert that
    does not say which bot raised it sends you to the wrong machine."""
    for url in ("https://ntfy.sh/atopicname12",
                "https://discord.com/api/webhooks/1/2",
                "https://hooks.slack.com/services/T/B/x",
                "https://hooks.example/abc"):
        _, payload = alerting._shape(url, "t", "m")
        assert alerting.SOURCE in json.dumps(payload), url


def test_send_uses_the_shaped_url_not_the_configured_one(monkeypatch):
    """End to end through send(): the ntfy URL rewrite must survive the call,
    not just exist in a helper nothing routes through."""
    sent = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["body"] = json.loads(req.data.decode())
        return FakeResponse()

    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://ntfy.sh/atopicname12")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", fake_urlopen)
    assert alerting.send("t", "m") == "webhook"
    assert sent["url"] == "https://ntfy.sh/"
    assert sent["body"]["topic"] == "atopicname12"


# ---------------- channel(): ask without paging ----------------

def test_channel_reports_webhook_without_sending_anything(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("channel() must not probe — that would page the "
                             "operator every time anything asked")

    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://hooks.example/abc")
    monkeypatch.setattr(alerting.urllib.request, "urlopen", explode)
    monkeypatch.setattr(alerting, "_macos_banner", explode)
    assert alerting.channel() == "webhook"


def test_channel_is_honest_when_no_webhook_is_set(monkeypatch):
    monkeypatch.setattr(alerting.sys, "platform", "linux")
    assert alerting.channel() == "log-only"
    monkeypatch.setattr(alerting.sys, "platform", "darwin")
    assert alerting.channel() == "desktop"


# ---------------- the watchdog entrypoint, under launchd's environment -------

class _Receiver(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's name
        n = int(self.headers.get("Content-Length", 0))
        _Receiver.received.append(self.rfile.read(n).decode())
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture
def receiver():
    _Receiver.received = []
    srv = HTTPServer(("127.0.0.1", 0), _Receiver)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, _Receiver.received
    srv.shutdown()


def test_watchdog_entrypoint_reads_dotenv_and_delivers(tmp_path, receiver):
    """THE regression test for the bug this change exists to fix.

    `run_watchdog.sh` runs `python src/watchdog.py` under launchd, which
    supplies no shell profile and no .env. Before 2026-07-30 that meant
    ALERT_WEBHOOK_URL was invisible to the watchdog, so it fired macOS banners
    forever while .env claimed a webhook was configured.

    Run for real: cleared environment, a temp cwd holding only .env and HALT,
    and a local receiver. HALT is used because `check()` reports it on any day
    — a weekday-only trigger would make this pass or fail by calendar.
    """
    srv, received = receiver
    port = srv.server_address[1]
    (tmp_path / ".env").write_text(
        f"ALERT_WEBHOOK_URL=http://127.0.0.1:{port}/hook\n")
    (tmp_path / "HALT").write_text("test\n")

    r = subprocess.run(
        [sys.executable, os.path.join(REPO, "src", "watchdog.py")],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=120)

    assert received, (
        "the watchdog raised no webhook alert — it could not see .env "
        f"(rc={r.returncode})\nstderr:\n{r.stderr[-2000:]}")
    # One POST per problem. HALT is the one asserted because it is the only
    # problem that fires on any day of the week; on a weekday the missing
    # heartbeat is reported too, and asserting on received[0] would make this
    # pass or fail by calendar — the trap the fixture was chosen to avoid.
    assert any("HALT" in body for body in received), received
    for body in received:
        assert json.loads(body)["source"] == alerting.SOURCE


def test_the_receiver_would_notice_if_nothing_was_sent(tmp_path, receiver):
    """Negative control, in-file: the same run WITHOUT a .env must deliver
    nothing. Without this, the test above could pass on a receiver that
    recorded requests from anywhere."""
    srv, received = receiver
    (tmp_path / "HALT").write_text("test\n")
    subprocess.run(
        [sys.executable, os.path.join(REPO, "src", "watchdog.py")],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=120)
    assert received == []


def test_the_webhook_url_is_never_logged_by_the_shaping_path(monkeypatch,
                                                             caplog):
    """Extends test_log_redaction.py:82 to the new code path — the URL is a
    credential and _shape() now handles it too."""
    secret = "https://ntfy.sh/repete-alerts-7f3a91c2"

    def boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setenv(alerting.WEBHOOK_ENV, secret)
    monkeypatch.setattr(alerting.urllib.request, "urlopen", boom)
    monkeypatch.setattr(alerting, "_macos_banner", lambda t, m: False)
    with caplog.at_level("DEBUG"):
        alerting.send("t", "m")
    assert "repete-alerts-7f3a91c2" not in caplog.text


def test_load_env_NEVER_reaches_the_operators_real_dotenv(tmp_path, monkeypatch):
    """Regression for 2026-08-22: `load_env()` fell back to the repo-root
    `.env` when the cwd had none, so the negative control above — run from a
    temp cwd — loaded the OPERATOR'S real webhook URL and posted false
    "Trading agent needs attention" alerts to the real ntfy topic on every suite run. The test stayed
    green because it only watched its local receiver. Found in repete2 by
    polling the topic: ~120 messages in 12h, across all three bots.

    Only has teeth on a host that HAS a repo .env (the laptop); on CI there is
    nothing to leak, and the skip says so rather than hiding it.
    """
    import pytest
    if not os.path.isfile(os.path.join(REPO, ".env")):
        pytest.skip("no repo .env on this host — the leak cannot be exercised here")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    import watchdog as watchdog_mod
    loaded = watchdog_mod.load_env()
    assert loaded is None, f"load_env reached outside the cwd: {loaded}"
    assert not os.environ.get(alerting.WEBHOOK_ENV), (
        "the operator's real ALERT_WEBHOOK_URL leaked into a process whose "
        "cwd holds no .env")


def test_load_env_STILL_loads_the_cwd_dotenv(tmp_path, monkeypatch):
    """Positive direction, or the test above passes on a load_env that loads
    nothing at all."""
    (tmp_path / ".env").write_text("ALERT_WEBHOOK_URL=http://127.0.0.1:1/x\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    import watchdog as watchdog_mod
    assert watchdog_mod.load_env() == str(tmp_path / ".env")
    assert os.environ.get(alerting.WEBHOOK_ENV) == "http://127.0.0.1:1/x"
