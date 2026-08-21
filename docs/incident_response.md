# Incident response (solo-operator edition)

One person operates this system. The value of a written process at N=1 is
that during an incident you follow steps instead of improvising at the worst
possible moment.

## Owner

**Connor Shibley.** Also named in `config.yaml` as `ops.incident_owner`, and
the two must agree — `tests/test_incident_owner.py` fails if they drift.

Added 2026-08-21. Until then this file had a severity table, a six-step
process, and timeline and postmortem templates — and no name, no contact and
no escalation target anywhere in it. Every runbook said "alert" without saying
who receives one, and one refers to "a second operator" who was nowhere
defined.

**How an alert reaches him:** `ALERT_WEBHOOK_URL` if set, otherwise a macOS
banner on a Mac, otherwise nothing but a CRITICAL log line. Ask which one is
in force rather than assuming — `python src/health.py` prints it as
`alerts=webhook|desktop|log-only`, and preflight refuses to start when there
is neither a channel nor a named owner. `log-only` on the always-on Linux host
means an incident is written to a file and nobody is told.

## Severity levels

| Sev | Definition | Examples | Response |
|---|---|---|---|
| 1 | Money or record integrity at risk | interlock misconfigured toward live; ledger corruption; secret leaked | Drop everything. Stop trading first (create `HALT`), investigate second |
| 2 | Trading impaired | missed cycles, HALT tripped, persistent data outage, slo_breach | Same day, before next market open |
| 3 | Product impaired, trading fine | publisher down, dashboard stale, X posts failing | Within a day or two; never urgent by definition (invariant #9) |
| 4 | Cosmetic / informational | typo on a page, noisy log line | Whenever |

The classification question is always: **can this affect a trading decision
or the audit trail?** If yes → Sev 1–2. If no → Sev 3–4, calm down.

## The steps

1. **Stabilize.** For Sev 1: `touch HALT` (stops new trading, exits/brackets
   still protect open positions). For Sev 2–3: nothing to stabilize —
   the system already failed safe or soft.
2. **Snapshot.** `scripts/backup.sh` BEFORE touching anything, so the
   broken state is preserved for diagnosis.
3. **Diagnose** with the matching runbook (docs/runbooks.md).
4. **Fix** the cause, not the symptom.
5. **Verify** per the runbook's verify step + `python src/health.py`.
6. **Record.** Ledger `event` record for anything that touched trading;
   postmortem below for Sev 1–2.

## Timeline template

```
INCIDENT <date> — <one-line title>
Severity: 
Detected: <how — watchdog alert / manual / CI>
Timeline (ET):
  hh:mm  first symptom
  hh:mm  detected
  hh:mm  stabilized
  hh:mm  fixed / verified
Impact: <cycles missed, trades affected, records affected — numbers>
```

## Postmortem template (Sev 1–2, within 48h)

```
ROOT CAUSE: <the actual mechanism, not "vendor was down">
WHY NOT CAUGHT EARLIER: <which guard/test/alert should have, and didn't>
FIX SHIPPED: <commit>
GUARD ADDED: <the new test/rail/alert that makes recurrence loud>
```

The house rule from 2026-07-16 (stale-bars incident): every Sev 1–2
postmortem ships a guard, not just a fix. The freshness guard, the drift
guard, and the vendor cross-check all exist because an incident demanded a
permanent answer.
