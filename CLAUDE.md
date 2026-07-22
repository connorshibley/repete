# CLAUDE.md — Instructions for Claude Code working on this project

## What this is
An autonomous **swing-trading** agent (stocks via Alpaca, **paper trading**) that:
documents every decision with reasoning in an append-only JSONL ledger, uses an
LLM only as a judgment layer (approve/downsize/veto), learns cautiously from
closed trades, and posts trade recaps to X. Built from an evidence-based design
(see `GUIDE.md` for the full walkthrough and the research rationale).

## Architecture (do not restructure without asking)
```
src/broker.py    Alpaca wrapper. Paper-mode double interlock lives here. Orders carry a
                 deterministic client_order_id (ta-SYM-side-YYYYMMDD, 2026-07-21) so a
                 crashed cycle rerun cannot double-submit.
src/preflight.py Fail-SAFE startup validation (2026-07-21): risk params, interlock, env
                 keys, ledger-tail integrity, timeframe. Any failure = no trading this
                 cycle + ledger preflight_failure + macOS alert. Opposite polarity from
                 data outages (which degrade gracefully) — on purpose.
src/scorecard.py Monthly performance vs S&P from cycle_complete equity snapshots + SPY
                 bars (2026-07-21). Surfaces in review.py + dashboard. The benchmark
                 goal is measured every month and published — never promised, and never
                 wired into sizing/signals (goal-chasing is the documented failure mode
                 of every LLM-trader reviewed).
src/datacheck.py Second-vendor price cross-check (2026-07-21): SPY close Alpaca vs
                 yfinance each cycle; same-session divergence > cap => degradation
                 event + entries blocked that cycle (exits never). Vendor outage fails
                 open silently — fires only when both vendors report and DISAGREE.
src/revalidate.py Quarterly strategy re-validation, REPORT ONLY (2026-07-21): reruns
                 each enabled strategy's CURRENT live params (never a grid re-search)
                 through the walk-forward gate on recent data; prints + ledger
                 `revalidation` event. Never auto-disables — live_kill is the fast path.
src/strategies/  Strategy ensemble (deterministic ONLY; LLM never generates signals):
                 ma_crossover (baseline), tsmom, xsmom, meanrev. Registry in __init__.py;
                 config `strategies:` gates ENTRIES per strategy; exits always route to the
                 strategy that OWNS the position (tagged on its ledger record), even if since
                 disabled. Entries: priority order, first surviving buy takes ownership.
                 A strategy may be enabled live ONLY after passing backtest.enablement_gate
                 (walk-forward OOS: positive, PF>=1.3, >=15 trades, beats B&H raw /
                 risk-adjusted / exposure-matched).
src/strategy.py  Compatibility facade over strategies/ (legacy generate_signal + indicators).
src/llm.py       Judgment layer: approve / downsize / veto. Can NEVER enlarge or invent trades.
                 Runs on llm.model (claude-sonnet-5, right-sized 2026-07-18 — evals show no
                 thinking-model edge for judge roles); learning passes use learning.model (Fable).
                 Judge prompt includes a bull/bear debate step (2026-07-21, TradingAgents-inspired,
                 single call): both cases argued in the JSON before the verdict, stored on the
                 ledger's llm_review; calibration impact measured by the judgments scoreboard.
src/risk.py      Hard rails: sizing (meanrev: stop-distance risk sizing, gate 2026-07-19 §8,
                 superseded vol_target; others: 1% notional), caps, trade-rate limit,
                 daily-loss kill switch (HALT file), swing guard (min_holding_days blocks
                 early exits — no day trading), chandelier trail (tsmom only, §7 — stop
                 ratchets up, never down), re-entry cooldown (meanrev only, §9),
                 entry drift guard (2026-07-21: buy skipped when live quote drifts >
                 risk.max_entry_drift_bps from signal price; fail-open on quote outage;
                 entries only — would have blocked all six 2026-07-16 stale-bars fills),
                 correlation heat cap (2026-07-21, EastEquity review: buy blocked when
                 >= risk.correlation_cap.max_correlated open positions have >= threshold
                 return correlation with it — "co-moving names are one bet"; entries
                 only, fail-open without bars; fail-open guard skips are ledgered as
                 "degradation" events and counted by review.py — silence must stay
                 distinguishable from "checked and fine");
                 param-gated down-regime exposure cap exists but is OFF (cannot bind),
                 pre-registered live kill criteria (2026-07-21, risk.live_kill: a strategy
                 with >=15 live closed trades and PF<0.8 stops ENTERING, exits unaffected —
                 the live-side mirror of the enablement gate, registered before any strategy
                 was near the threshold). Degradation SLO: ops.max_degradations_per_day
                 fail-open events in one day -> slo_breach event + macOS alert (main.py).
src/ledger.py    Append-only JSONL audit trail. Outcomes written only after close (outcome embargo).
src/memory.py    Retrieval layer: similar-setups trade sample for the judge (2026-07-21,
                 deterministic strategy/regime/symbol/indicator match — losers still
                 force-included; balanced random sample for signal-less callers),
                 ranked lessons, judge calibration + last-20-resolved-calls scoreboard,
                 regime — assembled into the review prompt.
src/lessons.py   Hypothesis book (memory/lessons.jsonl, append-only events + replay): falsifiable
                 lessons with a lifecycle candidate -> active | refuted | retired; staleness is
                 scope-tiered (symbol 21d / strategy 90d / regime 180d — learning.staleness_tiers). learnings.md is
                 a GENERATED view of this store — never hand-edit or treat it as source of truth.
src/judgments.py Judge calibration (memory/judgments.jsonl): every approve/downsize/veto logged,
                 later resolved (realized close or counterfactual) and scored; the judge sees its
                 own track record in the prompt. kind=llm and kind=rails bucketed separately.
                 Judgments also carry the judge's stated confidence (2026-07-21) — scored per
                 bucket vs realized win rate in review.py; measurement only for now (no caps).
                 The judge's context includes a deterministic CURRENT BOOK block (memory.py,
                 2026-07-21): open positions, unrealized P&L, gross exposure — broker-fresh.
                 risk.py also carries a portfolio heat cap (max_portfolio_heat_pct: total
                 open stop-risk + new entry risk <= 4% equity, entries only) and the
                 watchdog has a --catchup mode (launchd 15:55 ET: a missed 15:45 cycle
                 runs late while the market is still open instead of losing the day).
src/counterfactual.py  What a vetoed buy would have done (pessimistic stop-before-TP replay,
                 embargoed until min_holding_days + extra_days pass).
src/postexit.py  Post-exit runner tracking (memory/postexit.jsonl, append-only): every close
                 re-marked at 15/30/60d -> left_on_table | good_lock_in | mixed |
                 stopped_then_recovered | stop_confirmed. MEASUREMENT ONLY — feeds
                 review.py + future exit-rule gate evidence, never sizing/signals/judge.
src/modelver.py  Decision-surface fingerprint (config + strategy/risk/broker/llm code)
                 stamped on every ledger record; review.py segments the track record by
                 version. Stamp-only by design — no freeze; gates iterate the model.
src/regime.py    Deterministic market regime from SPY bars (trend x vol bucket); tags decisions,
                 judgments, and lesson scopes so off-regime evidence gets discounted.
src/market_context.py  Morning news awareness (Alpaca News API + free public RSS
                 from WSJ + CNBC + MarketWatch via news.rss_sources [headline/summary only, no
                 login/scraping/credentials — ToS-clean; full-text would need a
                 licensed API, not built] + LLM distill, 9:35 job):
                 today-only context for the judge/plan post + validated watchlist
                 NOMINATIONS. The LLM may summarize news and nominate scan symbols;
                 it still cannot generate, enlarge, or execute trades — a nominated
                 entry needs a deterministic strategy signal + judge review (told
                 "outside backtested universe, extra skepticism") + all rails, and
                 is capped at news.max_news_entries_per_cycle (1). Nominations are
                 validated deterministically (ticker shape, fresh bars, history).
                 Stale context is ignored; every failure = no context, normal trading.
                 Missed-run resilience (2026-07-21): if no context exists at cycle
                 start (all hourly fires missed), main.run_cycle self-heals with one
                 inline refresh before judging — fail-soft, never blocks the cycle.
src/learn.py     Learning engine: `python src/learn.py` (weekly, --meta for merge pass) +
                 learn.inline_pass() at every cycle end. Bounded LLM calls, never crashes a cycle.
src/x_poster.py  X recaps. dry_run default. Always disclose [PAPER]. Failures never block
                 trading. post_text = single choke point (disclosure + 275 cap + t.co link math).
src/journal.py   Public trade journal: ~500-word Fable-5 write-up per executed buy and per
                 close (template fallback) -> memory/journal.jsonl (append-only) -> journal.html
                 on the GitHub Pages site; recap tweets link to #<trade_id>. Cosmetic — never
                 blocks trading. Engagement metrics are NEVER read; learning stays outcome-based.
src/landing.py   Cream/orange public landing page (2026-07-21, Claude palette) — the
                 site's index.html front door; the dark terminal dashboard moved to
                 dash.html. Same conventions: self-contained, ledger-read, cosmetic.
src/dashboard.py Self-contained dashboard.html — dark terminal theme, hero total-P/L banner
                 (equity - reporting.starting_equity), P/L-over-time + per-trade P/L charts,
                 equity curve, positions, decisions + judge reasoning (filter chips), lessons,
                 calibration, slippage; vanilla inline JS only (tooltips/count-up/filters).
                 Regenerated at every scheduled
                 touchpoint and published with journal.html via scripts/publish_dashboard.sh
                 to https://connorshibley.github.io/trading-agent-dashboard/ (public repo).
                 The news brain (market_context) refreshes hourly 9:25-15:25 ET on
                 claude-haiku-4-5 (news.model) via com.trading-agent.newsbrain.
src/main.py      Orchestrator: state -> signal -> judge -> rails -> execute -> ledger -> learn -> post.
config.yaml      All parameters. .env holds secrets (never commit).
```

