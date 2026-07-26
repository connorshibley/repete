# PRODUCT.md — turning Repete into a subscription publication

Status: **Phases A–D built (2026-07-22).** Nothing is for sale. No money may
be collected until the gates in §Revenue below are met and an attorney has
reviewed the model. The ordered path from here to a live product is
`docs/go_live_checklist.md`.

---

## The model

**Paid publication (newsletter / signals).** Subscribers read what Repete
does — decisions, the judge's bull/bear reasoning, journal entries, the
monthly scorecard. They act on their own accounts, themselves. We never
touch a subscriber's money, broker, or account.

### Why this shape matters architecturally

The trading agent stays **single-tenant**: one bot, many readers.

- No per-subscriber broker keys, no multi-tenant trading loop, no per-tenant
  risk configs — the largest and riskiest refactor is simply not needed.
- Multi-tenancy exists **only** in the subscriber/billing layer.
- LLM cost is flat as subscribers grow (one judge, one news brain, one
  learning pass). Gross margin improves with every subscriber.

### Hard architectural invariant

> **The publisher is READ-ONLY against the ledger.** Nothing in the
> subscriber, billing, email, or web layer may write to the ledger or
> influence a trading decision. A trading cycle must behave identically
> whether the publisher exists or not.

This is not a style preference — it is what keeps the track record
trustworthy. The moment publishing could affect trading, the published
record stops being evidence of anything.

---

## Legal reality (read before writing a payment page)

Selling trading signals for a subscription fee sits near the **publisher's
exemption** to the Investment Advisers Act (*Lowe v. SEC*, 1985). That
exemption is narrow and fact-dependent. It generally requires content that is:

- **impersonal** — never tailored to an individual subscriber's situation;
- **regular** — published on a schedule, not issued in reaction to a
  subscriber's circumstances;
- **bona fide** — genuine publication, not a front for advisory services.

Practical rules this repo follows:

1. Never answer "what should *I* buy?" — that is personalized advice.
2. Every surface carries the paper-trading and not-advice disclaimer.
3. Performance claims always ship with sample size and methodology.
4. No guarantees, no projected returns, no testimonials.

**A securities attorney must review the model, the marketing copy, and the
terms before the first dollar is collected.** Drafted legal pages in this
repo are scaffolding for that review, never a substitute for it.

---

## Revenue gates (enforced in code, not promised in a doc)

Same discipline as the trading gates: pre-registered, checked automatically.

| Gate | Threshold | Why |
|---|---|---|
| Closed trades | >= 30 | Below this, no performance claim is meaningful |
| Live history | >= 3 months | One month is noise; monthly scorecard needs samples |
| Attorney sign-off | recorded flag | Model + copy + terms reviewed |
| Legal pages | published | ToS, Privacy, Risk Disclosure live |

As of the last update: **1 closed trade, 6 days.** Charging today would be
both an advertising-law exposure and commercially hopeless — nobody buys a
one-trade record. Build now, charge later.

---

## Phases

### Phase A — Foundation (nothing is sellable without it) ✅ built
- **Event-store abstraction** (`src/store.py`): `JsonlStore` (default,
  unchanged) and `SqliteStore` behind one two-method interface. Every domain
  class (`Ledger`, `LessonStore`, `JudgmentStore`, `PostExitStore`) is
  backend-agnostic. `scripts/migrate_jsonl_to_sqlite.py` migrates and then
  **verifies record-for-record**; JSONL files are never modified.
- **Containerization**: `Dockerfile` (non-root, healthcheck) +
  `docker-compose.yml`; state lives in volumes, secrets in env vars.
- **`scripts/scheduler.py`**: the launchd replacement. Same ET job times,
  in a container that never sleeps. A closed laptop lid stopped being a
  missed trading day.
- **`src/health.py`**: one status object (heartbeat age, HALT, degradations
  today, SLO breach, storage backend, open positions) for the watchdog,
  container healthcheck, and future status page.

### Phase B — Publisher platform ✅ built (2026-07-22)
FastAPI service in `publisher/`, importing the agent's stores through a
`ReadOnlyLedger` that has no write methods. Magic-link auth (no passwords,
token hashes only), Stripe checkout + webhooks + entitlements (stub mode
without keys), tiered content (free = 1-day delay, judge reasoning
withheld; paid = same-day + full reasoning/debate/confidence + journal),
DRAFT legal pages, and the revenue gate enforced at the checkout boundary.

**Email digests — built 2026-07-25, deliberately not scheduled.** This line has
been wrong twice: first "dry-run email digests" (overstated — nothing joined
the pieces), then "HALF-BUILT" (accurate at the time). What exists now:

- `publisher/broadcast.py::send_daily_digest()` joins `content.feed()` to
  `SubscriberDB.active_emails()`. One feed is built **per tier**, not per
  recipient — `content.feed()` walks the whole record stream several times, so
  per-recipient builds were tens of millions of traversals for identical output.
- `scripts/send_digest.py` is the entry point.
- **Three independent switches** must all be set before mail leaves:
  `publisher.digest.enabled` **and** `publisher.email.dry_run: false` **and**
  `RESEND_API_KEY`. The first exists separately because the other two also gate
  the magic-link sign-in email, which is transactional — one recipient, sent
  seconds after they asked. Flipping `dry_run` so someone can sign in must not
  also arm an unattended mailer against the whole list.
