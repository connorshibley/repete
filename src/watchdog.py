"""Dead-man watchdog — the bot must fail LOUDLY when it runs unattended.

Scheduled ~30 min after the daily cycle (see scripts/
com.trading-agent.watchdog.plist). Checks FOUR things and alerts via an
operator notification + log + ledger event when any of them fails:

  1. HEARTBEAT — did the PROCESS run? memory/heartbeat (written on every
     run_cycle exit path) must be from today (local). Missing/old on a weekday
     means the cycle never started — launchd broke, the venv broke, or the
     machine slept through it.
  2. CYCLE_COMPLETE — did the cycle FINISH? A `cycle_complete` record dated
     today must exist in the ledger.
  3. MARKET_CONTEXT — did it KNOW anything? Added 2026-07-29 (W6-A3), after the
     10:34 ET news refresh lost all eleven RSS feeds and the Alpaca wire to DNS
     failure and said so in one INFO line nobody reads. Checked only once the
     cycle completed, so a broken cycle reports its root cause rather than two
     symptoms. ALERT ONLY — news is fail-soft and never gates trading.
  4. HALT: if the kill-switch HALT file exists, keep reminding the owner
     every day until they deal with it.

Check 2 exists because check 1 cannot see a cycle that started and died.
`write_heartbeat()` runs in a `finally:`, so a crashed cycle stamps a FRESH
heartbeat on its way out and reads as healthy. That is what happened on Friday
2026-07-24: the 15:45 cycle died about six seconds in, this watchdog said
nothing, `catchup` used the same test and so silenced its own recovery, and the
EOD post read exactly like a quiet market. It cost a full trading day and went
unnoticed for two.

Added 2026-07-26. `check()`'s own docstring has described all three since; THIS
docstring said "two things" until 2026-07-29 (W5-5) — the file contradicted
itself, and the stale half was the one you read first.

Alerts degrade gracefully (a notification failure still logs); the watchdog
itself never raises.
"""
import logging
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger("watchdog")


def configure_logging() -> None:
    """Attach file handlers. Called from __main__ only — see the note in
    main.configure_logging() for why import-time setup polluted the real
    production logs during every test run."""
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("logs/agent.log", mode="a")],
        force=True,
    )
    import log as structlog
    structlog.redact_existing_handlers()

HEARTBEAT_FILE = "memory/heartbeat"
HALT_FILE = "HALT"


def notify(title: str, message: str):
    """Raise an operator alert through whatever channel exists on this host.

    Was an `osascript` banner — which meant that once the agent moved off the
    laptop, every alert went to a log file nobody reads. Now delegates to
    `alerting.send()`: webhook when `ALERT_WEBHOOK_URL` is set, desktop banner
    otherwise, so laptop behaviour is unchanged. Never raises."""
    try:
        import alerting
        return alerting.send(title, message)
    except Exception as e:  # noqa: BLE001 — alerting must not crash the alerter
        log.warning("notification failed: %s", e)
        return "log-only"


def heartbeat_date(path: str = HEARTBEAT_FILE) -> date | None:
    """Local calendar date of the last heartbeat, or None if unreadable."""
    try:
        with open(path) as f:
            ts = f.read().strip()
        return datetime.fromisoformat(ts).astimezone().date()
    except (OSError, ValueError):
        return None


_UNREADABLE = object()          # distinct from "read it, found nothing"


def _all_records(records=None):
    """The ledger, or None when it cannot be read at all. Shared by every check
    so `_UNREADABLE` and a genuine read failure behave identically."""
    if records is _UNREADABLE:
        return None
    if records is None:
        try:
            import yaml
            from ledger import Ledger
            with open("config.yaml") as f:
                cfg = yaml.safe_load(f)
            return Ledger(cfg["memory"]["ledger_path"]).all_records()
        except Exception:  # noqa: BLE001 — reported by the caller, not swallowed
            return None
    return records


def _event_on(day: date, event: str, records=None) -> bool | None:
    """Did `event` occur on `day`? True / False / None (ledger unreadable)."""
    records = _all_records(records)
    if records is None:
        return None
    for r in records:
        if r.get("event") != event:
            continue
        ts = r.get("ts") or r.get("timestamp") or ""
        try:
            if datetime.fromisoformat(ts).astimezone().date() == day:
                return True
        except ValueError:
            continue
    return False


def completed_on(day: date, records=None) -> bool | None:
    """Did a cycle reach `cycle_complete` on `day`?

    True / False / None, where None means the ledger could not be read at all.
    The three-way answer matters: a monitor that cannot see its input must say
    so rather than report all-clear, which is the failure this whole module is
    being corrected for.
    """
    return _event_on(day, "cycle_complete", records)


def news_on(day: date, records=None) -> bool | None:
    """Did the news brain produce a market context on `day`? Same three-way
    answer, same reason."""
    return _event_on(day, "market_context", records)


