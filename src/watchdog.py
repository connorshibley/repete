"""Dead-man watchdog — the bot must fail LOUDLY when it runs unattended.

Scheduled ~30 min after the daily cycle (see scripts/
com.trading-agent.watchdog.plist). Checks two things and alerts via a macOS
notification + log + ledger event when either fails:

  1. HEARTBEAT: memory/heartbeat (written on every run_cycle exit path) must
     be from today (local). Missing/old on a weekday means the cycle never
     ran — launchd broke, the venv broke, or the machine slept through it.
  2. HALT: if the kill-switch HALT file exists, keep reminding the owner
     every day until they deal with it.

Alerts degrade gracefully (a notification failure still logs); the watchdog
itself never raises.
"""
import logging
import os
import subprocess
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler("logs/agent.log", mode="a")],
)
log = logging.getLogger("watchdog")

HEARTBEAT_FILE = "memory/heartbeat"
HALT_FILE = "HALT"


def notify(title: str, message: str):
    """macOS banner via osascript; failure is logged, never raised."""
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            check=False, capture_output=True, timeout=10)
    except Exception as e:  # noqa: BLE001 — alerting must not crash the alerter
        log.warning("notification failed: %s", e)


def heartbeat_date(path: str = HEARTBEAT_FILE) -> date | None:
    """Local calendar date of the last heartbeat, or None if unreadable."""
    try:
        with open(path) as f:
            ts = f.read().strip()
        return datetime.fromisoformat(ts).astimezone().date()
    except (OSError, ValueError):
        return None


def check(today: date | None = None,
          heartbeat_path: str = HEARTBEAT_FILE,
          halt_path: str = HALT_FILE) -> list[str]:
    """Return the list of problems found (empty = all clear)."""
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
    if os.path.exists(halt_path):
        problems.append("HALT file present — trading is disabled until you "
                        "review and delete it")
    return problems


def main():
    problems = check()
    if not problems:
        log.info("watchdog: all clear")
        return
    for p in problems:
        log.critical("watchdog: %s", p)
        notify("Trading agent needs attention", p)
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
    main()
