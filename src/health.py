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

from strategies.base import ENTRY_ACTIONS  # noqa: E402

import statepaths  # noqa: E402

# Kept as names because twelve tests drive these functions through them; the
# literals now have exactly one definition, in src/statepaths.py. These are the
# DEFAULTS — status() resolves the live values from cfg, so a health check can
# be pointed at a fixture instead of at whatever sits next to the process.
HEARTBEAT_FILE = statepaths.DEFAULT_HEARTBEAT_PATH
HALT_FILE = statepaths.DEFAULT_HALT_PATH
# The heartbeat is written by the trading cycle, which fires at 15:45 ET on
# weekdays. 26h covers a normal overnight gap BETWEEN two consecutive trading
# days — Monday 15:45 to Tuesday 15:45 is 24h.
MAX_HEARTBEAT_AGE_HOURS = 26

# ...but the age alone cannot tell you a cycle was MISSED (2026-08-03).
#
# This comment used to claim "weekends are excluded from the staleness verdict".
# They were not. The guard was `now.weekday() < 5`, which only skips the check
# when TODAY is a weekend and does nothing about the weekend GAP. The heartbeat
# comes from the 15:45 cycle, so every Monday before 15:45 the newest one is
# Friday's — 66-70h old — and health.py reported
#
#     DEGRADED | heartbeat is 66.2h old — a weekday cycle was missed
#
# when nothing had been missed and the day's cycle was still hours away. It
# fired every Monday, and `main()` exits non-zero on degraded, so anything
# gating on this failed every Monday morning too.
#
# A stale heartbeat is only EVIDENCE OF A MISS once the cycle that writes it was
# actually due. So the assertion is gated on the scheduled time having passed
# today, which fixes Monday and market holidays for the same reason, rather than
# on an hour count that cannot know about either. Detection is unchanged after
# the cycle is due: still stale at 16:15 is still a genuine miss.
CYCLE_HOUR_ET, CYCLE_MINUTE_ET = 15, 45

# The off-host mirror runs inside the WEEKDAY backup job (scheduler.py: the
# "backup" entry, range(0, 5) at 17:00 ET). Its staleness is judged the same
# way the heartbeat's is — against whether a run was actually DUE — and not
# against a flat number of hours. Friday 17:00 to Monday 17:00 is 72h of
# entirely correct silence, so any fixed threshold either cries wolf every
# Sunday or is too loose to catch a single missed weekday. Divergence #22 is
# what an alarm that fires on a working system costs.
BACKUP_HOUR_ET, BACKUP_MINUTE_ET = 17, 0
# The job takes seconds; the grace is for a slow upload or a late scheduler,
# not for a missed run.
BACKUP_GRACE_HOURS = 2


def _et_date(ts: str) -> str:
    """The EASTERN calendar date of an ISO timestamp, or "" if unreadable.

    Why this exists (2026-08-03, found while §50 was running)
    --------------------------------------------------------
    `cycle_was_due` converts to America/New_York, because the cycle fires at
    15:45 ET. The completion check did not: it compared `now.strftime()` — a
    UTC date — against `record["ts"][:10]`, also UTC. Each half was internally
    consistent and the two halves disagreed with each other.

    Between 20:00 ET and midnight ET the UTC date has already rolled forward
    while ET is still the same trading day. So every weekday evening:

        cycle_was_due(now)        -> True   (ET: weekday, past 15:45)
        cycle_completed_today     -> False  (looking for Aug 4 records on Aug 3)
        => "cycle ran today but never completed"

    `python src/health.py` therefore reported DEGRADED and exited non-zero
    every weekday night, four hours a day, with nothing wrong. Observed live at
    20:51 ET with the day's cycle sitting completed in the ledger.

    This is the SAME CLASS of bug as the Monday false alarm fixed in PR #77 —
    a staleness question answered in one timezone and a scheduling question
    answered in another — which is why the fix is to make every date in this
    module Eastern rather than to special-case the evening.
    """
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def cycle_was_due(now: datetime) -> bool:
    """Has today's trading cycle already been scheduled to run?

    False on weekends, and on a weekday before 15:45 ET. Callers use this to
    decide whether a stale heartbeat means anything yet.
    """
    from zoneinfo import ZoneInfo
    et = now.astimezone(ZoneInfo("America/New_York"))
    if et.weekday() >= 5:
        return False
    return (et.hour, et.minute) >= (CYCLE_HOUR_ET, CYCLE_MINUTE_ET)


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


