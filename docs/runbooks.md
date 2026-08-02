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

**Stop it now:** `./scripts/halt.sh "why you are stopping it"`

---

## STOP TRADING NOW (operator-initiated halt)

**When:** you want new positions to stop opening — a data source looks wrong,
fills look wrong, news you do not want the bot trading into, or you simply want
it to stand still while you look at something.

**What it does:** blocks NEW ENTRIES, immediately. `risk.check_halt` is
consulted once per cycle *and again per order*, so this stops the very next
order, not merely the next cycle.

**Two modes. Pick by asking: do you still trust the bot and the broker?**

| | `./scripts/halt.sh "why"` (default) | `./scripts/halt.sh --freeze "why"` |
|---|---|---|
| new entries | blocked | blocked |
| the cycle | **runs** | does not run at all |
| your open positions | still managed — closed when their strategy says to | untouched by the agent; **only broker-side bracket legs protect them** |
| use it when | "stop adding risk" — the market has gone mad, you want the book wound down on its own terms | the BOT or the BROKER is what you distrust: bad fills, a suspect feed, a bug |

The distinction matters most in the case that reads backwards: if you halt
*because the broker is filling badly*, the default mode would keep sending it
sell orders. That is what `--freeze` is for.

**Neither mode sells anything by itself.** `exits` only acts when a strategy
signals an exit; `freeze` does not act at all. Neither is a substitute for
deciding what to do with an open book.

**If you actually want the book flat, close the positions yourself** — through
the broker, deliberately, having decided that is what you want. There is no
scripted flatten, on purpose.

**History (2026-08-02):** HALT used to be `freeze` always, while `halt.py`
claimed positions kept "running to their normal stops and exits". They did not
— the cycle returned before any signal was evaluated. PR #72 corrected the
wording; the mode split made the original promise available for real.

```bash
./scripts/halt.sh "the market has gone mad"              # entries blocked, exits still run
./scripts/halt.sh --freeze "fills look wrong on every order"   # stop everything
./scripts/halt.sh --status                               # is it on, which mode, and why
./scripts/halt.sh --clear                                # resume trading
```

**Verify:** `./scripts/halt.sh --status` reports engaged and names the mode.
The next cycle logs `halted_exits_only` (exits mode) or `halted_cycle_skipped`
(freeze), and blocked entries record the `halt` rail. The halt is also ledgered
(`halt_engaged`) and alerted, so a second operator sees it.

**Notes:**

- Halting twice does **not** overwrite the original reason **or its mode**.
  Re-running with `--freeze` on a live `exits` halt changes nothing — clear it
  and engage again. Silently escalating an engaged kill switch while reporting
  "already engaged" is exactly the surprise to avoid.
- A HALT file written before modes existed (no `mode:` line) reads as
  **freeze**. Whoever pulled it believed it stopped everything, and a missing
  line is not permission to resume trading.
- It refuses to halt without a reason — an unexplained HALT cannot be safely
  cleared later, because nobody can tell it from a stray file.
- A failing ledger write or a dead alert webhook does **not** prevent the halt.
  Those are the same things that tend to be broken during a real incident.

---

## HALT engaged (daily-loss kill switch)

**Symptoms:** `HALT` file exists in the repo root; cycles log "halted" and do
nothing; watchdog notification fired.

**Diagnose:**
```bash
cat HALT                                    # reason + timestamp written at trip
# The trip and the skipped cycles are DIFFERENT events, and neither is named
# "halt". Grepping for "halt" alone returns nothing, which during an incident
# reads as "the kill switch never fired".
grep '"event": "kill_switch"' memory/ledger.jsonl | tail -3
grep '"event": "kill_switch_flatten_failed"' memory/ledger.jsonl | tail -3
grep '"event": "halted_cycle_skipped"' memory/ledger.jsonl | tail -3
```

The kill switch always engages a **freeze**, never `exits`, and that is
load-bearing rather than stylistic: it engages HALT *before* flattening
specifically so the next cycle cannot re-enter the same path and re-call
`flatten_all()`. Under `exits` it would do exactly that, every cycle, for as
long as the daily loss stood — and return early before any exit ran anyway.

