"""Finish a flatten the kill switch started and could not complete.

Why this exists (2026-08-02)
----------------------------
`_kill_switch_fired` engages HALT and then flattens. If the flatten raised — a
broker timeout, an API outage — it logged `kill_switch_flatten_failed` and
returned. The halt is `freeze`, so no later cycle ran, so **nothing ever
retried**. The book sat open after a 5% daily loss with only its broker-side
bracket legs managing it, and the only path out was a human noticing.

PR #73 documented that as the worst state in the system and left it manual on
purpose. This automates it.

WHAT THIS CAN DO, AND ONLY THIS
-------------------------------
Cancel orders and close positions, when a kill-switch flatten is already
pending. It is not a new reason to sell — it finishes a job the operator's own
daily-loss rail started. There is no entry path in this module and
`test_flatten_recovery.py` walks the AST to prove it.

TWO THINGS IT REFUSES TO DO
---------------------------
1. Run without a pending marker. A manual `--freeze` halt never sets one, so
   halting because the broker looks wrong does NOT hand the broker a
   liquidation order.
2. Trust a flatten that did not raise. `broker.flatten_all()` is
   `cancel_orders()` + `close_all_positions()`, and Alpaca's close-all returns
   per-position results rather than raising when only some of them close. Until
   now a flatten that closed 3 of 5 positions was recorded as a complete one,
   because nothing re-read the book. Here success means the BROKER says there
   are no positions left (invariant #4), not that no exception surfaced.
"""
import logging
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import risk

log = logging.getLogger("agent.flatten_recovery")

DEFAULT_MAX_ATTEMPTS = 8          # ~2 hours at the 15-minute retry cadence

ET = ZoneInfo("America/New_York")
_OPEN, _CLOSE = dtime(9, 30), dtime(16, 0)


def within_retry_window(now: datetime | None = None) -> bool:
    """Regular US session, weekdays. Attempts outside it are refused.

    The window is enforced HERE rather than in the launchd calendar because the
    plist and `scheduler.py` would otherwise each carry their own copy of it,
    and a `StartCalendarInterval` that omits `Hour` silently means *every* hour
    — a schedule whose comment says "session only" while it fires at 03:00.
    One definition, both schedulers, §29's rule.

    Why it matters beyond tidiness: a market order sent pre-open or overnight is
    rejected for reasons that have nothing to do with the outage being retried,
    and it would still SPEND an attempt from a budget meant to measure whether
    the broker is taking orders. Holidays are not modelled — a rejected attempt
    on Thanksgiving costs one of eight, and the alert says what happened.
    """
    now = now or datetime.now(ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    now = now.astimezone(ET)
    return now.weekday() < 5 and _OPEN <= now.timetz().replace(tzinfo=None) < _CLOSE


def max_attempts(cfg: dict) -> int:
    raw = ((cfg.get("risk") or {}).get("kill_switch") or {}).get("max_attempts")
    if raw is None:
        return DEFAULT_MAX_ATTEMPTS
    try:
        val = int(raw)
    except (TypeError, ValueError):
        log.warning("risk.kill_switch.max_attempts is %r — using %d",
                    raw, DEFAULT_MAX_ATTEMPTS)
        return DEFAULT_MAX_ATTEMPTS
    if val <= 0:
        log.warning("risk.kill_switch.max_attempts is %r — using %d",
                    raw, DEFAULT_MAX_ATTEMPTS)
        return DEFAULT_MAX_ATTEMPTS
    return val


def auto_retry_enabled(cfg: dict) -> bool:
    raw = ((cfg.get("risk") or {}).get("kill_switch") or {}).get(
        "auto_retry_flatten")
    return True if raw is None else bool(raw)


def flatten_and_verify(broker) -> dict:
    """Flatten, then ask the BROKER what is left. Returns remaining positions.

    An empty dict is the only evidence of success this repo accepts. A raising
    flatten and a silently partial one are the same event as far as the caller
    is concerned — positions remain — and collapsing them here means no caller
    can accidentally treat "no exception" as "flat".
    """
    try:
        broker.flatten_all()
    except Exception as e:  # noqa: BLE001 — the verify below is what decides
        log.critical("flatten_all raised: %s", e)
    try:
        return broker.positions() or {}
    except Exception as e:  # noqa: BLE001
        # Cannot see the book, so cannot claim it is flat. Reported as "still
        # populated" via a sentinel the caller logs; assuming success here is
        # exactly the mistake this module exists to stop.
        log.critical("positions() unreadable after flatten: %s", e)
        return {"<unknown>": {"detail": f"positions() failed: {e}"}}


def alert(title: str, body: str):
    try:
        from watchdog import notify
        notify(title, body)
    except Exception as e:  # noqa: BLE001 — an alert must never be the failure
        log.warning("alert delivery failed: %s", e)


def run(broker, ledger, cfg: dict) -> str:
    """One recovery attempt.

    Returns one of: 'not_pending', 'disabled', 'outside_window', 'recovered',
    'retrying', 'abandoned'. The string is the whole result — callers log it,
    they do not branch on side effects.
    """
    if not (risk.check_halt() and risk.flatten_pending()):
        return "not_pending"
    if not auto_retry_enabled(cfg):
        return "disabled"
    if not within_retry_window():
        return "outside_window"

    cap = max_attempts(cfg)
    if risk.flatten_attempts() >= cap:
        # Already abandoned on a previous run. Do NOT keep counting and do NOT
        # keep alerting — an alert that repeats every 15 minutes forever is one
        # an operator learns to swipe away, which is worse than none.
        return "abandoned"

    n = risk.record_flatten_attempt()
    remaining = flatten_and_verify(broker)

    if not remaining:
        risk.clear_flatten_pending()
        ledger.log_event("kill_switch_flatten_recovered",
                         f"book is flat after {n} recovery attempt(s); "
                         f"HALT remains engaged")
        alert("Repete: flatten recovered",
              f"The kill-switch flatten completed on attempt {n}. "
              f"No positions remain.\n\n"
              f"HALT is STILL ENGAGED — clearing it is your call.")
        return "recovered"

    names = ", ".join(sorted(remaining))
    if n >= cap:
        ledger.log_event("kill_switch_flatten_abandoned",
                         f"{n} attempts exhausted; still open: {names}")
        alert("Repete: FLATTEN FAILED — manual action needed",
              f"{n} automatic attempts have failed. These positions are STILL "
              f"OPEN after a daily-loss kill switch:\n\n  {names}\n\n"
              f"Automatic retries have stopped. Close them at the broker "
              f"yourself. Clearing HALT also cancels the pending flatten.")
        return "abandoned"

    ledger.log_event("kill_switch_flatten_retry",
                     f"attempt {n}/{cap} failed; still open: {names}")
    alert("Repete: flatten retry failed",
          f"Attempt {n} of {cap} did not clear the book.\n\n"
          f"Still open: {names}")
    return "retrying"


def main() -> int:
    """CLI / scheduled entry point. Exits 0 unless a human must act.

    Deliberately silent and cheap when nothing is pending, because it runs every
    15 minutes and the overwhelmingly common case is that there is no incident.
    """
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import yaml
    from dotenv import load_dotenv

    if not (risk.check_halt() and risk.flatten_pending()):
        return 0                       # the common case: nothing to do, no cost

    load_dotenv()
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    import store
    store.configure(cfg)
    from broker import Broker
    from ledger import Ledger

    outcome = run(Broker(cfg), Ledger(cfg["memory"]["ledger_path"]), cfg)
    print(f"flatten recovery: {outcome}")
    return 1 if outcome == "abandoned" else 0


if __name__ == "__main__":
    raise SystemExit(main())