def last_backup_due(now: datetime,
                    grace_hours: float = BACKUP_GRACE_HOURS) -> datetime | None:
    """The most recent weekday 17:00 ET at which a mirror should already have
    happened, allowing `grace_hours` for the run itself. None if no such
    instant exists inside the last week.

    Deliberately parallel to `cycle_was_due`: the question is never "how old is
    this file" but "was a run due since it was written".
    """
    from datetime import timedelta

    from zoneinfo import ZoneInfo
    et_zone = ZoneInfo("America/New_York")
    cursor = now.astimezone(et_zone) - timedelta(hours=grace_hours)
    for _ in range(8):
        if cursor.weekday() < 5:
            due = cursor.replace(hour=BACKUP_HOUR_ET, minute=BACKUP_MINUTE_ET,
                                 second=0, microsecond=0)
            if due <= cursor:
                return due.astimezone(timezone.utc)
        cursor = (cursor - timedelta(days=1)).replace(hour=23, minute=59,
                                                      second=59, microsecond=0)
    return None


def mirror_receipt_ts(path: str, now: datetime | None = None) -> datetime | None:
    """When the off-host mirror last VERIFIED. None if it never has.

    None is not zero and not "old": `scripts/backup.sh` writes this file only
    after the remote returned a hash matching the bytes uploaded, so its
    absence means no verified mirror exists, which is a different and worse
    state than a stale one. Callers must not collapse them.
    """
    try:
        with open(path) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not rec.get("verified"):
        return None
    try:
        ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _open_buys_count(records: list[dict]) -> int:
    """Open executed ENTRIES, matching ledger.open_buys / ReadOnlyLedger — an
    outcome record closes its trade. Used by the read-only status path.

    ENTRY_ACTIONS, moved in lockstep with both of those. This docstring's
    claim to "match" them is the whole contract of this function, and leaving
    `== "buy"` here would have made health report a smaller open book than the
    bot is actually carrying the moment a short exists — a monitor that
    disagrees with the thing it monitors."""
    open_ids: set = set()
    for r in records:
        if (r.get("type") == "decision" and r.get("executed")
                and r.get("action") in ENTRY_ACTIONS):
            open_ids.add(r.get("trade_id"))
        elif r.get("type") == "outcome":
            open_ids.discard(r.get("trade_id"))
    return len(open_ids)


