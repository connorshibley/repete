# PRODUCT.md — turning Repete into a subscription publication

Status: **Phase A in progress.** Nothing is for sale. No money may be
collected until the gates in §Revenue below are met and an attorney has
reviewed the model.

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
dry-run email digests (outbox.jsonl unless RESEND_API_KEY set), DRAFT
legal pages, and the revenue gate enforced at the checkout boundary.

Run it:
```bash
PUBLISHER_SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))") \
  uvicorn publisher.app:app --port 8080          # local
docker compose --profile publisher up -d          # container (memory/ mounted :ro)
```
Env vars: PUBLISHER_SESSION_SECRET (required), RESEND_API_KEY (real email),
STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET (real billing). Absent keys =
dry-run/stub — behavior flips via environment, never code.

### Phase C — Trust & compliance artifacts
Disclaimer component on every surface; ToS / Privacy / Risk Disclosure
scaffolding marked `DRAFT — REQUIRES ATTORNEY REVIEW`; `src/evidence.py`
exporting an audit pack (invariant proof, decision lineage, monthly
performance, model-version segments).

### Phase D — Enterprise hardening
SLOs + runbooks, dependency scanning in CI, secrets rotation, automated
backups **with a restore drill**, rate limiting, structured logging with no
secrets, incident response doc, SOC 2 readiness checklist.

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