## Safety invariants — NEVER weaken these, even if asked casually
1. **Paper by default.** Live trading requires BOTH `mode: live` in config.yaml AND
   `LIVE_TRADING_CONFIRMED=YES` in .env. Never remove or bypass this interlock.
2. **The LLM cannot override risk rails.** `risk.py` checks run after the LLM review,
   in deterministic code. Keep it that way.
3. **Swing-only.** The swing guard (`min_holding_days`) must keep blocking exits on
   young positions. Only the daily-loss kill switch and broker-side protective
   stop/take-profit bracket legs (set deterministically at entry, before any LLM
   involvement; the chandelier trail may deterministically RAISE a stop leg later,
   never lower or remove it) may exit before `min_holding_days`; the guard
   continues to block all strategy-signal exits on young positions. `timeframe`
   stays `1Day`.
4. **Positions/equity always read fresh from the broker** (`broker.account()`,
   `broker.positions()`) — never inferred from memory, the ledger, or prior LLM output.
5. **Outcome embargo.** Lessons/evidence are generated only from CLOSED trades;
   counterfactual resolutions wait out the full horizon (min_holding_days + extra).
   Memory samples must keep force-including losing trades, and lesson retrieval keeps
   force-including a refuted CAUTION when any exist.
6. **Learning is conservative-only and validated.** Lessons feed ONLY the veto/downsize
   review prompt (never sizing/signals) and must EARN `active` status from evidence
   (>=3 supports, 2x contradicts); refutation and staleness retire them. All lifecycle
   transitions are ledger-mirrored. `learnings.md` is a generated view of
   `memory/lessons.jsonl` — never hand-edit it.
