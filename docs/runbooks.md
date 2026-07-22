# Runbooks

One page per failure mode. Format: symptoms → diagnose → fix → verify.
General rule: the agent fails SAFE on config problems (no trading) and fails
SOFT on data/LLM/X problems (degrades, keeps going) — know which kind you're
looking at before touching anything.

Quick status from anywhere:

```bash
python src/health.py            # one status object; nonzero exit = unhealthy
python src/review.py            # full skeptical report
tail -5 logs/agent.jsonl        # structured log tail (secrets redacted)
```

---

## HALT engaged (daily-loss kill switch)

**Symptoms:** `HALT` file exists in the repo root; cycles log "halted" and do
nothing; watchdog notification fired.

**Diagnose:**
```bash
cat HALT                                    # reason + timestamp written at trip
grep '"event": "halt"' memory/ledger.jsonl | tail -3
```

**Fix:** This is the one guard that *should* require a human. Read the ledger
around the trip, understand which positions caused the loss, decide whether
the day was an anomaly (gap, halt, fat print) or the strategy misbehaving.
Only then: `rm HALT`.

**Verify:** next cycle runs (`python src/main.py` manually or wait for the
schedule) and `python src/health.py` shows `halted: false`.

---

## slo_breach (too many fail-open degradations in one day)

**Symptoms:** macOS alert "degradation SLO breached"; `slo_breach` event in
the ledger.

**Diagnose:**
```bash
grep '"event": "degradation"' memory/ledger.jsonl | tail -10
```
Each degradation names its source (`correlation_cap: no bars`,
`drift_guard: no quote`, `market_context: stale`...). Three in one day means
some upstream (usually the data vendor) is flaky — the guards are skipping
fail-open, which silently widens risk.

**Fix:** Identify the common source. Vendor outage → usually resolves itself;
consider not trading manually that day. Key expired → rotate
(docs/secrets_rotation.md). Code bug → fix, run tests.

**Verify:** next cycle produces zero degradation events.

---

## Stale bars (data-freshness guard aborting cycles)

**Symptoms:** cycle logs "SPY bars stale — aborting"; no decisions ledgered.

**Diagnose:**
```bash
python -c "
import yaml, sys; sys.path.insert(0,'src')
from dotenv import load_dotenv; load_dotenv()
from broker import Broker
cfg=yaml.safe_load(open('config.yaml')); b=Broker(cfg)
print(b.bars('SPY','1Day',3)[-1])"
```
Compare the last bar's date to today. Market holiday → not a bug, the guard
is doing its job. Weekday with markets open → Alpaca data problem.

**Fix:** Nothing to fix locally on a vendor outage — the guard exists so we
never repeat 2026-07-16 (six fills priced off stale bars). If Alpaca status
page confirms an incident, wait it out; the 15:55 catch-up run gets one more
chance the same day.

**Verify:** the catch-up cycle at 15:55 ET trades normally, or the next day's
cycle does.

---

## Vendor divergence (datacheck blocking entries)

**Symptoms:** ledger `degradation` event naming `datacheck`; "entries blocked"
in the cycle log; exits still processed.

**Diagnose:** the event detail carries both SPY closes (Alpaca vs yfinance)
and the divergence in bps. Confirm with any public quote source which vendor
is wrong.

**Fix:** none needed for a day — this fires only when both vendors report and
DISAGREE, and it already fails closed for entries only. Persistent (>2 days)
divergence → check whether one vendor changed its adjustment policy
(splits/dividends); update `datacheck` config cap if the world legitimately
changed.

**Verify:** next cycle has no datacheck event and entries flow.

---

## Missed cycle (nothing ran at 15:45 ET)

**Symptoms:** watchdog alert at 16:15; no `cycle_complete` event today;
heartbeat stale in `python src/health.py`.

**Diagnose:**
```bash
ls -la memory/heartbeat && cat memory/heartbeat
tail -50 logs/agent.log                  # or: docker compose logs --tail 50 agent
launchctl list | grep trading-agent      # laptop path
docker compose ps                        # container path
```

**Fix by cause:**
- Laptop asleep → the 15:55 catch-up already retried while the market was
  open; if that also missed, the day is lost (by design — no after-hours
  trading). Long-term fix: run the container on an always-on host.
- Container down → `docker compose up -d`, then check `docker compose ps`
  health column.
- Crash mid-cycle → the deterministic client_order_id makes a rerun safe:
  `python src/main.py` (or `docker compose run --rm agent python src/main.py`).

**Verify:** `python src/health.py` healthy; today's `cycle_complete` in the
ledger.

---

## Publisher down / misbehaving

**Symptoms:** site 5xx or unreachable; `/healthz` failing.

**Diagnose:**
```bash
curl -s localhost:8080/healthz | python -m json.tool
docker compose --profile publisher logs --tail 50 publisher
```

**Fix:** restart is always safe — the publisher holds no agent state and
cannot affect trading (invariant #9): `docker compose --profile publisher
restart publisher`. Missing `PUBLISHER_SESSION_SECRET` → set it in `.env`.
429s everywhere → someone is hammering an endpoint; that is the rate limiter
working, not an outage.

**Verify:** `/healthz` 200; `/feed` returns JSON.

**Remember:** trading does not care whether the publisher is up. Never delay
a trading fix to babysit the publisher.

---

## Backup / restore

**Take a backup now:** `scripts/backup.sh`
**Prove it restores:** `python scripts/restore_drill.py`

**Actual restore after data loss:**
```bash
tar -xzf backups/agent-backup-<newest>.tar.gz -C /tmp/restore
# inspect /tmp/restore/memory before touching anything live
cp -a /tmp/restore/memory/. memory/
python scripts/restore_drill.py     # then run the drill against live
python src/health.py
```
The streams are append-only JSONL — a restore is a file copy, no database
surgery. Anything traded between backup and restore is reconciled at the
next cycle start from the BROKER (source of truth for positions, invariant
#4); the ledger gap is honest history, note it in an `event` record.

---

## Container restart / redeploy

```bash
docker compose build && docker compose up -d       # agent
docker compose --profile publisher up -d           # + publisher
docker compose run --rm agent python src/health.py # verify
```
State lives in the `./memory` volume mount, so rebuilds are always safe.
Never bake state into the image.