**CHECK THIS FIRST: did the flatten actually succeed?**
```bash
grep '"event": "kill_switch_flatten_failed"' memory/ledger.jsonl | tail -3
```
If that line exists, **positions are still open and nothing is managing them**
except their broker-side bracket legs — the book is frozen with exposure on.
That is the worst state this system has, and it is **not** healed
automatically; automatic re-liquidation after a broker failure is a bigger
decision than the mode split was. Deal with it deliberately: close the
positions at the broker yourself, or `./scripts/halt.sh --clear` and re-engage
with `./scripts/halt.sh "post-kill-switch, working exits"` so the agent manages
them down while still opening nothing.

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
- No jobs listed (`launchctl list | grep trading-agent` empty) → the launchd
  jobs were never installed for this checkout. Install them with
  `sh scripts/install_launchd.sh --load` — it renders the plist templates with
  this checkout's real path (they ship a `{{AGENT_ROOT}}` placeholder so a moved
  or freshly-cloned repo can't run stale paths) and bootstraps them.

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

## The digest didn't go out

**Symptoms:** subscribers report no email, or you expected one and saw none.

**Diagnose — read the audit trail before changing anything:**
```bash
tail -3 publisher_data/digest_runs.jsonl   # every run records why it stopped
tail -5 publisher_data/outbox.jsonl        # per-recipient delivery records
```

`skipped_reason` names the cause outright. The four you will actually see:

| `skipped_reason` | meaning |
|---|---|
| `publisher.digest.enabled is false` | working as designed — the digest is **off by default** |
| `already ran today (…)` | it already went out; check `outbox.jsonl` before re-sending |
| `no active subscribers` | the list is empty, or you are pointed at the wrong `data_dir` |
| `… exceeds max_recipients_per_run` | the blast-radius cap refused rather than truncating |

If `dry_run: true` on every outbox line, nothing was ever sent: a real send
needs **all three** of `publisher.digest.enabled`, `email.dry_run: false`, and
`RESEND_API_KEY`. Deliberately three — see PRODUCT.md.

**Rehearse before arming anything:**
```bash
python scripts/send_digest.py --dry-run
```

**Verify:** a run line with `skipped_reason: null` and `failed: 0`.

---

## The digest went out twice

**Symptoms:** subscribers received two copies.

**Cause:** almost always a human re-running the CLI. `send_daily_digest()`
refuses a second run the same **ET** day, but `--force` bypasses that guard —
which is what it is for, and why it should not live in a cron line.

**Diagnose:** `grep '"date_et": "YYYY-MM-DD"' publisher_data/digest_runs.jsonl`
— more than one line with `queued + sent > 0` is a double send.

**Fix:** nothing to roll back; mail is gone. Remove `--force` from whatever
invoked it. If two hosts are running the publisher, stop one — the same-day
guard reads a local file and cannot see the other machine.

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

---

## Decay alert (strategy may have fallen to random-entry levels)

**Symptoms:** alert titled "Repete: strategy may have decayed";
`scripts/run_decay_check.sh` logged a `WORSE_THAN_RANDOM` verdict in
`logs/cron.log`. Trading is **still running** — this monitor is alert-only and
never halts (§47).

**Diagnose:**
```bash
.venv/bin/python src/decaycheck.py --json        # full verdict + percentile
.venv/bin/python src/decaycheck.py --strategy tsmom   # per-strategy breakdown
grep 'decaycheck' logs/cron.log | tail -10       # is this new, or a trend?
```

Read the percentile, not just the verdict. One reading below the 5th percentile
on ~20 trades is weak evidence; the same reading falling week over week is the
signal. The monitor is deliberately **lenient** (§47: the null carries no stops,
which flatters the strategy), so a fire is more meaningful than a pass.

**Fix:** This is a research trigger, not an incident. Nothing auto-changes.
Options, in the order the repo's own discipline prefers:
1. Confirm it isn't a data problem first — `python src/datacheck.py`, and check
   the trades in the window aren't dominated by one symbol or one week.
2. If the decay looks real, the response is a **new pre-registration**, not a
   parameter tweak. A strategy that stopped working is not fixed by re-tuning it
   on the data that showed it stopped — that is curve-fitting the failure.
3. To stop trading meanwhile, that is a human decision: set the strategy's
   `max_open_positions: 0` in `config.yaml`, or write a `HALT` file. The monitor
   will not do either for you.

**Verify:** `.venv/bin/python src/decaycheck.py` reports the expected verdict,
and `git diff config.yaml` shows exactly what you intended to change (probably
nothing).

**Note:** exit code 2 means the check **could not run** — a missing config, no
bars, a broker failure. That is *not* a pass. `INSUFFICIENT_DATA` (exit 0) is
also not a pass: it means there were fewer than 20 closed trades, so no claim
was made in either direction.
