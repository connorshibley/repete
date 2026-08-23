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

That path is `memory.heartbeat_path` in `config.yaml`, resolved for the writer
and for both readers by `src/statepaths.py` — one definition, where `main.py`,
`health.py` and `watchdog.py` each used to hold their own copy of the literal
with nothing making them agree. The default above is what ships and what every
command on this page assumes; `src/preflight.py` refuses to start a cycle if it
is changed, because the key exists only so a QA sweep can point a health check
at a fixture (F-14, `docs/qa_findings.md`). `memory.halt_path` is the same
arrangement for the HALT file — but only for the MONITORS: the trading rail in
`risk.py` always reads where `scripts/halt.py` writes.

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

### 2. The watchdog (checks the heartbeat *and* that the cycle finished)

`src/watchdog.py` runs on its own launchd job
(`scripts/com.repete.watchdog.plist`, weekdays **4:15 PM** local —
30 minutes after the 3:45 PM cycle should have finished). It checks **four**
things:

1. **Heartbeat freshness — did the process RUN?** On a weekday, the timestamp
   in `memory/heartbeat` must be from *today* (local time). Missing,
   unreadable, or dated yesterday ⇒ today's cycle did not start.
2. **`cycle_complete` — did the cycle FINISH?** A `cycle_complete` event dated
   today must exist in `memory/ledger.jsonl`. A fresh heartbeat with no
   completion means the process ran and got nowhere.
3. **`market_context` — did it KNOW anything?** A `market_context` event dated
   today must exist. Checked *only when check 2 passed*, so a broken cycle
   reports one root cause instead of two symptoms. **Alert only** — news is
   fail-soft by design and can never gate a trade.
4. **HALT** — if the kill-switch `HALT` file exists, it nags daily until a
   human reviews and deletes it. (HALT also blocks trading, so an engaged
   kill switch with a distracted owner is dead-bot-with-extra-steps.)

> **Check 2 was added 2026-07-26, and this document omitted it until
> 2026-07-29.** It exists because check 1 alone is blind to the failure that
> actually happened: `write_heartbeat()` runs in a `finally:`, so the crashed
> 15:45 cycle on Friday 2026-07-24 stamped a **fresh** heartbeat on its way out
> and read as perfectly healthy. `catchup` used the same test, so the crash
> silenced its own recovery, and the EOD post read like a quiet market. One
> trading day lost, unnoticed for two.
>
> Listing two checks here was therefore not a cosmetic omission — it described
> the version of the watchdog that missed the incident this file exists to
> explain. `tests/test_cycle_completion.py` is the assertion that keeps it
> honest.

> **Check 3 was added 2026-07-29 (W6-A3)** for the same class of reason. That
> morning the 10:34 ET news refresh lost **all eleven RSS feeds and the Alpaca
> wire** to DNS failure and recorded it as a single INFO line, `no headlines
> this morning`. The cycle ran, completed, traded, and the watchdog said
> all-clear — correctly, on the three questions it was asking. Nobody was asking
> whether the bot knew anything.
>
> The pattern across all three additions is the same: **the monitor could only
> fail on the questions it already asked, and every real incident arrived as a
> question nobody had written down.** The fix each time was a new question plus
> a test that fails if the answer stops being checked.

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

The cycle fires at 3:45 PM ET. This said it "finishes in well under a minute"
until 2026-08-06, when the first measurements arrived and showed otherwise:
across seven launchd cycles, **min 1.63 min, median ~2.6, max 7.72** — the
slowest being 2026-08-05, which spent a 54 s stall and two ~90-100 s broker
socket hangs and finished at 3:52 PM. Every one landed before the bell, but
"well under a minute" was never true of any of them.

`cycle_timing` now records duration and margin-to-close on every cycle, and
`ops.min_close_margin_min` alerts once a day when the margin gets thin. The
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

1. `launchctl list | grep repete` — all **12** jobs should be listed
   with status `0`: `cycle`, `catchup`, `watchdog`, `newsbrain`, `dailypost`,
   `learn`, `backup`, `restoredrill`, `secretcheck`, `decaycheck`,
   `flattenretry`, `logrotate`. Anything missing means
   `sh scripts/install_launchd.sh --load` has not been run since it was added.
   (This list said **eight** until 2026-08-06 and had been wrong for some time
   — `tests/test_runbook_accuracy.py` now derives the count from the shipped
   plists, so it cannot drift silently again.)
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
