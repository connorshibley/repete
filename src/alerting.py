"""Alerting that survives the laptop (2026-07-23).

The problem this fixes
----------------------
`watchdog.notify()` alerted through `osascript` — a macOS notification banner.
That works when the bot runs on the owner's laptop and nowhere else. Once the
agent moves to a Linux container, `osascript` does not exist, every alert
degrades to a line in a log file nobody reads, and the bot fails exactly as
designed while telling no one. The watchdog was named a "dead-man switch" but
on a server it was a dead switch.

Two directions, and both are needed
-----------------------------------
**PUSH OUT (`send`)** — the agent noticed something and wants to say so:
a missed cycle, a preflight failure, an SLO breach. `ALERT_WEBHOOK_URL` gets a
JSON POST, **shaped to whatever that destination accepts** (`_shape()`) — the
one-size body this used to send is rejected outright by Slack and Discord. The
macOS banner stays as a fallback when the webhook is unset, so laptop behaviour
is unchanged. `channel()` answers which one would carry an alert without
sending one.

**PUSH ALIVE (`heartbeat_ping`)** — nobody noticed anything, which is the
dangerous case. Every check here is *pull-based*: something running on the host
has to observe that the host is broken. If the container dies, the process is
OOM-killed, or the VPS is powered off, there is nothing left to run the
watchdog, and silence is indistinguishable from a quiet market. So the cycle
pings an external uptime service on success (`HEARTBEAT_PING_URL`,
healthchecks.io-style) and **that service alerts the owner when the pings
stop.** An external observer is the only design that survives the host dying.

Both are best-effort by contract: alerting must never raise into a trading
path. A failed alert is logged and swallowed — the alternative is an alerting
bug taking down the thing it was meant to protect.

No secrets are ever placed in an alert body; URLs come from the environment and
are never logged (a healthchecks.io URL is itself a credential).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("alerting")

WEBHOOK_ENV = "ALERT_WEBHOOK_URL"
PING_ENV = "HEARTBEAT_PING_URL"
TIMEOUT = 10

# ---- alerts must never leave a test run or a drill (2026-08-02) -------------
#
# `send()` falls back to an osascript banner when no webhook is set, and nothing
# stopped a pytest run from reaching it. Measured 2026-08-02: one evening's work
# delivered eight real notifications to the owner's phone — three from
# `scripts/halt.py` drills, one from the kill-switch alert added in #75, and four
# "deployment drift" banners fired BY THE TEST SUITE.
#
# Those last four look like the live monitor misbehaving and are not.
# `deploycheck._repo_root()` resolves from the module's own path, so a test in
# `tmp_path` still inspects the REAL repository, while its fresh tmp ledger
# defeats the once-per-day dedupe that keeps the live check quiet. Every suite
# run rang the bell.
#
# An alert channel that cries wolf during development is one the owner learns to
# swipe away, and a muted channel is worse than none — the same argument the
# drift check already makes for its own dedupe.
#
# SUPPRESSION IS DELIBERATELY NARROW. `PYTEST_CURRENT_TEST` is set by pytest per
# test and never exists in production; `REPETE_ALERTS_OFF` must be set by hand.
# Nothing here guesses at its own environment, because silencing a real alert is
# a far worse failure than one spurious banner.
SUPPRESS_ENV = "REPETE_ALERTS_OFF"
# The escape hatch for the tests that exist to exercise delivery ITSELF
# (tests/test_alerting.py, tests/test_alert_delivery.py). It cannot be
# `monkeypatch.delenv("PYTEST_CURRENT_TEST")`: pytest re-sets that variable for
# each phase of each test, so a fixture deleting it during setup has it back
# before the call runs. An explicit opt-in is also the more honest control —
# turning a safety guard off should be something a file says out loud.
FORCE_ENV = "REPETE_ALERTS_FORCE"
_FALSEY = ("", "0", "false", "no")


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() not in _FALSEY


def _suppressed() -> str | None:
    """Why delivery is suppressed, or None to deliver normally."""
    if _truthy(FORCE_ENV):
        return None
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "pytest"
    if _truthy(SUPPRESS_ENV):
        return SUPPRESS_ENV
    return None

# Which bot this is. The three forks share one alert channel, so this is the
# only thing that tells them apart in a notification. repete2 shipped with
# "repete1" here from the fork until 2026-07-30 — an alert that lies about who
# raised it is worse than no alert, because it sends you to the wrong machine.
SOURCE = "repete"


def _shape(url: str, title: str, message: str) -> tuple[str, dict]:
    """Where to POST, and what body that destination will actually accept.

    Added 2026-07-30. `send()` used to post `{title, message, source}` to
    whatever URL it was given, and `.env.example` told the owner that "any
    Slack/Discord/n8n-style incoming webhook works". Two of the three named
    services **reject that body**: Slack requires `text`, Discord requires
    `content`, and both answer a missing field with 400. `send()` then falls
    back to a macOS banner and returns "desktop" — which on the owner's laptop
    is indistinguishable from success. The docs described a delivery that could
    not happen.

    ntfy is the case worth explaining. Its JSON publishing format wants the
    topic IN THE BODY and the request sent to the SERVER ROOT; POST the same
    JSON to `https://ntfy.sh/<topic>` and ntfy takes the raw bytes as the
    message text, so the notification reads as a wall of JSON. Hence the URL
    rewrite here rather than a body-only change.

    The default is deliberately a passthrough: an unrecognised host keeps the
    original `{title, message, source}` contract, so n8n / Zapier / Pipedream
    catch-hooks and every existing test keep working unchanged.

    Self-hosted ntfy on a custom domain is NOT detected and falls through to
    the generic shape. That degrades to an ugly notification, not a silent
    failure, which is the right way round.
    """
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()

    if host == "ntfy.sh" or host.endswith(".ntfy.sh"):
        root = urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
        return root, {"topic": parts.path.strip("/"), "title": title,
                      "message": message, "tags": [SOURCE]}

    if "/api/webhooks/" in parts.path and host in (
            "discord.com", "discordapp.com", "ptb.discord.com",
            "canary.discord.com"):
        return url, {"content": f"**{title}**\n{message}\n_{SOURCE}_"}

    if host == "hooks.slack.com":
        return url, {"text": f"*{title}*\n{message}\n_{SOURCE}_"}

    return url, {"title": title, "message": message, "source": SOURCE}


def channel() -> str:
    """Which channel WOULD carry an alert right now, without sending one.

    Ported from repete1 (§24). `send()` already returned this, but only as a
    side effect of alerting — so the one moment you could discover the channel
    reached nobody was during an incident, via a notification you were not
    going to receive. This makes it a question that can be asked in advance.

    Deliberately does NOT probe: no webhook request, no banner. A health check
    that fired a real alert to test alerting would page the operator every time
    anything asked whether alerting worked.

    The consequence worth stating: **"desktop" is honest on this laptop and a
    lie on a server.** It is also a lie on a sleeping laptop, which is the case
    that actually applies here.
    """
    if os.environ.get(WEBHOOK_ENV, "").strip():
        return "webhook"
    if sys.platform == "darwin":
        return "desktop"
    return "log-only"


def _post_json(url: str, payload: dict) -> bool:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return 200 <= resp.status < 300


def _macos_banner(title: str, message: str) -> bool:
    """Best-effort desktop notification. Absent on any non-mac host."""
    r = subprocess.run(
        ["osascript", "-e",
         f'display notification "{message}" with title "{title}"'],
        check=False, capture_output=True, timeout=TIMEOUT)
    return r.returncode == 0


def send(title: str, message: str) -> str:
    """Raise an operator alert. Returns which channel carried it, for logs and
    tests: "webhook" | "desktop" | "log-only" | "suppressed". Never raises."""
    why = _suppressed()
    if why:
        # Logged, not silent. A suppressed alert still has to be findable —
        # "nothing was delivered" must be a recorded fact rather than an
        # absence, or this guard becomes indistinguishable from a broken
        # channel the next time someone asks why no alert arrived.
        log.info("alert suppressed (%s) — %s: %s", why, title, message)
        return "suppressed"

    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if url:
        try:
            if _post_json(*_shape(url, title, message)):
                return "webhook"
            log.warning("alert webhook returned a non-2xx status")
        except (urllib.error.URLError, OSError, ValueError) as e:
            # Never log the URL itself — it is a credential.
            log.warning("alert webhook failed: %s", type(e).__name__)

    try:
        if _macos_banner(title, message):
            return "desktop"
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("desktop notification unavailable: %s", type(e).__name__)

    # Nothing reached a human. Say so loudly in the log, which is all that is
    # left — and is exactly the situation ALERT_WEBHOOK_URL exists to prevent.
    log.critical("ALERT (no channel available) — %s: %s", title, message)
    return "log-only"


def heartbeat_ping(success: bool = True) -> str:
    """Tell the external uptime monitor the cycle completed.

    Configure `HEARTBEAT_PING_URL` with a healthchecks.io-style check URL. The
    monitor alerts the OWNER when pings stop, which is the only way a dead host
    ever gets noticed. `success=False` appends `/fail` so a cycle that raised
    is recorded as a failure rather than silently skipped — a check that is
    merely late looks identical to one that is broken otherwise.

    Returns "pinged" | "failed" | "disabled" | "suppressed". Never raises.

    SUPPRESSED UNDER TEST FOR A SHARPER REASON THAN send()'s. A ping says "the
    cycle completed". A suite run that reached this would tell healthchecks.io
    the bot is alive FROM A LAPTOP, holding the monitor green while the real
    deployment was dead — defeating the one check designed to survive the host
    dying, and doing it silently. Noise is the failure mode above; here it is a
    false all-clear.
    """
    why = _suppressed()
    if why:
        log.info("heartbeat ping suppressed (%s), success=%s", why, success)
        return "suppressed"

    url = os.environ.get(PING_ENV, "").strip()
    if not url:
        return "disabled"
    if not success:
        url = url.rstrip("/") + "/fail"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            if 200 <= resp.status < 300:
                return "pinged"
            log.warning("heartbeat ping returned a non-2xx status")
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.warning("heartbeat ping failed: %s", type(e).__name__)
    return "failed"
