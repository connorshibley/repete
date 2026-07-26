# SLOs

These formalize targets the code already measures — nothing here is
aspirational monitoring to be built later. Each SLO names its measurement
and its alert path.

> **2026-07-26.** Row 1 used to claim cycle completion was measured by
> `cycle_complete` events. It was not: `watchdog.check()` compared only the
> heartbeat file, and `write_heartbeat()` runs in a `finally:` — so a cycle
> that crashed still stamped a fresh heartbeat and read as healthy. On
> 2026-07-24 that cost a full trading day in silence, and the same date-only
> test in `catchup()` suppressed the recovery run. The claim is now true:
> `watchdog.completed_on()` asserts a `cycle_complete` dated today, and
> `tests/test_cycle_completion.py` replays the incident.
>
> The lesson generalises past this row. **A documented check is not a check.**

| SLO | Target | Measured by | Alert path |
|---|---|---|---|
| Cycle completion | every market day gets a cycle (15:45, or 15:55 catch-up) that reaches `cycle_complete` | `watchdog.completed_on()` — a `cycle_complete` dated today, not merely a fresh heartbeat | catch-up re-runs it 15:55; watchdog 16:15 ET → `alerting.send()` (webhook if `ALERT_WEBHOOK_URL`, else macOS banner) |
| Cycle crash visibility | a cycle that dies leaves a record saying so | `cycle_crashed` ledger event with the traceback tail | surfaced by the completion SLO above; `health.status()` names it |
| Heartbeat freshness | < 26h old on weekdays | src/health.py (`MAX_HEARTBEAT_AGE_HOURS`) | health.py nonzero exit; Docker HEALTHCHECK → container unhealthy |
| Degradations (fail-open guard skips) | < `ops.max_degradations_per_day` (3) per day | ledgered `degradation` events, counted in main.py | `slo_breach` event + macOS alert |
| Data freshness | no cycle trades on stale bars, ever | risk.bars_fresh gate (aborts/drops) | cycle log + ledger record |
| Vendor agreement | entries only when both price vendors agree | datacheck.py per cycle | degradation event when they disagree |
| Backup restorability | weekly drill passes | scripts/restore_drill.py (Sat 10:00 in scheduler) | nonzero exit → scheduler error log |
| Publisher availability | best-effort only — explicitly NOT an SLO on trading's level | /healthz | none beyond logs (invariant #9: trading never waits on it) |

## Error budget thinking, sized to one operator

A missed cycle is one lost trading day out of ~21/month — annoying, not
fatal, and the catch-up run already halves the odds. The budget that matters
is **integrity, which has no budget**: zero tolerance for trading on stale
data, unsigned config changes to the interlock, or a corrupted ledger tail —
those are Sev 1 (docs/incident_response.md) regardless of frequency.

## Review cadence

`python src/review.py` weekly (already habit) surfaces degradation counts;
the monthly scorecard publishes performance vs S&P. If degradations trend up
across weeks, the fix is a vendor/infra decision, not a bigger threshold —
the threshold stays where it is precisely so drift is visible.