def check(today: date | None = None,
          heartbeat_path: str = HEARTBEAT_FILE,
          halt_path: str = HALT_FILE,
          records=None) -> list[str]:
    """Return the list of problems found (empty = all clear).

    Three different questions, deliberately kept apart:

      * did the PROCESS run?    -> the heartbeat file
      * did the CYCLE finish?   -> a `cycle_complete` record in the ledger
      * did it KNOW anything?   -> a `market_context` record in the ledger

    Until 2026-07-26 only the first was asked, and `write_heartbeat()` runs in
    a `finally:` — so a cycle that crashed six seconds in still stamped a
    fresh heartbeat and read as healthy. That is exactly what happened on
    2026-07-24: no decisions, no abort record, no alert. `docs/slo.md` had
    claimed completion was measured all along; it never was.

    The third question was added 2026-07-29 (W6-A3). On that day the 10:34 ET
    news refresh lost all eleven RSS feeds AND the Alpaca wire to DNS failure
    and reported it as a single INFO line, because news is fail-soft by design.
    Fail-soft is right — news must never gate trading — but "the bot traded
    today with no market awareness at all" is worth one alert. This check only
    ever ALERTS; nothing here can block a cycle.
    """
    today = today or date.today()
    problems = []
    if today.weekday() < 5:  # Mon-Fri: a cycle should have run
        hb = heartbeat_date(heartbeat_path)
        if hb is None:
            problems.append("no heartbeat file — the trading cycle has "
                            "never run or the file is unreadable")
        elif hb < today:
            problems.append(f"last heartbeat {hb} — today's trading cycle "
                            "did NOT run")
        else:
            # The process ran today. Did it get anywhere?
            done = completed_on(today, records)
            if done is None:
                problems.append("cannot read the ledger to confirm today's "
                                "cycle completed — treating as a failure")
            elif not done:
                problems.append("the cycle process ran today but never "
                                "reached cycle_complete — it died or aborted "
                                "part-way; check for a cycle_crashed record")
            else:
                # It ran and finished. Did it know anything while doing so?
                # Only asked when the cycle completed, so a broken cycle
                # reports one root cause instead of two symptoms.
                if news_on(today, records) is False:
                    problems.append(
                        "no market_context today — the bot traded with no "
                        "news awareness; check the news_sources ledger event "
                        "for which feeds failed (news is fail-soft, so this "
                        "never blocked the cycle)")
    if os.path.exists(halt_path):
        problems.append("HALT file present — trading is disabled until you "
                        "review and delete it")
    return problems


def catchup(now: datetime | None = None, records=None) -> str:
    """Late catch-up (2026-07-21): scheduled ~15:55 ET weekdays. If today's
    cycle hasn't run (machine was asleep at 15:45) and the market is still
    open, run it NOW instead of losing the trading day. Safe to double-fire:
    a same-day rerun is idempotent (client_order_ids + broker-fresh state).
    Returns what it did (for logs/tests)."""
    from zoneinfo import ZoneInfo
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return "weekend — no action"
    if not (9 <= now.hour < 16 and (now.hour, now.minute) >= (9, 30)):
        return "market closed — no action"
    # Completion, NOT the heartbeat. This used to test `hb >= now.date()`,
    # which meant a cycle that crashed at 15:45 stamped a fresh heartbeat on
    # its way out and thereby suppressed the very catch-up that exists to
    # rescue it. On 2026-07-24 that cost a whole trading day: the crash
    # silenced its own recovery. Asking "did a cycle finish today?" makes the
    # catch-up fire on a crash, which is exactly when it is wanted.
    if completed_on(now.date(), records):
        return "cycle already ran today — no action"
    log.warning("catch-up: no completed cycle today at %s ET — running it late",
                now.strftime("%H:%M"))
    notify("trading-agent: late catch-up",
           "3:45 cycle was missed; running it now before the close")
    import main as main_mod
    main_mod.run_cycle()
    return "ran late cycle"


def main():
    if "--catchup" in sys.argv:
        log.info("watchdog catch-up: %s", catchup())
        return
    problems = check()
    if not problems:
        log.info("watchdog: all clear")
        return
    for p in problems:
        log.critical("watchdog: %s", p)
        notify("trading-agent: needs attention", p)
    try:  # ledger event is best-effort — ops alerts must not depend on it
        import yaml
        from ledger import Ledger
        with open("config.yaml") as f:
            cfg = yaml.safe_load(f)
        Ledger(cfg["memory"]["ledger_path"]).log_event(
            "ops_alert", "; ".join(problems))
    except Exception as e:  # noqa: BLE001
        log.warning("ledger ops_alert write failed: %s", e)


if __name__ == "__main__":
    configure_logging()
    main()
