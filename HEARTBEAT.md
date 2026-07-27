# The heartbeat — how this bot proves it's alive

An autonomous bot has a failure mode worse than a losing trade: **silently not
running.** A launchd misconfiguration, a broken virtualenv, an expired key, a
Mac asleep at 3:45 PM — any of these means no cycle, no orders, no stop
management, and *no error anywhere you'd look*, because nothing ran to produce
one. The heartbeat + watchdog pair exists to convert "silently dead" into a
loud, same-day alert.

## The two halves

### 1. The heartbeat (written by the bot)

Every run of the trading cycle ends by writing a single UTC timestamp to:

```
memory/heartbeat
```

The write lives in a `finally:` block wrapping the whole cycle
(`run_cycle()` in `src/main.py`), so it fires on **every** exit path:

| Cycle outcome | Heartbeat written? |
|---|---|
| Normal cycle (trades or all-holds) | yes |
| HALT file present — trading refused | yes |
| Stale-data abort (freshness guard tripped) | yes |
| Kill switch fired, everything flattened | yes |
| Unhandled crash mid-cycle | yes (finally runs on the way out) |
| **launchd never started the process** | **no — this is the signal** |

That table is the design: the heartbeat deliberately means *"the process
ran"*, not *"trading went well"*. Trading problems are already loud (ledger
events, HALT, log lines). The heartbeat catches the one problem that can't
log itself — total absence.

### 2. The watchdog (checks the heartbeat)

`src/watchdog.py` runs on its own launchd job
(`scripts/com.trading-agent.watchdog.plist`, weekdays **4:15 PM** local —
30 minutes after the 3:45 PM cycle should have finished). It checks:

1. **Heartbeat freshness** — on a weekday, the timestamp in
   `memory/heartbeat` must be from *today* (local time). Missing, unreadable,
   or dated yesterday ⇒ today's cycle did not run.
2. **HALT** — if the kill-switch `HALT` file exists, it nags daily until a
   human reviews and deletes it. (HALT also blocks trading, so an engaged
   kill switch with a distracted owner is dead-bot-with-extra-steps.)

On any problem the watchdog fires, in order:

- a **macOS notification** (`osascript display notification`) — the thing
  you actually see;
- a **CRITICAL line** in `logs/agent.log`;
- an **`ops_alert` event** in the ledger (`memory/ledger.jsonl`), so the
  outage is part of the permanent audit trail.

Each alert path is best-effort and independently wrapped: a broken
notification still logs, a broken ledger still notifies, and the watchdog
itself never raises. Weekends are quiet by design (no cycle is scheduled,
so a stale heartbeat is expected) — except HALT, which alerts every day.

## Why 4:15 PM

The cycle fires at 3:45 PM ET and finishes in well under a minute. The
30-minute gap absorbs slow starts and clock drift while still alerting the
same afternoon — early enough that a missed cycle can be run by hand
(`.venv/bin/python src/main.py`) before the market moves far without its
stop management. Don't schedule the watchdog before the cycle: it would
read yesterday's heartbeat every day and cry wolf.

## File format

One ISO-8601 UTC timestamp, newline-terminated:

```
2026-07-16T19:46:02.113724+00:00
```

Nothing else. It's overwritten in place each cycle (not appended) — history
lives in the ledger, not here. The file is runtime state and is `.gitignore`d
along with the rest of `memory/`.

## If you get an alert

1. `launchctl list | grep trading-agent` — all **eight** jobs should be listed
   with status `0`: `cycle`, `catchup`, `watchdog`, `newsbrain`, `dailypost`,
   `learn`, `backup`, `restoredrill`. Anything missing means
   `sh scripts/install_launchd.sh --load` has not been run since it was added.
2. `tail -50 logs/agent.log` and `logs/launchd.err.log` — did the cycle
   start and die, or never start?
3. If the market is still open and the miss was mechanical (asleep laptop),
   run the cycle manually: `.venv/bin/python src/main.py`.
4. If `HALT` is the alert: read the HALT file, understand *why* the kill
   switch fired, and only then delete it. The watchdog will keep reminding
   you until you do — that's the point.

## Testing it

Offline unit tests live in `tests/test_watchdog.py` (heartbeat fresh / stale /
missing / corrupt, weekend silence, HALT detection, and the
heartbeat-on-halted-cycle guarantee). To see the real alert end-to-end:
delete `memory/heartbeat` on a weekday and run
`.venv/bin/python src/watchdog.py` — you should get the macOS banner, the log
line, and an `ops_alert` ledger event.
