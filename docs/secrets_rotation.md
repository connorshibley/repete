# Secrets rotation

All secrets live in `.env` (never in git — enforced by
tests/test_secrets_hygiene.py) and, in logs, are scrubbed by the redaction
filter in src/log.py. Rotation is manual by nature (each vendor console),
so this doc is the procedure.

## Inventory

| Env var | Vendor console | Used by | Cadence |
|---|---|---|---|
| ALPACA_API_KEY / ALPACA_SECRET_KEY | alpaca.markets → API keys | broker.py (paper account) | 90d or on suspicion |
| ANTHROPIC_API_KEY | console.anthropic.com | llm.py, learn.py, market_context.py, journal.py | 90d or on suspicion |
| X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET | developer.x.com | x_poster.py | 180d or on suspicion |
| PUBLISHER_SESSION_SECRET | self-generated | publisher sessions | rotating logs everyone out — do it on suspicion or quarterly |
| RESEND_API_KEY (future) | resend.com | digest email | 90d |
| STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET (future) | dashboard.stripe.com | billing | per Stripe guidance; roll webhook secret with endpoint changes |

## Standard rotation (any key)

1. Create the NEW key in the vendor console (do not revoke the old one yet).
2. Update `.env` on every host that runs the agent (laptop + server).
3. Prove it: `python src/health.py` and, for the affected surface, one live
   call (a cycle, a news refresh, an X dry-run).
4. Revoke the OLD key in the console.
5. Note the rotation date in this file's log below.

Order matters: new-then-revoke means zero downtime; revoke-then-new means
the next cycle runs with a dead key (preflight will fail safe, but why).

`PUBLISHER_SESSION_SECRET` special case: generate with
`python -c "import secrets; print(secrets.token_hex(32))"`. Rotating it
invalidates every session cookie (subscribers just sign in again via magic
link — no passwords exist to reset, by design).

## "Assume leaked" drill (run immediately on any suspicion)

1. Rotate the suspect key per above, revoke old FIRST this time (kill the
   leak before continuity).
2. Check vendor console usage/audit logs for calls you didn't make.
3. Alpaca: verify no unexpected orders (`broker.positions()` vs the ledger;
   it is a paper account, but a hijacked paper account still poisons the
   track record).
4. Grep history: `git log -p | grep -c <fragment>` must be 0; the
   secrets-hygiene test guards tracked files, this checks history.
5. Ledger an `event` record describing scope + response.

## Rotation log

| Date | Key | Reason |
|---|---|---|
| 2026-07-22 | (none yet — doc created) | — |
