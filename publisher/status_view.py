"""Public status page (Phase E, 2026-07-22).

An honest uptime surface: is the bot actually running? The failure mode this
project fears most is silently NOT running (see HEARTBEAT.md), so the status
page reports the heartbeat, whether trading is halted, and today's fail-open
degradation count — the same signals the watchdog uses.

Read-only (invariant #9): everything comes from health.status(read_only=True),
which resolves the storage backend from cfg without mutating the process-wide
backend and never issues DDL.

Scope is deliberately operational. It reports whether the machine is alive and
whether the rails tripped — not per-symbol positions or reasoning, which are
the subscriber surfaces' job and are tier-gated there.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import dashboard as dash  # noqa: E402 — CSS + escaping helpers only
from publisher.gates import DISCLAIMER  # noqa: E402

_esc = dash._esc


def _age(hours) -> str:
    if hours is None:
        return "never"
    if hours < 1:
        return f"{int(hours * 60)} min ago"
    if hours < 48:
        return f"{hours:.1f} h ago"
    return f"{hours / 24:.1f} days ago"


def render(status: dict) -> str:
    """`status` is the dict from health.status(cfg, read_only=True)."""
    healthy = bool(status.get("healthy"))
    badge_cls = "win" if healthy else "loss"
    badge = "OPERATIONAL" if healthy else "DEGRADED"

    cards = [
        ("mode", status.get("mode", "paper")),
        ("last cycle", (status.get("last_cycle") or "—")[:16].replace("T", " ")),
        ("heartbeat", _age(status.get("heartbeat_age_hours"))),
        ("trading halted", "yes" if status.get("halted") else "no"),
        ("degradations today", str(status.get("degradations_today", 0))),
        ("open positions", str(status.get("open_positions")
                               if status.get("open_positions") is not None
                               else "—")),
    ]
    cards_html = "".join(
        f"<div class=card><div class=v>{_esc(v)}</div>"
        f"<div class=k>{_esc(k)}</div></div>" for k, v in cards)

    problems = status.get("problems") or []
    problems_html = (
        "<h2>current problems</h2><ul class=small>"
        + "".join(f"<li>{_esc(p)}</li>" for p in problems) + "</ul>"
        if problems else
        "<p class=small>No problems reported. A weekday cycle runs at 15:45 ET; "
        "the heartbeat above is written on every exit path, so a stale "
        "heartbeat — not a quiet page — is what a failure looks like.</p>")

    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Repete — status</title>"
        f"<style>{dash.CSS}</style></head><body><div class=wrap>"
        "<h1>Repete — system status</h1>"
        f"<div class=hero><div class=htext>"
        f"<div class=hk>STATUS</div>"
        f"<div class='hv {badge_cls}'>{badge}</div>"
        f"<div class=hs>Paper trading. This page reports whether the bot is "
        f"running, not how it is performing.</div></div></div>"
        f"<div class=cards>{cards_html}</div>"
        f"{problems_html}"
        "<p class=small><a class=x href='/'>home</a> · "
        "<a class=x href='/feed'>free feed</a> · "
        "<a class=x href='/legal/risk'>risk disclosure</a></p>"
        f"<hr><p class=small>{_esc(DISCLAIMER)}</p>"
        "</div></body></html>")
