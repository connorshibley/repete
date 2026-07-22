# SOC 2 readiness map

**NOT CLAIMING COMPLIANCE.** This maps what the repo actually does against
the Trust Services Criteria so that, if a real audit is ever worth its cost
(it is not at N=1 subscribers), the gap list already exists. It is honest
about what a solo project cannot satisfy.

## What exists today (evidence in the repo)

| Criteria area | What we have | Where |
|---|---|---|
| Change management | every decision-surface change fingerprinted and stamped on records; performance segmented by version | src/modelver.py, review.py |
| Logical access | secrets in env only, hygiene-tested; no passwords anywhere (magic links, hashes only); publisher cannot write agent state | .env convention, tests/test_secrets_hygiene.py, publisher/readonly.py |
| System operations | health status object, watchdog alerting, SLOs with alert paths, runbooks, incident process | src/health.py, src/watchdog.py, docs/ |
| Data integrity | append-only event streams, ledger-tail preflight, second-vendor price cross-check, restore drills | src/store.py, src/preflight.py, src/datacheck.py, scripts/restore_drill.py |
| Monitoring | structured redacted logs, degradation SLO with breach events, CI on every push | src/log.py, main.py, .github/workflows/ci.yml |
| Availability | container HEALTHCHECK, scheduler with catch-up runs, backups with pruning | Dockerfile, scripts/scheduler.py, scripts/backup.sh |
| Confidentiality | log redaction filter; no subscriber PII beyond email; token hashes not tokens | src/log.py, publisher/subscribers.py |

## Honest gaps (what a real SOC 2 would require that we do not have)

- **Formal written policies** (infosec policy, acceptable use, vendor
  management) — docs/ is operational, not policy.
- **Access reviews** — meaningless at one operator, required on paper.
- **Separation of duties** — impossible at N=1; compensating control is the
  append-only ledger + public dashboard (changes are visible, not approvable).
- **Vendor management program** — we depend on Alpaca, Anthropic, X, GitHub;
  no formal review of their reports.
- **Penetration testing** — none; the publisher's attack surface is small
  (no passwords, read-only data) but "small" is not "tested".
- **Continuous evidence collection tooling** (Vanta/Drata class) and the
  audit itself — pure cost until enterprise customers demand it.

## When to revisit

Only when a customer's procurement asks for it. The right order at that
point: policies written → tooling → Type I → Type II. Until then this file
is the map, and the invariants in CLAUDE.md are the real controls.
