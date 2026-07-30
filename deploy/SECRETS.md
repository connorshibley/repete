# Secrets for a deployed Repete

Every secret arrives as an **environment variable**. None is baked into the
image, committed, or logged (`src/log.py` redacts secret-named env values, and
`tests/test_secrets_hygiene.py` scans tracked files for key shapes).

## What each one unlocks — and what happens without it

| Variable | Required? | Without it |
|---|---|---|
| `ALPACA_API_KEY` | **yes** | Preflight fails; nothing trades. |
| `ALPACA_SECRET_KEY` | **yes** | Same. |
| `ANTHROPIC_API_KEY` | no | The LLM judge is skipped and every signal executes on **rules alone**. Trading continues — deterministic rails still apply — and the outage is ledgered as an `llm_judge:` degradation. |
| `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | no | X posting is skipped. Never blocks trading. |
| `ALERT_WEBHOOK_URL` | **strongly recommended off-laptop** | Alerts fall back to a macOS notification, which does not exist on a server — so a missed cycle, preflight failure or SLO breach is written to a log file and **nobody is told**. |
| `HEARTBEAT_PING_URL` | **strongly recommended off-laptop** | No external monitor. Every other check runs ON the host, so if the container or machine dies there is nothing left to notice, and silence is indistinguishable from a quiet market. This is the only check that survives the host going away. Treat the URL as a secret. |
| `FMP_API_KEY` | no | Fundamentals research only. **Nothing in `src/` reads it** — it exists solely for `scripts/probe_fmp_lookahead.py`, which decides whether FMP data may be BACKTESTED at all (restated figures, missing filing dates or a survivor-only universe each make a gate inflated and indistinguishable from a real edge). Absent = the probe refuses to run. Cannot affect trading. |
| `LIVE_TRADING_CONFIRMED` | **set to `NO`** | Half of the live interlock. Live needs BOTH this `=YES` **and** `mode: live`. Leave it `NO`. |
| `PUBLISHER_SESSION_SECRET` | publisher only | The publisher refuses to start rather than sign sessions with a default key. Generate: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `RESEND_API_KEY` | publisher only | All email writes to a dry-run outbox instead of sending. Setting it does **not** by itself arm the daily digest — that needs `publisher.digest.enabled: true` and `publisher.email.dry_run: false` as well. The key alone re-enables only transactional mail (the magic-link sign-in), which is the point of the separate flag. |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | publisher only | Billing runs in stub mode. **Stub mode grants nothing** unless `publisher.billing.stub_webhook_grants` is explicitly enabled — a production deploy that forgets these cannot hand out free entitlements. |

## Setting them

**Fly.io** — `flyctl secrets set KEY=value` (encrypted at rest, injected at
runtime, never in `fly.toml`).

**VPS** — a `.env` file next to `docker-compose.yml`, mode `600`, owned by the
deploy user. It is gitignored; `docker-compose.yml` reads it via `env_file`.

```bash
chmod 600 .env && ls -l .env      # -rw------- 
git check-ignore .env             # prints ".env" => safely ignored
```

## Rotation

Full procedure in `docs/secrets_rotation.md`. Short version: rotate at the
vendor, update the secret store, redeploy, then confirm with
`python src/health.py` and one manual cycle. Alpaca keys can be rotated without
touching positions — they are credentials, not account state.

## What is NOT a secret

`config.yaml` holds only parameters and is committed on purpose — the risk
rails are meant to be auditable. **Never** put a key in it; preflight and the
secret scanner both assume that separation.

## If a key leaks

1. Revoke at the vendor **first** (Alpaca: delete the key pair — this cannot
   move money on a paper account, but revoke anyway).
2. `touch HALT` to stop the next cycle while you sort it out.
3. Issue new keys, update the secret store, redeploy.
4. `git log -p --all -S'<leaked-prefix>'` to confirm it never entered history.
   If it did, rotating is mandatory — history rewriting alone is not enough.
