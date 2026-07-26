# Real-world checklist: from laptop paper bot to live product

The single ordered list. Every item is either measurable by code in this
repo or is an external action with a named owner (you, an attorney, a
vendor). Nothing on this list may be skipped by enthusiasm — the gates are
pre-registered precisely so a good month doesn't argue its way past them.

Status legend: [ ] open · [x] done · [~] partially

## Stage 1 — Run it like a service (do this first; no gate required)

- [~] **Always-on host.** Move off the laptop. Ready-to-run artifacts are in
  `deploy/` (2026-07-23): `deploy/README.md` (Fly.io **or** VPS, both running
  the same image and scheduler), `deploy/SECRETS.md` (every env var, what it
  unlocks, what degrades without it), `deploy/deploy.sh` (one command; refuses
  to start half-configured), `deploy/fly.toml`, `deploy/repete.service`.
  **Copy `memory/` across before the first cycle** or the track record restarts
  from zero. Laptop launchd remains the fallback
  (`sh scripts/install_launchd.sh --load`).
- [x] **Backups scheduled** (nightly 17:00 ET in scripts/scheduler.py) and
  **restore drill passing** (`python scripts/restore_drill.py`).
- [ ] **Alerts reach a phone.** Watchdog currently posts macOS
  notifications; on a server, route scheduler/watchdog errors to something
  that buzzes (email-to-SMS, ntfy.sh, etc.).
- [x] **CI green on every push** (.github/workflows/ci.yml).
- [ ] **Secrets exist only on the hosts that need them** and the rotation
  procedure has been run once for practice (docs/secrets_rotation.md).
- [x] **Monthly evidence pack** exported and skimmed
  (`python src/evidence.py`) — added to habit alongside the weekly review.

## Stage 2 — Trading go-live gate (GUIDE.md §9; measured by src/review.py)

All three, no exceptions, no early flips:

- [ ] **≥ 60 days of paper history** (2–3 months; the low end is the floor).
- [ ] **≥ 30 closed trades.**
- [ ] **Beats SPY buy-and-hold over the same window** — and read the
  equal-weight-universe baseline in the same report before celebrating.

Then, and only then, the interlock conversation: `mode: live` in
config.yaml AND `LIVE_TRADING_CONFIRMED=YES` in .env, sized far below the
paper account until live slippage is measured against the paper assumption
(review.py's fill-quality section).

- [ ] **Live sizing plan written down BEFORE the flip** (starting capital,
  per-trade risk, the date you'll compare live vs paper slippage).

## Stage 3 — Revenue gate (PRODUCT.md; enforced in publisher/gates.py)

Checkout physically refuses until all four hold:

- [ ] **≥ 30 closed trades** (shares evidence with Stage 2 on purpose).
- [ ] **≥ 90 days of history.**
- [ ] **Securities attorney has reviewed** the publication model, marketing
  copy, and terms (publisher's exemption is narrow — Lowe v. SEC).
      Then flip `publisher.attorney_signoff: true`.
- [ ] **Legal pages finalized** from the drafts in publisher/legal.py.
      Then flip `publisher.legal_pages_final: true`.
- [ ] Stripe LIVE keys only after all of the above; webhook secret set;
  one end-to-end test purchase refunded.

## Stage 4 — Entity & hygiene (parallel with Stage 3)

- [ ] LLC (or equivalent) + business bank account before the first dollar.
- [ ] Bookkeeping picked (even a spreadsheet) before revenue, not after.
- [ ] Disclosures re-checked everywhere the product speaks: X bio + pinned
  post, GitHub Pages footer (shipped Phase C), publisher pages (shipped
  Phase B), digest emails (built 2026-07-25; disclaimer in the body AND
  `[PAPER]` in the subject, since subjects survive forwarding when footers
  get trimmed).
- [ ] Before the first digest actually sends: a verified sending domain, then
  set `publisher.digest.enabled: true` **and** `email.dry_run: false` **and**
  `RESEND_API_KEY`. All three, deliberately — see PRODUCT.md. Rehearse with
  `scripts/send_digest.py --dry-run` and read `publisher_data/outbox.jsonl`
  before arming any of them.
- [ ] Insurance quote for media/E&O once revenue is real (cheap peace of
  mind for a publication business; attorney will have opinions).

## Stage 5 — Steady state (the boring list that keeps it alive)

- [ ] Weekly: `python src/review.py` read end to end.
- [ ] Monthly: evidence pack exported; scorecard month closed; backup
  restore drill confirmed green in scheduler logs.
- [ ] Quarterly: revalidation report read (src/revalidate.py); secrets
  rotated; this checklist re-scored.

---

*Created 2026-07-22 (Phase C/D). The unchecked boxes are the honest state
of the project — updating them requires doing the thing, not editing the
file.*
