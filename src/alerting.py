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
JSON POST; the macOS banner stays as a fallback when the webhook is unset, so
laptop behaviour is unchanged.

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
import urllib.error
import urllib.request

log = logging.getLogger("alerting")

WEBHOOK_ENV = "ALERT_WEBHOOK_URL"
PING_ENV = "HEARTBEAT_PING_URL"
TIMEOUT = 10


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
    tests: "webhook" | "desktop" | "log-only". Never raises."""
    url = os.environ.get(WEBHOOK_ENV, "").strip()
    if url:
        try:
            if _post_json(url, {"title": title, "message": message,
                                "source": "repete"}):
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

    Returns "pinged" | "failed" | "disabled". Never raises.
    """
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
