"""Machine-readable health status (2026-07-22).

One honest snapshot of whether the agent is actually working, for the
watchdog, a container healthcheck, a status page, and later the publisher.
Read-only: it inspects state, never changes it.

    python src/health.py            # human summary, exit 0 ok / 1 degraded
    python src/health.py --json     # the raw status object
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEARTBEAT_FILE = "memory/heartbeat"
HALT_FILE = "HALT"
# A weekday cycle runs at 15:45 ET; ~26h covers a normal overnight gap, and
# weekends are excluded from the staleness verdict below.
MAX_HEARTBEAT_AGE_HOURS = 26


def heartbeat_age_hours(path: str = HEARTBEAT_FILE,
                        now: datetime | None = None) -> float | None:
    try:
        with open(path) as f:
            ts = datetime.fromisoformat(f.read().strip())
    except (OSError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - ts).total_seconds() / 3600


def status(cfg: dict | None = None, now: datetime | None = None) -> dict:
    """Everything an operator (or a status page) needs in one object."""
    now = now or datetime.now(timezone.utc)
    if cfg is None:
        try:
            import yaml
            with open("config.yaml") as f:
                cfg = yaml.safe_load(f)
        except Exception:  # noqa: BLE001 — health must never crash
            cfg = {}

    out: dict = {
        "checked_at": now.isoformat(),
        "mode": cfg.get("mode", "paper"),
        "halted": os.path.exists(HALT_FILE),
        "heartbeat_age_hours": None,
        "storage_backend": "jsonl",
        "last_cycle": None,
        "open_positions": None,
        "degradations_today": 0,
        "slo_breach_today": False,
        "problems": [],
    }

    age = heartbeat_age_hours(now=now)
    out["heartbeat_age_hours"] = round(age, 2) if age is not None else None

    try:
        import store as store_mod
        store_mod.configure(cfg)
        out["storage_backend"] = store_mod.current_backend()
        from ledger import Ledger
        led = Ledger(cfg.get("memory", {}).get("ledger_path",
                                               "memory/ledger.jsonl"))
        records = led.all_records()
        today = now.strftime("%Y-%m-%d")
        for r in records:
            if r.get("type") != "event":
                continue
            if r.get("event") == "cycle_complete":
                out["last_cycle"] = r.get("ts")
            if (r.get("ts") or "")[:10] == today:
                if r.get("event") == "degradation":
                    out["degradations_today"] += 1
                elif r.get("event") == "slo_breach":
                    out["slo_breach_today"] = True
        out["open_positions"] = len(led.open_buys())
    except Exception as e:  # noqa: BLE001
        out["problems"].append(f"ledger unreadable: {e}")

    if out["halted"]:
        out["problems"].append("HALT file present — trading disabled")
    if age is None:
        out["problems"].append("no heartbeat — the cycle has never run")
    elif age > MAX_HEARTBEAT_AGE_HOURS and now.weekday() < 5:
        out["problems"].append(
            f"heartbeat is {age:.1f}h old — a weekday cycle was missed")
    if out["slo_breach_today"]:
        out["problems"].append("degradation SLO breached today")

    out["healthy"] = not out["problems"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    s = status()
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print(f"{'HEALTHY' if s['healthy'] else 'DEGRADED'} | mode={s['mode']}"
              f" | storage={s['storage_backend']}"
              f" | heartbeat={s['heartbeat_age_hours']}h"
              f" | open={s['open_positions']}"
              f" | degradations today={s['degradations_today']}")
        for p in s["problems"]:
            print(f"  - {p}")
    return 0 if s["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