7. **X posts disclose [PAPER]** while paper trading. Never remove the disclosure.
8. **Secrets stay in .env** and out of git. Never print keys to logs.
9. If the owner asks to go live, walk them through the go-live gate in GUIDE.md §9
   first (2–3 months paper, ≥30 closed trades, beats buy-and-hold) instead of just flipping it.

## Conventions
- Python 3.11+, no framework. Keep modules small and dependency-light.
- Every new decision path must write a ledger record (including skips/rejections).
- External-call failures (LLM, X, data) degrade gracefully; they never crash the cycle.
- Test logic without network: import modules with a dict config and fake bars
  (see GUIDE.md; a crossover fixture is `[10]*6 + [9,9,9,20]` with fast=3/slow=5).

## Prioritized backlog (good next tasks)
1. ~~**Bracket orders**~~ DONE: ATR-based stop-loss + take-profit legs
   (`risk.brackets` in config.yaml, off by default; GTC legs; broker-side exits
   reconciled into the ledger at cycle start by `main.reconcile_closed_positions`).
2. ~~**Backtest harness**~~ DONE: `python src/backtest.py` — offline replay through
   `strategy.generate_signal` + `risk` sizing with fee/slippage, walk-forward split,
   vs buy-and-hold; every variant logged to `memory/backtest_trials.jsonl`.