def _cycle_equity(rec: dict) -> float | None:
    """Equity out of a `cycle_complete` record, or None if it cannot be read.

    `detail` is a JSON string written by main.py — {"equity": ..., ...}. Read
    defensively: an unparseable record must yield "unknown", never a stale or
    invented number, because an unknown drawdown must not read as a healthy
    one.
    """
    try:
        return float(json.loads(rec.get("detail") or "{}")["equity"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def last_known_equity(records) -> float | None:
    """Newest equity from any `cycle_complete` record, or None.

    The only equity reading available WITHOUT calling the broker, which is what
    lets both `status()` and `watchdog.check()` report the drawdown rail
    off-network. Public and shared on purpose: two hand-rolled copies of "find
    the last cycle_complete and dig the equity out of its detail blob" would be
    free to disagree, and the pair would then disagree about whether the rail
    is engaged.
    """
    equity = None
    for r in records or ():
        if r.get("type") == "event" and r.get("event") == "cycle_complete":
            equity = _cycle_equity(r) or equity   # chronological; last wins
    return equity


def status(cfg: dict | None = None, now: datetime | None = None,
           read_only: bool = False) -> dict:
    """Everything an operator (or a status page) needs in one object.

    read_only=True (the publisher's /healthz path): never call
    store.configure() and never construct a writable store — reading agent
    state must not be able to flip the process backend or issue DDL
    (invariant #9). The agent's own CLI/watchdog use read_only=False."""
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
        # Resolved from cfg, which is why this reports on the data health was
        # POINTED AT rather than on the process's working directory. Falls back
        # to the cwd-relative default — including under the `cfg = {}` branch
        # above, so a broken config.yaml can never make a genuinely halted bot
        # report halted: false.
        "halted": os.path.exists(statepaths.halt_path(cfg)),
        "heartbeat_age_hours": None,
        # None means NO verified mirror has ever happened — distinct from an
        # old one, and reported as the worse of the two.
        "offhost_mirror_age_hours": None,
        "offhost_mirror_overdue": False,
        "offhost_mirror_required": False,
        "storage_backend": "jsonl",
        "last_cycle": None,
        # Default False, not None: if the ledger cannot be read we must not
        # conclude the cycle was fine. Unknown is treated as not-completed.
        "cycle_completed_today": False,
        "cycle_crashed_today": False,
        "open_positions": None,
        "degradations_today": 0,
        "slo_breach_today": False,
        # The drawdown circuit breaker, reported because it cannot report
        # itself (2026-08-03). §40 showed it is a ONE-WAY LATCH with no
        # recovery path — once engaged the book goes to cash, equity stops
        # moving, the peak never falls, and entries stay blocked for good. It
        # has never tripped in production, which is exactly why it has been
        # invisible. Populated from `risk.drawdown_state`, read-only.
        "drawdown": None,
        "problems": [],
    }

    age = heartbeat_age_hours(statepaths.heartbeat_path(cfg), now=now)
    out["heartbeat_age_hours"] = round(age, 2) if age is not None else None

    ops = cfg.get("ops", {}) or {}
    receipt_path = ops.get("offhost_mirror_receipt", "memory/offhost_mirror.json")
    mirror_ts = mirror_receipt_ts(receipt_path)
    if mirror_ts is not None:
        out["offhost_mirror_age_hours"] = round(
            (now - mirror_ts).total_seconds() / 3600, 2)

    # DEFAULT FALSE when the key is absent, and that is deliberate. A missing
    # receipt is the normal state on a laptop, in CI, and in any fresh
    # checkout; degrading there would make DEGRADED the resting state
    # everywhere except one host, which is how a health check stops being read.
    #
    # The shipped config.yaml sets this TRUE, and
    # tests/test_shipped_config.py::test_the_shipped_config_requires_an_offhost_mirror
    # asserts it — so "absent" cannot become a quiet way to switch the alarm
    # off in production. Same shape as the judge's `on_unavailable: block`,
    # where a config flip survived every test until the shipped value itself
    # was pinned.
    out["offhost_mirror_required"] = bool(ops.get("require_offhost_mirror", False))
    due = last_backup_due(now)
    # Overdue iff required, a run was due, and the last verified mirror
    # predates it. Never a flat hour count — see BACKUP_GRACE_HOURS.
    out["offhost_mirror_overdue"] = bool(
        out["offhost_mirror_required"]
        and due is not None
        and (mirror_ts is None or mirror_ts < due))

    try:
        import store as store_mod
        ledger_path = cfg.get("memory", {}).get("ledger_path",
                                                "memory/ledger.jsonl")
        if read_only:
            backend, _ = store_mod.backend_kind(cfg)
            out["storage_backend"] = backend
            records = store_mod.read_only_reader(cfg, ledger_path).read_all()
        else:
            store_mod.configure(cfg)
            out["storage_backend"] = store_mod.current_backend()
            from ledger import Ledger
            records = Ledger(ledger_path).all_records()
        # EASTERN, not UTC — see `_et_date`. `cycle_was_due` has always been in
        # ET because the cycle fires at 15:45 ET; this half was in UTC, and the
        # two disagreed for the four hours between 20:00 ET and midnight ET.
        today = _et_date(now.isoformat())
        last_equity = last_known_equity(records)
        for r in records:
            if r.get("type") != "event":
                continue
            if r.get("event") == "cycle_complete":
                out["last_cycle"] = r.get("ts")
                if _et_date(r.get("ts") or "") == today:
                    out["cycle_completed_today"] = True
            if _et_date(r.get("ts") or "") == today:
                if r.get("event") == "cycle_crashed":
                    out["cycle_crashed_today"] = True
                if r.get("event") == "degradation":
                    out["degradations_today"] += 1
                elif r.get("event") == "slo_breach":
                    out["slo_breach_today"] = True
        out["open_positions"] = _open_buys_count(records)
    except Exception as e:  # noqa: BLE001
        out["problems"].append(f"ledger unreadable: {e}")
        last_equity = None

    # --- the drawdown circuit breaker (2026-08-03) -------------------------
    # Its own try/except: this is reporting, and a failure to report the rail
    # must not take down the rest of the health check.
    try:
        import risk
        out["drawdown"] = risk.drawdown_state(
            last_equity, (cfg.get("risk") or {}).get("max_drawdown_pct", 0))
    except Exception as e:  # noqa: BLE001 — health must never crash
        out["problems"].append(f"drawdown state unreadable: {e}")

    if out["halted"]:
        out["problems"].append("HALT file present — trading disabled")
    if age is None:
        out["problems"].append("no heartbeat — the cycle has never run")
    elif age > MAX_HEARTBEAT_AGE_HOURS and cycle_was_due(now):
        out["problems"].append(
            f"heartbeat is {age:.1f}h old — a weekday cycle was missed")
    elif cycle_was_due(now) and not out["cycle_completed_today"]:
        # The heartbeat is fresh, so the process ran. `last_cycle` has been
        # collected here since this file was written but was only ever
        # reported, never asserted on — so a cycle that started and died read
        # as healthy (2026-07-24). This is the assertion that was missing.
        out["problems"].append(
            "cycle ran today but never completed"
            + (" — see the cycle_crashed record"
               if out["cycle_crashed_today"] else ""))
    if out["offhost_mirror_overdue"]:
        if out["offhost_mirror_age_hours"] is None:
            out["problems"].append(
                "no verified off-host mirror — every backup is on the same "
                "disk as the thing it protects")
        else:
            out["problems"].append(
                f"off-host mirror is {out['offhost_mirror_age_hours']:.0f}h old "
                f"— a weekday mirror was due and did not verify")
    if out["slo_breach_today"]:
        out["problems"].append("degradation SLO breached today")

    # §40: the latch has no recovery path, so this problem is permanent until
    # a human acts. Saying only "entries blocked" would read as a transient
    # state that clears itself — the whole point is that it does not.
    dd = out["drawdown"]
    if dd and dd.get("engaged"):
        detail = (f"{dd['drawdown_pct']:.2f}% from peak "
                  f"${dd['peak_equity']:,.0f} (limit {dd['limit_pct']:.1f}%)"
                  if dd.get("drawdown_pct") is not None else dd.get("note", ""))
        out["problems"].append(
            f"drawdown circuit breaker ENGAGED — {detail}; entries are "
            f"blocked and exits still run. " + _reset_hint())

    out["healthy"] = not out["problems"]
    return out


def _reset_hint() -> str:
    try:
        import risk
        return risk.HIGHWATER_RESET_HINT
    except Exception:  # noqa: BLE001 — a missing hint must not break the check
        return "the peak only ratchets UP, so this cannot clear itself."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    s = status()
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        dd = s.get("drawdown") or {}
        # Headroom, not just the drawdown: the number an operator wants is how
        # far the book is from the point of no return, and it is the one figure
        # here that nothing else surfaces.
        dd_txt = (f" | dd={dd['drawdown_pct']:.2f}%"
                  f" (headroom {dd['headroom_pp']:.2f}pp)"
                  if dd.get("drawdown_pct") is not None
                  and dd.get("headroom_pp") is not None else "")
        # Who would actually be told, printed rather than assumed.
        # alerting.channel() never probes, so asking is free and cannot page
        # anyone. It was written for exactly this question and was called from
        # NOWHERE until 2026-08-21 — so the only moment you could learn the
        # channel reached nobody was during an incident, through a
        # notification you were not going to receive. "log-only" here means a
        # failure goes to a file and no further.
        # "never" reads as the problem it is; "None h" reads as a formatting
        # bug and gets ignored.
        _m = s["offhost_mirror_age_hours"]
        if _m is not None:
            mirror_txt = f"{_m}h"
        elif s["offhost_mirror_required"]:
            mirror_txt = "never"
        else:
            mirror_txt = "n/a"
        if s["offhost_mirror_overdue"]:
            mirror_txt += " OVERDUE"

        try:
            import alerting
            alerts_txt = f" | alerts={alerting.channel()}"
        except Exception:  # noqa: BLE001 — monitoring never breaks the report
            alerts_txt = ""
        print(f"{'HEALTHY' if s['healthy'] else 'DEGRADED'} | mode={s['mode']}"
              f" | storage={s['storage_backend']}"
              f" | heartbeat={s['heartbeat_age_hours']}h"
              f" | mirror={mirror_txt}"
              f" | open={s['open_positions']}"
              f"{dd_txt}"
              f"{alerts_txt}"
              f" | degradations today={s['degradations_today']}")
        for p in s["problems"]:
            print(f"  - {p}")
    return 0 if s["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