- The opt-out link is `GET /unsubscribe` — a confirmation page that **mutates
  nothing** — plus `POST /unsubscribe/confirm`, with a single-use 90-day token.
  This shape is not cosmetic: Gmail's link proxy, Outlook Safe Links and
  Proofpoint all prefetch links in email, so a mutating GET would silently
  unsubscribe people via their employer's security scanner.

**It is not scheduled in production, and that is on purpose.** The agent
container cannot run it — its Dockerfile never copies `publisher/` and it has
no `publisher_data` mount — so automating it means a new compose service for a
job that today mails nobody. The shape when that changes: a `digest` service
under the `publisher` profile running `scheduler.py --set publisher`, with
`PUBLISHER_JOBS = [("daily-digest", range(0,5), 17, 30, [PY,
"scripts/send_digest.py"])]`. Use `compose exec`, not `run --rm`, or two
containers write one sqlite file.

Known gaps, named rather than glossed: no plain-text alternate part, and no
`List-Unsubscribe` / RFC 8058 one-click header (that needs a verified sending
domain, which does not exist yet).

`tests/test_digest_broadcast.py` pins all of it — including that a GET never
unsubscribes and that an unsubscribed address is never mailed.

Run it:
```bash
PUBLISHER_SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))") \
  uvicorn publisher.app:app --port 8080          # local
docker compose --profile publisher up -d          # container (memory/ mounted :ro)
```
Env vars: PUBLISHER_SESSION_SECRET (required), RESEND_API_KEY (real email),
STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET (real billing). Absent keys =
dry-run/stub — behavior flips via environment, never code.

### Phase C — Trust & compliance artifacts ✅ built (2026-07-22)
- **One canonical disclaimer** (`src/disclaimer.py`): dashboard, journal,
  and blog footers render it; `publisher/gates.py` re-exports it — the
  agent's pages and the publisher can never drift apart.
- **Audit pack exporter** (`src/evidence.py`): `python src/evidence.py`
  writes a dated bundle — `summary.md` (gates + counts + model version),
  `performance.json` (report card, monthly scorecard, model-version
  segments), `lineage.json` (per-trade chain: entry decision → judge
  verdict → exit outcome), `invariants.json` (machine-checked spot checks:
  interlock, stream integrity, outcome embargo, every-entry-judged,
  disclaimer presence). Offline and read-only; exportable with zero keys.
- Legal-page scaffolding was already shipped in Phase B
  (`publisher/legal.py`, headed DRAFT — REQUIRES ATTORNEY REVIEW).

### Phase D — Enterprise hardening ✅ built (2026-07-22)
- **CI** (`.github/workflows/ci.yml`): pytest + pip-audit CVE scan on every
  push/PR; the suite is offline so CI holds zero secrets.
- **Backups with a restore drill**: `scripts/backup.sh` (nightly 17:00 ET
  via the scheduler, keeps 14, never archives .env) +
  `scripts/restore_drill.py` (Sat 10:00 — extracts the newest archive and
  proves it parses record-for-record; FAIL exits nonzero).
- **Rate limiting** (`publisher/ratelimit.py`): per-IP token buckets,
  strictest on the email-sending endpoint; 429 from middleware.
- **Structured logging with secret redaction** (`src/log.py`):
  JSON-lines mirror at logs/agent.jsonl; secret-named env values scrubbed.
- **Ops docs** (`docs/`): runbooks, incident response, secrets rotation,
  SLOs, SOC 2 readiness map (explicitly not a compliance claim), and the
  real-world go-live checklist.

### Phase E — Product surface
Authenticated subscriber dashboard (reusing `dashboard.py` renderers),
marketing page, docs, public status page.

---

## Operating the container

```bash
docker compose up -d                                  # scheduler, forever
docker compose run --rm agent python src/main.py      # one cycle
docker compose run --rm agent python src/health.py    # status
python scripts/migrate_jsonl_to_sqlite.py --verify    # backend parity check
```

Switching backends: migrate, verify, then set `storage.backend: sqlite` in
`config.yaml`. JSONL stays the default and the fallback — a config typo can
never silently move the audit trail (there is a test for that).

Persistence and publishing in the container:
- `docker-compose.yml` mounts `./memory`, `./logs`, **and `./backups`** as
  volumes — backups must survive a rebuild/redeploy, not live in the container's
  ephemeral layer.
- The scheduler chains `scripts/publish_dashboard.sh` after the cycle and the
  plan/review posts, so the public dashboard/journal are pushed the same way
  the laptop path pushes them. Publishing needs a `.site/.git` checkout with
  push credentials mounted into the container; absent that, publish is a clean
  no-op.
- `tzdata` is pinned in `requirements.txt` so the scheduler's timezone handling
  never depends on the base image shipping zoneinfo data.

Laptop (launchd) path: render and install the jobs for the current checkout
with `sh scripts/install_launchd.sh --load` — the committed plists carry a
`{{AGENT_ROOT}}` placeholder so a moved or freshly-cloned repo never runs a
stale absolute path.