3. ~~**Weekly review command**~~ DONE: `python src/review.py` — skeptical report
   (win rate, PF, vs SPY, lesson staleness) scored against the GUIDE.md §9 go-live gate.
4. **SQLite ledger backend** behind the same `Ledger` interface (JSONL stays default).
5. **Morning plan / evening review X posts** (respecting dry_run and [PAPER] disclosure).
6. ~~**Unit tests**~~ DONE: `tests/` (pytest, fully offline; run
   `.venv/bin/python -m pytest tests/ -q`).
7. ~~**Data-freshness guard**~~ DONE 2026-07-16: `risk.bars_fresh` +
   `risk.max_bar_age_days` — stale SPY aborts the cycle, stale symbols are
   dropped (response to the stale-bars API bug).
8. ~~**Heartbeat + watchdog**~~ DONE 2026-07-16: `memory/heartbeat` written on
   every cycle exit; `src/watchdog.py` (launchd 16:15 weekdays) alerts via
   macOS notification when a weekday cycle didn't run or HALT is engaged.
9. ~~**Fill-quality tracking**~~ DONE 2026-07-16: `main.record_fill_quality`
   appends measured slippage (signal vs fill, bps) per trade; surfaced in
   `review.py` for the go-live cost comparison.
10. ~~**tsmom index gate / meanrev entry cap / vol-regime stops**~~ DECIDED,
    final 2026-07-17 on a FROZEN data snapshot: ALL REJECTED (index gate was
    briefly adopted 07-16 on what turned out to be API re-fetch drift, then
    reverted). Param-gated code paths remain. See
    knowledge/backtest_candidates.md §2–4 + its METHOD NOTE: **all backtest
    variant comparisons must use one frozen `--bars-file` snapshot** — live
    re-fetches of the same window drift intraday.
11. ~~**Earnings-blackout entry filter**~~ DECIDED 2026-07-17 (frozen
    snapshot): ADOPTED for tsmom (N=3, `strategies.tsmom.earnings_blackout_days`),
    REJECTED for meanrev (hurts the dip edge) — per-strategy param, yfinance
    calendar via src/earnings.py (cached, fail-open). See
    knowledge/backtest_candidates.md §1.
12. **Dashboard + daily posts** DONE 2026-07-17: `src/dashboard.py` renders
    dashboard.html each cycle; `src/daily_posts.py` posts a 9:35 plan and
    4:20 review (launchd com.trading-agent.dailypost), read-only scans,
    [PAPER] enforced.
13. **common-trade ports** DONE 2026-07-19 (research in
    knowledge/port_research_2026-07-19.md; gates in backtest_candidates.md
    §7–§9): post-exit runner tracking (src/postexit.py), model-version
    fingerprint (src/modelver.py), citation-graded lessons, secrets-hygiene
    test, gap-adjusted heat report (review.py), chandelier trail (ADOPTED
    tsmom), stop-distance risk sizing (ADOPTED meanrev, superseded
    vol_target), re-entry cooldown (ADOPTED meanrev).
