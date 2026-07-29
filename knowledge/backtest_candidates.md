# Backtest candidates (from external knowledge review, 2026-07-16)

Rules distilled from the autonomoustrading.io review + our own research.
NOTHING here goes live without passing `backtest.enablement_gate`
(walk-forward OOS: positive, PF>=1.3, >=15 trades, beats B&H raw /
risk-adjusted / exposure-matched).

## METHOD NOTE — frozen snapshots required (discovered 2026-07-17)
Alpaca historical bars for an IDENTICAL request drift between fetches minutes
apart during market hours (same window, same code — verified simulate() is
bit-for-bit deterministic on fixed input). Consequence: **any variant
comparison across separate API fetches is invalid.** All gate runs must use
one frozen `--bars-file` snapshot per comparison (2026-07-17 snapshot:
`memory/bars_snapshot_2020_2026-07-10.json`). The 07-16 tsmom-gate adoption
was a casualty of this and was reverted after frozen-data re-validation.

## 1. Earnings-blackout entry filter — SPLIT VERDICT 2026-07-17
**Spec:** block new entries in single names (ETFs exempt) within N calendar
days before a scheduled earnings report (tested N = 0, 3, 5). Exits
unaffected.
**Rationale:** gaps break stop assumptions; overnight gap risk is
uncompensated for a 2-day-minimum swing hold.
**Data source:** yfinance `get_earnings_dates` (src/earnings.py: 24h cache,
fail-open, ETFs naturally exempt; ~50 dates/symbol back to 2014 incl.
scheduled future dates). Frozen calendar for gate runs:
memory/earnings_snapshot.json.
**Result (frozen bars + frozen calendar, same snapshot both strategies):**
- **tsmom: ADOPTED, N=3** — wins IS (+3.89 PF 2.62, dd 1.13 vs unfiltered
  +3.50 PF 2.29, dd 1.31), passes the gate, OOS +1.38 PF 2.22 ≥ baseline
  +1.25 PF 2.01. Trend entries right before a report carry gap risk with no
  compensating edge. Live as `strategies.tsmom.earnings_blackout_days: 3`.
- **meanrev: REJECTED** — the filter hurt IS monotonically (+0.43 → +0.38
  (N=3) → +0.35 (N=5)): pre-earnings dips that bounce are part of the
  dip-buying edge.
The param is therefore PER-STRATEGY (`strategies.<name>.earnings_blackout_days`),
not a global risk rail.

## 2. Time-varying stop width by vol regime — REJECTED 2026-07-17
**Spec:** stop_atr_mult 2.0 in low-vol regime, test 2.5–3.0 in high-vol
(regime from src/regime.py, deterministic at entry).
**Rationale:** fixed 2×ATR may whipsaw in high-vol regimes.
**Result (frozen snapshot, full grid × both strategies):** widened high-vol
stops were equal-or-worse in every combination — tsmom IS 3.50 (hv off) vs
3.47/3.46 (hv 2.5/3.0); meanrev IS 0.82 (hv off) vs 0.76/0.76. The 2×ATR
stop is already wide enough at this timescale. Param-gated code path
(`risk.brackets.stop_atr_mult_high_vol`) remains for future retests; off in
config.

## 3. Regime gate on tsmom entries — ADOPTED 2026-07-16, REVERTED 2026-07-17
**Spec:** block tsmom entries when SPY < SMA50 (regime down/*), test vs
current behavior (tsmom already requires symbol > its own SMA200).
**Rationale:** the judge repeatedly flags long momentum in down regimes; make
it a testable deterministic rule instead of paying LLM skepticism per trade.
**07-16 result (live-fetch runs — later found unreliable):** appeared to win
IS (+4.10 vs +3.51) with OOS tied; adopted.
**07-17 re-validation (frozen snapshot, both variants same data):** UNGATED
wins IS by return (+3.50 vs +3.31; the gated variant has better PF 2.78 vs
2.29 and lower dd), OOS statistically tied (+1.25 PF 2.01 ungated vs +1.23
PF 2.21 gated). The pre-registered rule (gate must WIN IS and OOS >= ungated)
is not met → REVERTED to `index_sma_period: 0`. The 07-16 IS "win" was API
re-fetch drift, not signal. Honest residual: the gate is roughly return-free
and slightly PF/dd-favorable — a defensible retest candidate after the next
genuine bear regime provides OOS data that actually exercises it.

## 4. Meanrev max-positions-per-cycle cap — REJECTED 2026-07-16, CONFIRMED 07-17
**Spec:** limit meanrev to 1 new entry per cycle (dips cluster on market-wide
down days, concentrating correlated entries).
**Result (07-17 frozen snapshot, both variants same data):** cap=1 wins IS
(+0.82 PF 1.22 vs uncapped +0.43 PF 1.08) but clearly LOSES OOS: +0.73
PF 1.58 / 133 trades vs uncapped +1.33 PF 2.12 / 170 trades. The clustered
dip entries the cap removes were profitable out-of-sample — an in-sample
mirage, now confirmed on clean single-snapshot evidence (the 07-16 rejection
had compared across fetches). Config unchanged (no cap). Param-gated code
path remains; all variants in memory/backtest_trials.jsonl.

## §5 Vol-targeted sizing (2026-07-18) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from the 2023–2026 research pass (vol-aware risk is the one
process-side upgrade with durable evidence). `risk.vol_target`: position
dollars scaled by target_annual_vol / realized_vol(20d), clamped [0.5, 1.5].
METHOD: frozen snapshot memory/bars_snapshot_2020_2026-07-10.json,
earnings_snapshot.json, slippage pinned 5 bps (matches prior rounds),
default params, split 0.7. Pre-registered adoption rule (before running):
adopt per strategy iff (a) OOS return ≥ baseline − 0.25pp, (b) OOS maxDD
strictly improves, (c) IS return ≥ baseline − 1pp.

- tsmom:  base IS +3.50 / OOS +1.25 maxDD 0.7 → vt IS +1.74 / OOS +0.76
  maxDD 0.6. Fails (a) and (c) → **REJECTED**.
- meanrev: base IS +0.43 / OOS +1.33 maxDD 0.3 → vt IS −0.02 / OOS +1.10
  maxDD 0.2, PF 2.12→2.16. Passes (a) −0.23pp, (b), (c) → **ADOPTED**
  (marginal, rule-driven; scoped via risk.vol_target.strategies=[meanrev];
  monitor live).

## §6 Down-regime gross-exposure cap (2026-07-18) — REJECTED (cannot bind)

`risk.regime_exposure`: in a down-trend regime, total gross exposure capped
at 50% of equity. Same frozen-snapshot method + pre-registered rule. Result:
IS/OOS identical to baseline for both tsmom and meanrev — at 1%-per-trade
fixed-fractional sizing with max 5 positions, gross exposure never
approaches 50%, so the cap cannot bind and (b) cannot be satisfied. A
forced-binding sanity check at cap=3% did alter behavior (tsmom OOS +1.22
maxDD 0.5 vs +1.25 maxDD 0.7) but 3% is not a deployable setting. Config
stays disabled; param-gated rail remains for a future where sizing scales.

## §7 Chandelier trailing stop (2026-07-19) — ADOPTED for tsmom, REJECTED for meanrev

Candidate from the common-trade comparison + literature: ratcheting
volatility trail on top of the existing 2×ATR initial stop. Effective stop
= max(initial stop, high-water-since-entry − 3.0×ATR(14)), never lowered
(LeBeau chandelier; Kaminski & Lo 2014 and Han/Zhou/Zhu 2016 find stop
rules add value specifically under momentum/serial correlation — so the
PREDICTION is: helps tsmom, hurts or does nothing for meanrev, whose exits
are already fast). Single pre-registered config: trailing_atr_mult=3.0,
no grid. METHOD: frozen snapshot memory/bars_snapshot_2020_2026-07-10.json,
earnings_snapshot.json, slippage pinned 5 bps, current live params,
split 0.7, strategies tsmom + meanrev.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS return ≥ baseline − 0.25pp, (b) OOS maxDD strictly improves,
(c) IS return ≥ baseline − 1pp.

## §8 Risk-based (stop-distance) sizing (2026-07-19) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from common-trade (Van Tharp / fixed-fractional-risk school;
Balsara 1992: smoother equity curves). Position dollars =
equity × risk_pct / stop_distance_fraction, so every trade risks the same
equity fraction entry-to-stop, instead of the same notional. Pre-registered
risk_pct = 0.10% (≈$100 true risk on $100k at a typical 4–8% ATR stop →
$1.2k–2.5k notional, deliberately the same scale as today's 1%-notional
sizing so the comparison is allocation shape, not leverage). When active it
REPLACES vol_target scaling for that strategy (both normalize by realized
vol; compounding them double-counts). Existing caps still clamp.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS return > baseline, (b) OOS maxDD ≤ baseline + 0.25pp,
(c) IS return ≥ baseline. (Return-dominant branch: the claim is better
risk-normalized allocation, not tail-cutting.)

## §9 Same-ticker re-entry cooldown (2026-07-19) — ADOPTED for meanrev, REJECTED for tsmom

Candidate from common-trade (min_days_between_entries_same_ticker=5).
Blocks a NEW entry in a symbol for N calendar days after that symbol's
last exit — anti-whipsaw churn control. Weakest evidence base of the three
(practitioner hygiene; no strong academic support). Single value: 5 days.
PRE-REGISTERED RULE (before running): adopt per strategy iff
(a) OOS PF ≥ baseline PF, (b) OOS return ≥ baseline − 0.25pp,
(c) OOS maxDD ≤ baseline. Expected to bind mostly on meanrev
(dip re-entries cluster); prediction: neutral-to-small effect.

### §7–§9 RESULTS (2026-07-19, same session as registration; baselines
reproduced yesterday's numbers exactly, confirming snapshot integrity)

Baselines: tsmom IS +3.88 dd 1.1 | OOS +1.38 PF 2.223 dd 0.7 (51 trades);
meanrev (with then-live vol_target) IS −0.02 dd 0.8 | OOS +1.10 PF 2.161
dd 0.2 (170 trades).

§7 trail 3.0×ATR:
- tsmom: IS +4.60 dd 1.1 | OOS +2.37 PF 2.598 dd 0.5 (94 trades — trail
  exits + re-entries roughly double activity). Passes (a) +0.99pp, (b)
  0.5<0.7, (c). **ADOPTED** (`risk.brackets.trailing_atr_mult: 3.0`,
  `trailing_strategies: [tsmom]`; live ratchet in main.update_trailing_stops
  via broker.replace_stop). The Kaminski-Lo/HZZ momentum prediction held.
- meanrev: OOS +1.11 dd 0.2 — dd does NOT strictly improve (tie). Fails (b)
  → **REJECTED** (exits already fast; +0.01pp is noise). As predicted.

§8 risk-sizing 0.1%:
- tsmom: IS +6.19 dd 2.3 | OOS +1.96 dd 1.1. Fails (b) (1.1 > 0.7+0.25)
  → **REJECTED** — return bought with proportionally more drawdown.
- meanrev: IS +0.21 dd 1.4 | OOS +2.38 PF 2.226 dd 0.3. Passes (a) +1.28pp,
  (b) 0.3 ≤ 0.45, (c) → **ADOPTED**, and it beats + SUPERSEDES vol_target
  (§5) head-to-head on this snapshot (+2.38 vs +1.10), so vol_target is now
  disabled (both normalize by realized vol; only one may be active).

§9 cooldown 5d:
- tsmom: OOS +2.35 PF 4.034 dd 1.0. Fails (c) (dd 1.0 > 0.7) → **REJECTED**
  despite the eye-catching PF — the rule was registered first and maxDD is
  the condition it fails. A future round may re-register with a dd-tolerance
  branch if live evidence warrants.
- meanrev: IS +0.50 PF 1.123 dd 0.6 | OOS +1.06 PF 2.245 dd 0.2. Passes
  (a) 2.245 ≥ 2.161, (b) −0.04pp ≥ −0.25, (c) dd ≤ → **ADOPTED**
  (`risk.reentry_cooldown: {days: 5, strategies: [meanrev]}`).

COMBINED-OVERLAY VERIFICATION (meanrev risk_sizing + cooldown, since each
was adopted alone): IS +1.34 PF 1.163 dd 1.1 | OOS +2.24 PF 2.282 dd 0.3 —
holds up (OOS well above baseline, dd inside §8 tolerance, best IS of any
arm). Verification only, not an adoption decision.

METHOD NOTE: adoption split by strategy forced per-strategy scoping into
the params (`trailing_strategies`, `risk_sizing.strategies`,
`reentry_cooldown.strategies`) — a global flag would have applied a tsmom
result to meanrev and vice versa.

## §10 — Universe expansion 25 → 38 unique (2026-07-21) — ADOPTED

Motivation: evidence velocity. At ~2 closes/week every learning mechanism
(lessons, calibration, live_kill, postexit) is starved; a wider gated
universe multiplies independent setups at UNCHANGED per-trade risk, and the
correlation heat cap (adopted earlier today) prevents the wider book from
collapsing into one bet.

Candidates (18, sector-diverse liquid large caps): XOM CVX WMT COST PG KO HD
MCD CAT BA LLY ABBV GS BAC ORCL AVGO CRM DIS.

METHOD (per the standing METHOD NOTE): ONE frozen snapshot — ~4.5y
(1097 daily bars/symbol, all 38 complete) fetched once 2026-07-21, plus a
frozen yfinance earnings calendar; every comparison ran offline from those
files. Live params only (no grid re-search — re-picking params inside a
universe decision would be data mining). walk_forward split 0.7; trials in
memory/backtest_trials.jsonl. Note: a first attempt on a 2y snapshot
produced zero OOS trades for tsmom/meanrev — the 30% OOS window (150 bars)
was shorter than their 200-bar SMA warmup. Windowing artifact, not signal;
the run was discarded and refetched longer. Recorded so nobody repeats it.

RESULTS (OOS, same snapshot, current 25 names vs expanded 38 unique):

| strategy     | current univ                    | expanded univ                  | gate (expanded) |
|--------------|---------------------------------|--------------------------------|-----------------|
| tsmom        | +0.02%, PF 1.02, 57t, dd 0.46%  | +0.72%, PF 1.72, 54t, dd 0.56% | PASS            |
| meanrev      | +0.65%, PF 1.57, 74t, dd 0.32%  | +0.84%, PF 1.51, 103t, dd 0.43%| PASS            |
| ma_crossover | +1.19%, PF 2.14, 56t, dd 0.45%  | +1.59%, PF 2.49, 54t, dd 0.56% | PASS            |

DECISION (pre-registered rule: adopt only if every enabled strategy passes
the gate on the expanded universe): all three pass, 3/3 improve or hold →
**ADOPTED**, config `symbols:` extended to 38 unique names. CORRECTION, same day: the original list held 25 names (not 20 — XOM/PG/HD/KO/DIS were already present past the visible fold), so the expansion is 25→38 and the first config edit briefly duplicated those five (43 entries). The dashboard boot screen surfaced the 43; duplicates removed. The backtest itself was always clean — its bars dict deduped to 38 unique symbols.

HONEST FLAG: tsmom on the CURRENT universe FAILS the gate on this recent
window (PF 1.02 — momentum has been thin in the 20-name universe lately).
This is exactly the drift class src/revalidate.py (also added 2026-07-21)
exists to catch quarterly. No action beyond the expansion (which restores
tsmom's OOS to PF 1.72); live_kill remains the realized-trades fast path.

## §11 — risk_sizing.risk_pct 0.1 → 5.0 (2026-07-23) — ADOPTED, but it does not bind

Owner requested 5% per-trade risk. Re-run on the SAME frozen snapshot §8 used
(`memory/bars_snapshot_2020_2026-07-10.json`, 25 symbols × 1637 bars,
2020-01-02 → 2026-07-09), live params, no grid re-search, per the METHOD NOTE.

**This is NOT a re-validation of §8.** §8 deliberately pre-registered 0.10%
*because* it produced the same notional scale as 1%-notional sizing, so the
test measured "allocation shape, not leverage". 5% asks a different question of
the same data, so it is recorded here as a new section.

Applying §8's pre-registered rule (adopt iff (a) OOS return > baseline,
(b) OOS maxDD ≤ baseline + 0.25pp, (c) IS return ≥ baseline):

| risk_pct | IS return | OOS return | OOS maxDD | OOS trades | OOS PF | verdict |
|---|---|---|---|---|---|---|
| 0.1 (baseline, §8) | +1.34% | +2.24% | 0.3% | 160 | 2.282 | — |
| 1.0 | +1.87% | +2.63% | 0.5% | 161 | 2.164 | passes |
| 2.0 | +1.87% | +2.63% | 0.5% | 161 | 2.164 | passes |
| **5.0** | **+1.87%** | **+2.63%** | **0.5%** | **161** | **2.164** | **PASSES** |

(a) +2.63 > +2.24 ✓  (b) 0.5 ≤ 0.55 ✓  (c) +1.87 ≥ +1.34 ✓ → ADOPTED.

**The finding that matters more than the verdict:** 1.0, 2.0 and 5.0 are
*byte-identical*. They are the same setting. `risk.size_order()` applies
`max_order_value_usd` ($2,000) AFTER the risk-based calculation, so on $100k
equity every one of them clamps to a $2,000 position:

| risk_pct | stop 4% | stop 7% | stop 15% |
|---|---|---|---|
| 0.1 | $2,000 / $80 loss | $1,400 / $98 | $600 / $90 |
| 1.0 / 2.0 / 5.0 | $2,000 / $80 | $2,000 / $140 | $2,000 / $300 |

So the gate validated **"$2,000-capped sizing beats 0.1%-uncapped sizing"**,
not "5% risk is safe". True per-trade risk stays 0.08–0.3% of equity.

Two corrections to claims made on 2026-07-22 while raising this parameter,
both refuted by the code and re-measured here:
1. "One 5% stop-out risks $5,000 and trips the 3% daily kill switch" — **false**.
   The clamp caps a single loss at ~$80–300; the kill switch fires at $3,000.
2. "At `max_portfolio_heat_pct: 4.0` every meanrev entry would be rejected" —
   **false**. Four open positions total ~$560 of heat against a $4,000 cap; a
   fifth entry is allowed. The cap was never binding, so the 4.0 → 10.0 raise
   was unnecessary and has been **reverted**.

**Consequence / open question:** the effective position-size lever is
`max_order_value_usd`, not `risk_pct`. Raising it would make 5% genuinely bind,
and only then could a single stop-out approach `daily_loss_limit_pct`. That
pairing must get its own gate run before it is changed.

## METHOD NOTE 2 — the OOS window is contaminated (2026-07-23)

The 70/30 walk-forward split puts OOS at **2024-07-23 → 2026-07-09**, and
§1–§11 scored **~11 adopt/reject decisions on that same window**. It is
therefore a SELECTION set, not a clean holdout: repeatedly choosing the variant
that scores best on a fixed window is in-sample fitting one level up.

Consequences, binding on all future work:
1. Quoted OOS figures (incl. "OOS +2.63%, PF 2.16, 3.58x exposure-matched") are
   an **upper bound on the true edge**, not evidence of it.
2. The only clean holdout that exists is the **LIVE FORWARD RECORD** — which is
   exactly what the go-live gate's 30-closed-trades requirement is for.
3. Backtests here **generate hypotheses; only live trading confirms them.** No
   deployment or risk increase may be justified by the mined OOS alone.
4. Every new section must record the **running trial count**, and the bar rises
   with it: with N prior comparisons, a marginal pass is more likely to be luck
   than edge. Prefer candidates that pass by a clear margin.

Trial count through §11: ~11 registered comparisons (plus grid arms inside them).

## §12 — position-slot cap `max_open_positions` (2026-07-23) — RULE PRE-REGISTERED

**Motivation (live evidence, not theory).** In 9 live days the bot generated
**146 buy signals** and executed **6**. Since 07-17 the ONLY rail blocking
entries is `max_open_positions: 5` (**68 blocks**); the five slots are
permanently held by long-horizon tsmom index positions entered on one day.
Meanwhile the actual RISK rails — portfolio heat (4%), correlation cap,
concentration (10%) — have **never once evaluated a candidate**, because
`max_open_positions` sits upstream of them in `risk.pre_trade_checks`. The
book's real risk is nowhere near any of them (heat ~$560 of a $4,000 cap).

So the binding constraint today is a **concurrency cap, not a risk cap**, and
the bot is rejecting ~100% of its own signal supply. Its own counterfactual
ledger scores the blocked trades **9 bad_veto / 1 good_veto**.

At the observed rate (1 closed trade / 8.86 days) the go-live gate's 30 closed
trades is **~8.7 months** away — and conditioned on the current book (5 slots
held by trend positions with ratcheting trails, 0 entries in 7 days) the
near-term rate is ~0.

**Candidate:** raise `max_open_positions` 5 → 8 → 12, letting the heat and
correlation caps become the real limit on concurrency.

**PRE-REGISTERED RULE (written before running):** adopt the largest N such that
(a) OOS return >= baseline (N=5) return,
(b) OOS maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute (the
    `daily_loss_limit_pct` ceiling — a book that can lose more than the kill
    switch tolerates in one day is not a capacity gain, it is leverage),
(c) OOS profit factor >= 1.3 (the standing gate bar) and >= baseline PF - 0.15,
(d) OOS trade count strictly increases (the whole point is evidence velocity).
If several N pass, prefer the SMALLEST that captures most of the trade-count
gain — capacity we cannot yet justify with live evidence is not free.

### §12 RESULT — SPLIT: meanrev PASSES, tsmom FAILS. Global raise NOT adopted.

One frozen snapshot (`bars_snapshot_2020_2026-07-10.json`, 25 symbols, frozen
earnings calendar), live params, no grid re-search. Trial count: ~13.

| strategy | N | OOS ret | maxDD | PF | trades | verdict |
|---|---|---|---|---|---|---|
| meanrev | 5 (base) | +2.63% | 0.5% | 2.164 | 161 | — |
| meanrev | **8** | **+3.03%** | 0.5% | **2.216** | **180** | **PASS** |
| meanrev | 12 | +2.98% | 0.5% | 2.156 | 182 | PASS |
| tsmom | 5 (base) | +2.12% | 0.6% | 2.398 | 97 | — |
| tsmom | 8 | +2.47% | 0.7% | 1.977 | 156 | **FAIL (c)** |
| tsmom | 12 | +2.64% | 1.1% | 1.730 | 230 | **FAIL (b)+(c)** |

**tsmom fails clause (c):** PF 2.398 → 1.977 → 1.730, well under the 2.248
floor, while win rate slips 43% → 39%. Its return RISES (+2.12 → +2.64), which
is exactly the trap clause (c) was written to catch: the extra trades are not
edge, they are more risk bought at a worse rate. At N=12 drawdown also nearly
doubles, breaching (b). **Pre-registration did real work here** — on return
alone tsmom's N=12 looks like the best cell in the table.

**meanrev passes at both.** Per the tie-break (smallest N capturing most of the
trade gain: N=8 captures 19 of 21 extra trades), the meanrev answer is **N=8**.

**Why this cannot simply be adopted:** `max_open_positions` is GLOBAL. Raising
it to 8 would admit mostly *tsmom* — the strategy that produces 93% of live buy
signals and whose marginal trades are the ones that degrade. That is the
opposite of the intent.

**Root cause, visible in the live book:** tsmom is a trend follower holding
positions for weeks (all 5 live slots, entered on one day, 7+ days held, trails
ratcheting, no exit in sight). meanrev exits on SMA5 with `max_hold_days: 7` and
would cycle fast — but can never get a slot. A single shared pool lets the
slow strategy starve the fast one.

## §13 — PER-STRATEGY slot allocation (2026-07-23) — PRE-REGISTERED, NOT YET RUN

**Candidate:** replace the single global `max_open_positions` with a per-strategy
allocation (e.g. `strategies.<name>.max_open_positions`), leaving the global
value as a hard ceiling over the whole book. Rationale is §12's split result
plus the live starvation mechanism above — not a desire for more exposure.

**PRE-REGISTERED RULE:** adopt only if, on the frozen snapshot,
(a) meanrev's allocation improves its OOS trade count with PF >= baseline - 0.15,
(b) tsmom's allocation is NOT raised above 5 (§12 showed it degrades),
(c) whole-book OOS maxDD <= 3.0pp absolute (`daily_loss_limit_pct` ceiling),
(d) the portfolio-heat and correlation caps are shown to bind at least once in
    the run — if a wider book never exercises the real risk rails, the slot
    count was never the true constraint and the change is not justified.

Requires code: `risk.pure_checks` counts positions globally today. Until it is
implemented and passes, **`max_open_positions` stays at 5.**

### §13 RESULT — ADOPTED for meanrev (alloc 8); tsmom + ma_crossover pinned at 5.

First, a correction that changes every earlier number in this file.

**THE BACKTESTER WAS MISSING TWO LIVE RAILS.** `simulate()` called
`risk.pure_checks` only; the **portfolio-heat cap and the correlation cap** live
in `risk.pre_trade_checks` and were therefore **never simulated**. Every gate
run through §12 was measured against FEWER rails than live trades under, so the
sim overstated achievable trade counts. Both are now applied in the sim
(`n_heat_blocked` / `n_corr_blocked` are recorded on every Result).

Re-measured baselines with the rails present:

| strategy | before (no rails) | after (rails simulated) |
|---|---|---|
| meanrev | +2.63%, PF 2.164, 161 tr | **+2.49%, PF 2.076, 160 tr** |
| tsmom | +2.12%, PF 2.398, 97 tr | unchanged (never reached a cap at N=5) |

Earlier sections' absolute figures are therefore mildly optimistic for meanrev
and should be re-run before being cited again.

**§13 ladder (rails simulated, one frozen snapshot, trial count ~16):**

| meanrev alloc | OOS ret | maxDD | PF | trades | heat blk | corr blk |
|---|---|---|---|---|---|---|
| 5 (base) | +2.49% | 0.5% | 2.076 | 160 | 0 | 7 |
| **8** | **+2.84%** | **0.5%** | **2.116** | **177** | 0 | 7 |
| 12 | +2.75% | 0.5% | 2.044 | 178 | 0 | 8 |

Clauses: (a) PASS at 8 and 12 — trades up, PF above the 1.926 floor.
(b) PASS — tsmom is pinned at 5, and is now pinned EXPLICITLY in config so it
cannot absorb the new headroom. (c) PASS — maxDD 0.5% vs the 3.0pp ceiling.
Tie-break selects **alloc 8** (177 of 178 trades, 94% of the gain).

**(d) PARTIALLY MET — stated plainly rather than reinterpreted.** The clause
required the heat cap AND the correlation cap to bind. The **correlation cap
bound 7-8 times** (a real risk rail is now the constraint, which was the
intent). The **heat cap bound 0 times** and structurally cannot at ~2%
deployment: measured book heat is ~$560 against a $4,000 cap. That is a flaw in
how I drafted (d) — requiring it to bind implicitly required a deployment
increase — not a failure of the evidence. Recorded here so the shortfall is
visible rather than buried.

**What this does and does not buy.** It removes the live starvation mechanism
(tsmom holding every slot for weeks while meanrev, which exits on SMA5, waits).
It does NOT increase per-trade risk, deployment, or any risk rail: per-trade
sizing, heat, correlation, concentration, the daily-loss kill switch and the
swing guard are all unchanged. The backtest gain is +17 OOS trades over ~2
years — real but modest. **It does not shorten the go-live gate; it only lets
evidence accrue somewhat faster.**

## METHOD NOTE 3 — the §1–§13 snapshot is GONE; those numbers are unreproducible (2026-07-23)

Every gate from §1 to §13 was scored on `memory/bars_snapshot_2020_2026-07-10.json`
(25 symbols x 1637 bars). `memory/` is in `.gitignore`, so **the file was never
committed and no longer exists on any machine reachable from this repo.**

This is a process failure with a specific consequence: *the verdicts survive as
prose, but nobody can re-derive them.* "Re-running §8 reproduces §8" is now an
unfalsifiable claim. Sections §1–§13 should be read as **recorded history, not
as re-checkable evidence.**

Fixed going forward:
- `scripts/build_snapshot.py` rebuilds snapshots from **yfinance** (no API keys,
  so any future session can do it) into **`data/snapshots/` — committed** — with
  a SHA256 in `MANIFEST.json`. `--verify` fails loudly on drift.
- Snapshots are gzipped JSON (~1.8 MB vs ~7.8 MB); `backtest.load_bars_file`
  accepts `.json.gz`.

**The new snapshot is NOT a drop-in replacement.** yfinance serves
split/dividend-ADJUSTED prices, the retired Alpaca snapshot served raw; and the
new file carries all **38** current symbols where the old one had 25. Different
universe AND different price basis. Therefore **no figure from §1–§13 may be
compared against a figure from §14 onward.** Every section below re-measures its
own baseline inside its own snapshot.

### Re-baselined on `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…)

38 symbols, 62,244 bars, live config, 70/30 walk-forward:

| strategy | OOS return | PF | trades | maxDD | gate |
|---|---|---|---|---|---|
| meanrev | +3.00% | 1.638 | 266 | 0.8% | PASS |
| tsmom | +2.07% | 2.170 | 93 | 0.6% | PASS |
| ma_crossover | +2.94% | 2.400 | 88 | 1.1% | PASS |

All three enabled strategies **re-pass the enablement gate on a different data
vendor, a wider universe and an adjusted price basis.** That is a genuine
robustness result — vendor-specific artifacts are one of the ways a backtest
edge turns out to be nothing — and it is the strongest thing the backtests say.
It is still not live evidence, and METHOD NOTE 2 still applies: this window has
been mined, and the live forward record remains the only clean holdout.

## METHOD NOTE 4 — multiple-testing correction is now arithmetic, not judgement (2026-07-23)

METHOD NOTE 2 clause 4 promised the bar would rise with the trial count, but
specified only "prefer a clear margin" — a judgement call, and judgement calls
drift toward the answer you wanted. `src/significance.py` replaces it:

- **Moving-block bootstrap** (block ~ sqrt(n)) over the OOS closed-trade P&L
  sequence, so a single lucky market stretch cannot masquerade as edge the way
  it would under naive per-trade resampling.
- **Bonferroni correction**: K arms in a section are each judged at 0.05/K.
- A candidate whose CI includes zero is reported **INCONCLUSIVE — not a pass.**

Two honest limits, both recorded in the module docstring: the two arms share
the same bars but are resampled independently, which *overstates* the variance
(so passing is harder than it should be — safe direction); and no bootstrap can
undo the fact that the OOS window itself has been mined.

## §14–§17 — PRE-REGISTERED RULES, WRITTEN BEFORE ANY CANDIDATE WAS RUN

Committed as its own commit, ahead of the results commit, so git timestamps
prove the rules preceded the numbers.

Protocol for all four, enforced by `scripts/gate_compare.py`:
1. every arm runs in-sample; 2. the winner is picked **on IS only**;
3. baseline and IS-winner run OOS; 4. the OOS difference goes through the
Bonferroni-corrected block bootstrap with K = number of arms.

**Deterministic rules (identical across the four sections):** adopt only if the
IS-selected arm, out of sample, satisfies ALL of —
(a) return >= baseline return,
(b) PF >= 1.30 (standing bar) AND >= baseline PF - 0.15,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute
    (`daily_loss_limit_pct`: a book that can lose more in a drawdown than the
    kill switch tolerates in a day is leverage, not capacity),
(d) >= 15 closed OOS trades.
**Plus:** the bootstrap CI on per-trade P&L must exclude zero in the
candidate's favour. Deterministic-pass + inconclusive-bootstrap = **KEEP THE
INCUMBENT**; the rules passing inside the noise band is not a result.

- **§14 xsmom re-test.** Rejected 2026-07-15 by 0.13pp on the 25-symbol
  universe — the closest rejection in this repo — and never retried on the 38
  names that lifted the other three. Judged by the standing `enablement_gate`,
  since this is enable/don't-enable rather than a parameter change.
- **§15 exit rules.** `max_hold_days: 7` and `exit_sma_period: 5` were adopted
  at the original enablement and have **never been gated at all** — the largest
  ungated surface in the system. Arms grid both.
- **§16 chandelier ATR multiple.** §7 registered a single value (3.0) with no
  grid; the swing literature spans 2.5–3.0. Arms: 2.0 / 2.5 / 3.0.
- **§17 Donchian breakout.** The absent classical swing family. Fits existing
  plumbing (single-asset, no cross-section, like meanrev). Judged by
  `enablement_gate`; it may only be enabled if it passes cleanly.

Running trial count entering §14: ~13 registered comparisons plus grid arms.

### §14–§17 RESULTS (2026-07-23) — FOUR CANDIDATES RUN, **ZERO ADOPTED**

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…, 38 symbols,
62,244 bars). Rules were committed in the preceding commit; results in this one.

**§14 xsmom re-test — NOT ENABLED.**
On the 38-name universe xsmom clears the standing `enablement_gate` outright:
OOS **+2.91%, PF 3.067, 46 trades, maxDD 1.1%** — the highest profit factor of
any strategy in this repo. It fails the significance clause: mean $+63.33/trade
with a 95% CI of **[-19.68, +186.65]**, P(mean>0) = 87.5%. Per the pre-registered
rule (deterministic pass + inconclusive bootstrap = keep the incumbent), it
**stays disabled.** The highest PF in the repo is also its least certain number;
46 trades with 51.6% of gross profit in 3 of them is not 46 pieces of evidence.

**§15 exit rules — REJECTED.**
`max_hold_days` 5/7/14 x `exit_sma_period` 3/5/10 (5 arms). IS winner `hold14`
(+3.94% IS, the best of the five). Out of sample it is a dead heat with the
incumbent: **+2.99% vs +3.00%**, per-trade difference **-$0.04**, CI
[-17.76, +17.55]. All five arms land within 0.6pp of each other OOS.

Worth recording: the arm that looked best **out of sample** was `exit_sma3`
(+3.29%, PF 1.754). Selecting on OOS would have "adopted" it. Selection happens
in-sample only, which is the entire reason that did not happen. The finding is
that the largest ungated surface in the system turns out to be a **flat**
surface — the incumbent 7-day / SMA5 exits are fine, and nothing here is worth
changing.

**§16 chandelier ATR multiple — REJECTED BY NOISE (the interesting one).**
Arms 2.0 / 2.5 / 3.0(incumbent) / 4.0 on tsmom. IS winner `trail2.5`, and out of
sample it beats the incumbent on **every single point estimate**:

| arm | OOS ret | PF | maxDD | trades |
|---|---|---|---|---|
| 3.0 (incumbent) | +2.07% | 2.170 | 0.64% | 93 |
| **2.5 (selected)** | **+2.54%** | **2.558** | **0.46%** | **110** |
| 2.0 | +2.02% | 1.865 | 0.88% | 158 |
| 4.0 | +2.32% | 2.562 | 0.65% | 76 |

Every deterministic rule passes. The bootstrap does not: per-trade difference
**$+0.77**, CI **[-36.50, +37.29]**. Higher return, higher PF, lower drawdown,
more trades — and all of it inside the noise band at n≈100 with fat-tailed trend
P&L. **Incumbent kept at 3.0.**

This is the case the significance clause exists for, and the honest reading is
uncomfortable: with ~100 trades of trend P&L, this method cannot distinguish a
genuine 23% return improvement from luck. That is a statement about how little
these sample sizes support, not about 2.5 being wrong. **§16 is the strongest
candidate to re-test once live trades exist** — flagged, not adopted.

**§17 Donchian breakout — REJECTED, stays disabled.**
New strategy (`src/strategies/donchian.py`, 20-bar entry / 10-bar exit / SMA200
filter — the classical Turtle pair, chosen before running, not fitted). OOS
**+1.54%** against an exposure-matched bar of **+1.95%** — fails by 0.41pp, plus
PF ex-top-3 of **0.874**, i.e. it loses money without its three best trades.

The mechanism did behave as predicted in the module docstring (34% win rate,
PF 1.584 — win rarely, win large). It simply is not good enough. Code and tests
are kept: the strategy is registered, `enabled: false`, and a test pins it that
way, so the failure is documented rather than deleted.

## §18 — Concentration + bootstrap edge report (2026-07-23) — NEW STANDING DIAGNOSTIC

`scripts/edge_report.py`. Profit factor answers "did it make money here"; it does
not answer "on how many independent events does that rest". Those two questions
rank the strategies **in nearly opposite order**:

| strategy | PF | top3% of gross profit | PF ex-top3 | median trade | edge vs zero |
|---|---|---|---|---|---|
| xsmom | 3.067 | 51.6% | 1.484 | -$36.96 | not shown (87.5%) |
| ma_crossover | 2.400 | 54.7% | **1.087** | -$25.42 | not shown (97.1%) |
| tsmom | 2.170 | 32.0% | 1.475 | -$14.48 | **at uncorrected only** (99.0%) |
| meanrev | 1.638 | **6.6%** | 1.531 | **+$15.98** | **at uncorrected only** (99.0%) |
| donchian | 1.584 | 44.8% | **0.874** | -$21.17 | not shown (84.7%) |

Read by headline PF the ranking is xsmom > ma_crossover > tsmom > meanrev.
Read by how broadly the profit is earned it is close to the reverse. **meanrev
is the only strategy whose median trade makes money**, and the only one whose
result does not depend on a handful of events.

**The finding that matters most, and it is not a comfortable one:** after
Bonferroni-correcting for the five strategies compared, **not one of them has an
OOS per-trade edge distinguishable from zero.** meanrev and tsmom clear the
uncorrected 5% bar (P(mean>0) = 99.0% each); ma_crossover, xsmom and donchian do
not clear even that.

What this does and does not mean:
- It is **not** "the strategies are worthless". Every point estimate is positive,
  and absence of evidence at n=46–266 is not evidence of absence.
- It **is** "the backtest cannot certify any of these edges", and no gate that
  looked only at return/PF/drawdown would ever have said so. §1–§17 all passed
  through gates that could not see this.
- **ma_crossover is the weakest by evidence and is currently ENABLED** (PF 2.400
  is 54.7% three trades; PF ex-top-3 is 1.087, i.e. break-even). It is left
  enabled — disabling it weakens nothing and would cut trade velocity, and the
  decision belongs to the owner — but it should not be described as a validated
  edge. It is the ensemble's baseline/teaching device, and it is performing like
  one.
- It is the sharpest possible argument for the standing invariant: **the live
  forward record is the only holdout**, and 30 closed trades is the point at
  which any of this starts to be checkable.

Running trial count after §18: ~18 registered comparisons plus grid arms.

## §19 — Faster entries (2026-07-23) — RULE PRE-REGISTERED, results in the next commit

**Motivation (owner request, plus a divergence nobody had noticed).** The owner
asked for the agent to enter "as soon as it sees an opportunity" rather than
waiting for a fixed time. Investigating that surfaced a structural
**live-vs-backtest divergence**:

- `backtest.py` computes signals on the **close of bar i** and fills at the
  **open of bar i+1**. Every gate number in this file rests on that model.
- The live scheduler runs one cycle at **15:45 ET** and fills **immediately**,
  at today's close — a full bar earlier than the research assumes.

So the live bot has never traded the model it was validated against. A cycle at
the **open**, reading the last COMPLETED daily bar, is therefore not a
compromise between "faster" and "faithful" — it is *both*.

**§19a — 09:35 ET open cycle. SHIPPED (no gate required).** This is not a
strategy change: same strategies, same params, same rails. It changes *when*
the existing signals are acted on, and it moves live behaviour toward the
backtest rather than away from it. `datacheck.drop_forming_bar()` trims today's
partial bar (mid-session `broker.bars()` returns a forming bar whose close is
just the current price); the 15:45 cycle deliberately does NOT opt in, because
15 minutes before the bell the forming bar is effectively the close, which is
what every gate measured. 9 tests pin the guard, including one that fails if
the scheduler ever drops the `--open-cycle` flag.

Explicitly NOT done: intraday re-evaluation every N minutes. That runs
RSI(2)/SMA200 against a partial bar — an input no gate has measured — and
`config.yaml:63` warns against exactly it. The owner asked for faster; faster
was available without going there.

**§19b — `max_trades_per_day` 3 -> 5 / 8 / 12. PRE-REGISTERED, NOT YET RUN.**
The cap is enforced in the simulator (`backtest.py`, `fills_by_date`), so it is
honestly testable. With meanrev now allocated 8 slots (§13) and a second daily
cycle, a cap of 3 is more likely to bind than it was.

**PRE-REGISTERED RULE (written before running):** adopt the largest N whose
IS-selected arm, out of sample, satisfies ALL of —
(a) return >= baseline (N=3) return,
(b) PF >= 1.30 AND >= baseline PF - 0.15,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed trades,
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L excludes zero in
the candidate's favour. Deterministic pass + inconclusive interval = **KEEP THE
INCUMBENT** (this clause has already rejected §16, where the candidate beat the
incumbent on every point estimate). If several N pass, prefer the SMALLEST that
captures most of the trade-count gain.

Running trial count entering §19: ~18 registered comparisons plus grid arms.

### §19b RESULT — the gate could not answer this; measured directly. ADOPTED 3 -> 5.

**The pre-registered rule was INAPPLICABLE, and saying so matters more than the
verdict.** All four arms (3/5/8/12) came back **byte-identical** — same return,
PF, drawdown and trade count to the last digit.

The reason is a **second backtester/live divergence**, in the same family as the
missing heat/correlation caps found in §13:

| | live | backtest |
|---|---|---|
| `_trades_today()` counts | **the whole ensemble** (`risk.py`) | one strategy alone (`fills_by_date`) |
| effective cap for meanrev | 3 shared with 3 other strategies | 3 all to itself |

`simulate()` replays ONE strategy at a time, so a *global* rail is measured as a
per-strategy rail. meanrev alone averages 0.54 entries/day and never reaches 3 —
hence four identical arms. The instrument cannot see the parameter. Reporting
"deterministic PASS, significance inconclusive" would have been a verdict
generated by a broken measurement, so the rule is recorded as inapplicable
rather than applied.

**Measured directly instead.** Replaying the three ENABLED strategies over the
OOS window and summing their entries per calendar date:

- 447 entries across 231 entry-days, mean **1.94/day**
- distribution: 122 days with 1, 41 with 2, 39 with 3, 22 with 4, 5 with 5,
  1 with 6, 1 with 7

| cap | binds on | entries refused |
|---|---|---|
| **3 (incumbent)** | 29 of 231 days | **39 (8.7%)** |
| **5 (adopted)** | 2 of 231 days | 3 (0.7%) |
| 8 | 0 days | 0 |
| 12 | 0 days | 0 |

**Adopted 5.** The pre-registered "prefer the SMALLEST N that captures most of
the gain" clause decides it cleanly: 5 recovers 92% of what the cap was
refusing, and 8 and 12 recover **literally nothing** beyond it. Raising further
would be buying capacity that the evidence says is never used.

Note what this claim is and is not. It is **not** "returns improve" — no edge
estimate is involved and none is asserted. It is "the rate limiter was refusing
8.7% of the entries the strategies had already decided on, and 5 stops that."
That is a capacity measurement, which is why it is adoptable where §16's
apparent improvement was not: §16 asked the data to certify a better edge and
the data could not; this asks how often a counter fires, and the data answers
exactly.

**Upper-bound caveat:** the three strategies were replayed independently, so in
a joint run they would compete for slots, heat and correlation headroom. 447 is
an **upper bound** on combined demand and 8.7% an upper bound on what the cap
costs. The direction of the error is against the change, not for it.

**Known limitation, recorded not fixed:** the backtester still cannot model
ensemble-wide rails (`max_trades_per_day`, and the global `max_open_positions`
for the same reason). Fixing it means a joint multi-strategy simulator — a real
piece of work, and out of scope here. Until then, any gate on a GLOBAL rail must
be treated as structurally uninformative and measured the way §19b was.

Running trial count after §19: ~19 registered comparisons plus grid arms.

## §20 — RE-GATE UNDER THE ENSEMBLE SIMULATOR (2026-07-23) — RULES PRE-REGISTERED

`backtest.simulate_ensemble()` now replays **all enabled strategies against one
shared set of rails**, the way the live bot trades. `simulate()` is unchanged
and still measures a single strategy in isolation.

**Equivalence proof (why ensemble numbers can be trusted):** with exactly one
strategy enabled, the ensemble reproduces `simulate()` **trade-for-trade** —
verified for all five strategies on the frozen snapshot, pinned as a
parametrized test. So ensemble behaviour is single-strategy behaviour *plus
contention*, and nothing else.

### The ensemble baseline — a number that has never existed before

Live config, OOS, `bars_2020-01-01_2026-07-10.json.gz`:

**return +3.232% | PF 1.812 | maxDD 1.069% | 183 trades | 42% win | 5.73% deployed**

Compare with what every previous gate implicitly assumed — the three strategies
scored separately, each on its own $100k:

| | ensemble (shared $100k) | solo (own $100k each) |
|---|---|---|
| meanrev | **23 trades** | 285 trades, +2.963% |
| tsmom | 92 trades | 93 trades, +2.096% |
| ma_crossover | 68 trades | 91 trades, +2.730% |
| total | 183 trades, +3.232% | 469 trades, +7.789% |

### The finding: §13 does not do what §13 claimed

§13 (shipped this morning) diagnosed meanrev as starved by tsmom holding every
slot, and fixed it by raising meanrev's allocation to 8. Measured under the
ensemble, **meanrev executes 23 of the 285 trades it makes alone** — it is still
starved, and the 8 slots are close to irrelevant.

The mechanism is **priority order**, which §13 never touched. Entry priority is
`ma_crossover(1) -> tsmom(2) -> meanrev(4)`, so the two trend strategies are
offered every symbol first and consume the GLOBAL ceiling of 8 before meanrev is
consulted at all. Giving meanrev a private allocation of 8 cannot help when the
global pool is already empty.

The §13 diagnosis was right. The §13 fix addressed the wrong mechanism, and only
a single-strategy simulator could have made that look like a success.

### PRE-REGISTERED RULES (written and committed before any candidate was run)

All arms scored with `simulate_ensemble()` on the frozen snapshot, IS-only
selection, then OOS. Baseline = the live config exactly as shipped.

**§20a — entry priority order.** Arms: current (ma_x, tsmom, meanrev);
meanrev-first; meanrev-first-then-tsmom; round-robin-by-fewest-open. Adopt the
arm whose OOS satisfies ALL of —
(a) ensemble return >= baseline return,
(b) ensemble PF >= 1.30 AND >= baseline PF - 0.15,
(c) ensemble maxDD <= baseline x 1.5 AND <= 3.0pp absolute,
(d) ensemble trade count >= baseline (evidence velocity is the point),
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L excludes zero in
the candidate's favour. Deterministic pass + inconclusive CI = **KEEP THE
INCUMBENT** (this clause has already rejected §16 and gated §14).

**§20b — re-gate the §13 slot allocation** under the ensemble, now that the
instrument can see contention. Arms: current (8/5/5, global 8); equal (5/5/5);
meanrev-heavy (12/5/5, global 12). Same clauses.
**If the ensemble shows §13's allocation is not doing the work its section
claims, the honest outcome is to revert or restate §13, not to keep it because
it is already shipped.**

**§20c — re-gate `max_trades_per_day` (§19b, 3 -> 5)** under the ensemble. §19b
was adopted on a direct measurement that SUMMED three independent replays, an
explicit upper bound on combined demand. The ensemble measures the real thing.
Same clauses; **revert to 3 if 5 is not justified.**

Running trial count entering §20: ~19 registered comparisons plus grid arms.

### §20 RESULTS (2026-07-23) — the instrument reversed two of my own decisions

**§20b — §13's slot allocation is INERT. Restated, not reverted.**

| arm | OOS ret | PF | maxDD | trades |
|---|---|---|---|---|
| baseline 8/5/5, global 8 | +3.232% | 1.812 | 1.069% | 183 |
| **equal 5/5/5, global 8** | **+3.232%** | **1.812** | **1.069%** | **183** |
| meanrev 12, global 12 | +2.932% | 1.520 | 1.092% | 257 |

`5/5/5` is **byte-identical** to the shipped `8/5/5`. meanrev's allocation of 8
does nothing, because the GLOBAL ceiling of 8 is consumed by the two
higher-priority strategies before meanrev is ever consulted. Widening to 12
FAILS the deterministic rules outright (return down, PF 1.812 -> 1.520).

Left at 8 — it costs nothing and becomes correct if priority is reordered — but
**§13's section overstates what it achieved.** §13 said it "fixes tsmom starving
fast-cycling meanrev". It does not. The diagnosis was right and the fix was
aimed at the wrong mechanism, which only a single-strategy simulator could
have made look like a success.

**§20c — §19b REVERTED. `max_trades_per_day` back to 3.**

| cap | OOS ret | PF | trades |
|---|---|---|---|
| **3 (restored)** | **+3.237%** | **1.821** | 182 |
| 5 (§19b, shipped hours earlier) | +3.232% | 1.812 | 183 |
| 8 | +3.232% | 1.812 | 183 |
| 12 | +3.232% | 1.812 | 183 |

§19b's evidence summed three INDEPENDENT single-strategy replays and concluded a
cap of 3 was refusing 8.7% of entries. That sum was an explicit upper bound —
the strategies compete in reality — and jointly the cap **barely binds at all**,
because the global slot ceiling binds first. The difference between 3 and 5 is
one trade and 0.005pp, with 3 marginally ahead on both return and PF.

Reverted to 3. A circuit breaker should be as tight as it can be when loosening
it buys nothing. **§19b was a no-op recorded as a win; it stood for four hours.**

**§20a — entry priority is the REAL starvation mechanism. Deterministic PASS,
significance INCONCLUSIVE — escalated to the owner rather than self-approved.**

| arm | OOS ret | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| baseline (ma_x 1, tsmom 2, meanrev 4) | +3.232% | 1.812 | 1.069% | 183 | **23** |
| meanrev-first | +4.387% | 1.864 | 1.069% | 232 | 78 |
| **meanrev-then-tsmom (IS-selected)** | **+4.324%** | **1.827** | **1.069%** | **256** | **103** |
| meanrev 2nd | +4.201% | 1.818 | 1.069% | 242 | 80 |

All six deterministic clauses PASS: return +34% relative, trades +40%, PF
essentially unchanged, and maxDD **identical to three decimals**. The bootstrap
is inconclusive: per-trade P&L $16.89 vs $17.66, CI [-23.28, +25.96].

Per the pre-registered rule (deterministic pass + inconclusive CI = keep the
incumbent), **the incumbent stands and nothing was changed.**

But the rule has a drafting flaw worth naming, the same class as §13 clause (d)
and §19b: **it tests per-trade EDGE, while this candidate claims more trades at
the SAME edge.** A velocity change should not be expected to move per-trade P&L,
so the significance clause is measuring the wrong quantity — and the honest
response is to say so and hand the decision to the owner, not to quietly
reinterpret a rule I wrote before seeing the result. Escalated.

Running trial count after §20: ~23 registered comparisons plus grid arms.

### §20a ADOPTED — by OWNER DECISION on an escalated call, 2026-07-23

The owner was shown the numbers, the passing deterministic clauses, the
inconclusive interval, and the argument that the interval measures the wrong
quantity — and chose to adopt `meanrev -> tsmom -> ma_crossover`.

**Recorded plainly: this was NOT a rule-pass.** The pre-registered clause said
keep the incumbent; the owner overrode it with the reasoning on the table. That
is a legitimate way to decide, and it is different from the change having
cleared the bar. Anyone reading this later should not cite §20a as evidence of
edge.

**Re-verified at the SHIPPED config** (`max_trades_per_day: 3`, since §20a was
gated while the cap was still 5 — the arm as measured is not quite the arm that
ships, and pretending otherwise would be sloppy):

| | OOS return | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| old priority | +3.237% | 1.821 | 1.069% | 182 | 23 |
| **shipped** | **+4.749%** | **1.949** | **1.069%** | **245** | **100** |

All six deterministic clauses still PASS at the shipped config, and per-trade
P&L now points slightly upward ($19.38 vs $17.78) rather than down — though the
interval is still inconclusive, [-20.98, +29.00]. **Max drawdown is unchanged to
three decimals**, which is the single most reassuring number here: the extra
trades did not buy return with risk.

`ma_crossover` was demoted to priority 3 rather than disabled (owner's earlier
decision to keep it enabled stands). It is the weakest strategy by evidence, so
it should not get first refusal on every symbol.

## METHOD NOTE 5 — edge claims vs capacity claims (2026-07-23)

The significance clause has now been the wrong instrument **three times**:

| section | the claim | why the bootstrap misfired |
|---|---|---|
| §13 clause (d) | rails should bind | required a deployment increase to be satisfiable |
| §19b | the rate cap refuses entries | arms were byte-identical; it measured nothing |
| §20a | more trades at the same edge | it tests per-trade edge, which is not the claim |

The pattern: `src/significance.py` answers **"is the per-trade edge better?"**
That is exactly right for an EDGE claim and irrelevant to a CAPACITY claim —
"this rail is refusing N trades the strategies already decided on" is a COUNT,
deterministic given the data, not a noisy estimate.

**Binding on future sections:** state at pre-registration time whether a
candidate is an EDGE claim or a CAPACITY claim.
- **EDGE** -> the bootstrap CI must exclude zero. Unchanged.
- **CAPACITY** -> the bootstrap must show per-trade P&L is **not significantly
  WORSE** (`ci_high > 0`), and the deterministic clauses carry the decision.
  Capacity without an edge is just more slippage, so the drawdown and PF clauses
  stay mandatory.

This is written AFTER §20a and does not retroactively convert it into a pass.
§20a remains an owner override; the note exists so the next section is drafted
correctly rather than escalated.

## §21 — EXIT SPEED UNDER THE ENSEMBLE (2026-07-23) — RULES PRE-REGISTERED

**Claim type: CAPACITY** (declared before running, per METHOD NOTE 5).

**Why this is not a re-run of §15.** §15 gated `max_hold_days` and
`exit_sma_period` and found a **flat surface** — all five arms within 0.6pp OOS —
and concluded the incumbent exits were fine. But §15 was measured **solo**, and
in a solo run a position closing early frees a slot that **nobody else can use**.
Under the ensemble that slot goes straight to tsmom or ma_crossover.

That is exactly the blind spot that made §19b a no-op and §13 inert. §15's
"flat surface" may be an artifact of the instrument rather than a fact about
exits, and the only way to know is to re-run it on the simulator that can see
slot contention.

**Arms** (deliberately small — K inflates the Bonferroni correction, so a wide
grid actively raises the bar against the candidate):
`exit_sma_period` 3 / 5 (incumbent) / 7, and `max_hold_days` 5 / 7 (incumbent) / 10.
All scored with `simulate_ensemble()`; IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) ensemble return >= baseline return,
(b) ensemble PF >= 1.30 AND >= baseline PF - 0.15,
(c) ensemble maxDD <= baseline x 1.5 AND <= 3.0pp absolute,
(d) **closed trades > baseline** — this is the entire point of the section; an
    arm that does not increase evidence velocity has no claim here even if its
    return is prettier,
**and**, per the CAPACITY branch of METHOD NOTE 5, the Bonferroni-corrected
bootstrap must show per-trade P&L is **not significantly WORSE** (`ci_high > 0`).
The PF and drawdown clauses are non-negotiable: capacity without an edge is just
more slippage.

**Fallback arm-set if exits are genuinely flat:** universe expansion beyond the
current 38 names, judged by the same clauses. §10 took it 25 -> 38 and every
strategy re-passed; more liquid large-caps means more independent setups per
week at unchanged per-trade risk, with the correlation cap preventing it from
becoming one bet.

**Stated before the run so it cannot be quietly dropped:** the plausible outcome
is that both fail, and the honest report is *"the velocity levers are exhausted
— §20a took the last big one (meanrev 23 -> 100 OOS trades) and what remains is
calendar time."* That is a legitimate result and will be recorded as one.

Running trial count entering §21: ~23 registered comparisons plus grid arms.

### §21 RESULTS — both velocity levers REJECTED

**§21a exit speed — REJECTED on clause (a).** IS winner `hold5`; OOS
+4.357% vs baseline +4.749%. Every arm produced MORE trades and LESS return, and
**every arm had an identical max drawdown (1.069%)**. Faster exits buy ~8 extra
trades and cost ~0.4pp.

Worth recording as a positive result about the method: **§15's "flat surface"
verdict REPLICATED under the ensemble.** Not every solo verdict was an artifact,
and it is useful to know the instrument does not overturn everything it touches.

**§21b universe expansion 38 -> 68 — REJECTED on clause (d).** A fresh 68-name
snapshot (`bars_..._u60.json.gz`, 30 added liquid large-caps + sector ETFs) run
in config order: +2.249% vs +2.086%, PF 1.498 vs 1.457, but **246 trades vs 253
— fewer, not more.** More symbols compete for the same 8 slots, so breadth
dilutes velocity rather than adding it. Clause (d) is the whole point of a
capacity section, so this fails even though return and PF nudged up.

**Honest conclusion, as pre-committed:** the velocity levers are exhausted.
What remains is calendar time.

## §22 — SYMBOL ORDER: §20a REVERTED (2026-07-23)

**A fourth sim/live divergence, and this one was mine, introduced the same day.**

`simulate_ensemble()` iterated `sym_bars` in whatever order the caller supplied
— and a snapshot loaded from disk arrives **alphabetically sorted**. Live builds
its scan list as `scan_symbols = list(cfg["symbols"])` (main.py), i.e. **config
order**. Contention is resolved first-come by symbol, so whoever is scanned
first gets first refusal on the shared slots and the daily fill budget.

Running the identical config in the two orders moved OOS return by **2.66pp**
(+4.749% sorted vs +2.086% config). Every §20 number was measured sorted.

**Consequence: §20a does not hold.** Re-measured in live order:

| | OOS return | PF | maxDD | trades | meanrev trades |
|---|---|---|---|---|---|
| ma_crossover -> tsmom -> meanrev | **+2.669%** | **1.955** | 1.073% | 179 | 25 |
| meanrev first (§20a, shipped) | +2.086% | 1.457 | 1.073% | 253 | 104 |

It FAILS clause (a) (return lower) and FAILS clause (b) badly (PF 1.955 ->
1.457, against an allowance of −0.15). **REVERTED to the gated order.**

The velocity is real — 179 -> 253 trades, meanrev 25 -> 104 — but it is **not
free**, and "free velocity at identical drawdown" was precisely the claim §20a
was adopted on. The owner approved that change on numbers I had measured wrong.
Reverted, and the correct numbers are on the record.

**Fixed:** `simulate_ensemble()` now normalises to config order regardless of
input dict order, mirroring live. Two tests pin it — one proving scan order
changes outcomes when a slot is contested, one proving the ensemble is now
insensitive to the caller's dict ordering.

**A previously undocumented property of the LIVE bot, worth knowing:** because
live scans `cfg["symbols"]` in file order and slots are scarce, **symbols listed
early in config.yaml get systematic first refusal.** SPY/QQQ/DIA/IWM lead the
list, so index ETFs are structurally favoured over names further down. That is
an arbitrary bias nobody chose. It is NOT changed here — reordering the universe
is itself a candidate that must be gated — but it is now on the record.

Running trial count after §22: ~27 registered comparisons plus grid arms.

## §23 — RELATIVE-VOLUME ENTRY FILTER (2026-07-24) — RULES PRE-REGISTERED

**Claim type: EDGE** (declared before running, per METHOD NOTE 5).

**Motivation.** Two independent professional sources (SMB Ep 18; Humbled Trader
— see `knowledge/swing_trading_playbook.md`) both treat **volume expansion** as
core entry confirmation. Checking the repo: **not one of our five strategies
reads the volume field**, although every bar carries it. That is a verified
blind spot, not a deliberate choice.

It is also the right SHAPE of idea. §21 concluded the velocity levers are
exhausted, and the Humbled Trader source independently reports taking 2–5 swing
trades per month against Repete's ~10/month — i.e. chasing more trades was
likely the wrong direction all along. **A volume filter REDUCES trade count by
design and must justify itself on per-trade quality.** That is why it is an EDGE
claim, not a CAPACITY one.

**Mechanism.** `rvol = volume(entry bar) / mean(volume over the prior N bars)`,
N = 20. An entry is blocked unless `rvol >= threshold`. Deterministic, no new
data, no new machinery. Applied to entries only — **exits are never filtered**
(a position must always be able to leave, per the standing ownership rule).

**Arms:** baseline (no filter) / 1.5 / 2.0 / 2.5. Scored with
`simulate_ensemble()` on `data/snapshots/bars_2020-01-01_2026-07-10.json.gz`,
IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) ensemble return >= baseline return,
(b) PF >= 1.30 **AND >= baseline PF + 0.10** — an EDGE claim must *improve* the
    profit factor, not merely hold it; a filter that removes trades while
    leaving quality flat has bought nothing,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed OOS trades (below that the sample cannot support a verdict),
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L **EXCLUDES ZERO
in the candidate's favour**.

Note (b) is deliberately *stricter* than prior sections' `>= baseline - 0.15`.
Those were capacity claims where PF was allowed to soften slightly in exchange
for volume. This claim is the opposite trade, so the bar moves the other way.

**Stated before the run, so it cannot be spun afterwards:** **nothing in this
repo has ever cleared the EDGE bar** — not §14 xsmom (the highest PF here), not
§16, not §20a. The honest prior is that this fails too. It is worth running
because it is cheap, correctly shaped, and closes a verified blind spot — not
because it is likely to work. It will be recorded whichever way it falls.

Running trial count entering §23: ~27 registered comparisons plus grid arms.

### §23 RESULT — REJECTED. The best deterministic numbers in this repo, and still not evidence.

Snapshot `bars_2020-01-01_2026-07-10.json.gz`, ensemble simulator, 70/30 split,
IS-only selection. IS winner among candidates: **rvol1.5**.

| arm | OOS ret | PF | $/trade | trades | maxDD |
|---|---|---|---|---|---|
| baseline (no filter) | +2.669% | 1.955 | $14.91 | 179 | 1.073% |
| **rvol >= 1.5 (IS-selected)** | **+4.067%** | **2.898** | **$31.78** | 128 | **0.643%** |
| rvol >= 2.0 | +1.985% | 2.240 | $21.82 | 91 | 0.425% |
| rvol >= 2.5 | +0.826% | 1.705 | $14.00 | 59 | 0.591% |

**All six deterministic clauses PASS** — and not marginally. Return +52%
relative; PF +0.94 against a required +0.10; **drawdown FELL 40%**; per-trade
P&L **more than doubled**. On the deterministic rules alone this is the
strongest candidate ever run in this repo.

**REJECTED anyway.** Two independent diagnostics say it is noise:

**1. The bootstrap fails even UNCORRECTED.** Bonferroni (K=4) CI
[-$16.50, +$64.72]; uncorrected (K=1) CI [-$10.65, +$52.94]. Both include zero.
**P(candidate better) = 85.8%** — short of even the uncorrected 95% bar. The
pre-registered EDGE rule requires the CI to exclude zero. It does not.

**2. The threshold response is NON-MONOTONE, which is the more damning one.**
PF runs 1.955 → **2.898** → 2.240 → 1.705: it peaks at 1.5 and falls away on
*both* sides, ending *below* baseline at 2.5. If "volume confirms trade quality"
were a real mechanism, requiring *more* confirmation should not reverse it. A
single interior peak with decay either side is the signature of **a fitted
parameter, not a mechanism** — the 1.5 arm is where the noise happened to land.

That second point matters beyond §23. A candidate can pass every deterministic
clause by a wide margin and still be an artifact, and the deterministic clauses
**cannot see it** — only the CI and the shape of the parameter response can.
Where a grid is available, **check monotonicity**: a lone interior optimum is
weak evidence regardless of how good the winning cell looks.

**What ships:** nothing. `min_rvol` stays **unset** in `config.yaml` (a test
pins this), so live behaviour is unchanged. The code — `strategies.base.rvol()`
and `risk.rvol_blocked()` — is **kept as dormant, tested capability**: it closes
the verified blind spot that no strategy read the volume field at all, it is one
shared implementation across live and both simulators (so it cannot become a
fifth sim/live divergence), and it fails open on a missing volume feed. 17 tests.

**Prior confirmed.** §23's registration stated the honest expectation was
failure because nothing here has ever cleared the EDGE bar. That is now
**0 for 4** on EDGE claims (§14 xsmom, §16 chandelier, §20a priority, §23 rvol).
The pattern is consistent and worth stating plainly: **at these sample sizes
(n ≈ 60–260, fat-tailed P&L) this method cannot certify an edge, and every
apparent one so far has failed to survive contact with the interval.**

Running trial count after §23: ~31 registered comparisons plus grid arms.

## §24 — SCAN-ORDER ROTATION (2026-07-24) — ADOPTED as a correctness fix

**Claim type: neither EDGE nor CAPACITY — a CORRECTNESS fix.** Registered as
such before running, because the justification is not "makes more money."

**The defect.** Contention for slots is resolved first-come by scan position,
and live built its scan list as `list(cfg["symbols"])`. So slot allocation —
a consequential trading decision — was determined by **the order symbols were
typed into a YAML file**. SPY/QQQ/DIA/IWM occupy config positions 0–3 of 38.
The live book confirmed the prediction exactly: all five held names came from
config positions 0, 1, 2, 3 and 7.

This was flagged as a known live property in §22 and left alone. It should not
have been: the coming live record is the only clean holdout (§18 — the backtest
cannot certify any edge), and a record that samples ~5 index ETFs does not test
the strategies on the 38-name universe they were gated on.

**The fix.** `risk.scan_order()` / `risk.rotate_scan_order()` — offset the
universe by `date.toordinal()`, advancing one symbol per calendar day.
Deterministic (a date, never randomness, so gate re-runs reproduce), preserves
relative order, adds no fitted parameter, and is **one shared helper called by
live and both simulators** — a per-call-site re-implementation would have been
the fifth sim/live divergence.

**It does what it was built to do:**

| | config order | rotated |
|---|---|---|
| distinct symbols traded | 35 / 38 | **37 / 38** |
| share of trades from config positions 0–7 | **45.3%** | **20.3%** |

**And it costs return on this window — reported as pre-committed:**

| | OOS return | PF | maxDD | trades |
|---|---|---|---|---|
| config order | +2.669% | 1.955 | 1.073% | 179 |
| rotated | +1.829% | 1.526 | 1.174% | 187 |

**ADOPTED ANYWAY, and the evidence supports that rather than excusing it:**

1. **The difference is not significant.** Per-trade $14.91 vs $9.78; 95% CI on
   the difference **[-$19.72, +$9.50]** — includes zero at the *uncorrected*
   level, i.e. the most generous possible reading for "config order is better."
2. **The sign FLIPS in sample.** In-sample, config order +9.098% vs rotated
   **+9.366% — rotation wins.** A genuine advantage should not reverse between
   windows. Losing IS and winning OOS is the signature of noise.
3. **The "before" number is not a legitimate baseline to defend.** Config order
   was never chosen as a trading decision; it is an accident of typing. On this
   particular window it happened to over-weight index ETFs during a strong index
   run (52 of 179 trades vs 20 of 187 after rotation). **Keeping an arbitrary
   ordering because it scores higher on the mined OOS window is precisely the
   selection-on-noise error that forced the §20a revert.**

**What it buys:** a live forward record that samples the whole universe. That
record is the only thing that can ever settle whether these strategies work, and
until now it was going to be a record of five index ETFs.

Running trial count after §24: ~32 registered comparisons plus grid arms.

## PHASE 0 — INTRADAY FEASIBILITY (2026-07-24) — VERDICT: PROCEED, with one large caveat

Owner scoped an intraday project and chose **(A) intraday EXECUTION of the
existing swing strategies** — same signals, same holding horizons, finer
entry/exit timing — with day trading explicitly **out of scope**. Phase 0 was a
cheap kill-check to run *before* building. Findings:

### 1. Data — SOLVED (this was expected to be the blocker)

yfinance caps intraday history at ~2y hourly / 60d 5-minute, which would have
left no in-sample period outside the already-mined OOS window. **Alpaca's basic
tier returns 6 years of both.** Built
`data/snapshots/bars_1Hour_2020-08-01_2026-07-10.json.gz`: **38 symbols,
800,093 bars, 14.7 MB gzipped — committed and hash-manifested**, same window as
the daily snapshot so IS/OOS splits align.

**Recorded reproducibility regression:** unlike `build_snapshot.py` (yfinance,
no credentials), `build_intraday_snapshot.py` **requires Alpaca keys**. A future
session without keys can verify the committed hash but cannot rebuild the file.
That is a real step down from METHOD NOTE 3's standard and is stated, not hidden.

11 of 38 symbols have shorter series (~17.5k vs 23.6k bars) — Alpaca's intraday
coverage for those names starts later. Not fatal, but any intraday gate must
report per-symbol coverage rather than assume a balanced panel.

### 2. Cost — NOT the binding constraint

| | value |
|---|---|
| daily OOS baseline | 187 trades, net $1,828.92 |
| slippage paid @ 5 bps × 2 | $182.55 |
| cost as share of gross edge | **9.1%** |
| **breakeven trade multiplier** | **~11×** |
| round-trip cost vs median trade | 10 bps vs 341 bps = **2.9%** |

Costs only dominate beyond ~11× the trade count. **Phase 1 does not multiply
trade count** — it changes *when* within a day an entry happens, not how many
signals fire. The 11× ceiling is a constraint on day trading (B), which is out
of scope, not on (A).

### 3. Effect size — LARGE, and this is the real finding

Matched all 187 OOS trades to hourly bars:

| | median | mean |
|---|---|---|
| intraday range on the ENTRY DAY | **229 bps** | 287 bps |
| absolute trade outcome (entry→exit) | 341 bps | 510 bps |

**The intraday band is 67% the size of the entire trade outcome.** Entry timing
within the day is not a rounding error on a 13-day median hold — it is a
first-order determinant of the result.

### THE CAVEAT THAT MATTERS MOST

A 67% effect size **cuts both ways, and it is an overfitting hazard, not a
promise.** Nothing here says finer bars give *better* entries; it says entry
timing has a large lever. Tuning that lever on the already-mined OOS window
would be fitting noise with the biggest lever available in this project — a
faster version of the mistake that forced the §20a revert.

Two further honest limits on Phase 1:
- "Time-equivalent parameters" (daily SMA200 → hourly SMA1400) is an
  **approximation, not an identity**. RSI(2) on hourly bars measures intraday
  mean reversion, which is a different phenomenon from the daily signal that was
  gated. The translation must be validated, not assumed.
- Per-symbol coverage is uneven (see above), so an intraday panel is not a
  like-for-like replacement for the daily one.

**Verdict: PROCEED to Phase 1**, because the data exists, costs are not
prohibitive, and the effect is large enough to measure. **Not** because it is
likely to be positive — §18 still stands (no strategy shows an out-of-sample
edge distinguishable from zero), and every velocity/timing change attempted this
session has failed its gate (§19b, §20a reverted; §21, §23 rejected).

Phase 1 must be pre-registered as an **EDGE** claim with the §23 monotonicity
check applied, and reported whichever way it falls.

## §25 — INTRADAY FILL TIMING (2026-07-24) — RULES PRE-REGISTERED

**Claim type: EDGE.** This claims *better fills*, so it must improve per-trade
P&L. Declared before running, per METHOD NOTE 5.

**Design, and why it is not the design Phase 0 originally sketched.** The Phase 0
plan said "re-express daily parameters in hourly-equivalent terms (SMA200d →
SMA1400h)." That was wrong and is abandoned: hourly RSI(14) measures intraday
mean reversion, not the daily RSI(2) phenomenon that was gated, so scaling
periods creates genuinely *different strategies* needing full re-gating — the
whole ~32-trial contamination problem again.

Phase 0 measured that the large lever is **when you fill** (entry-day intraday
range 229 bps median against a 341 bps median trade outcome — **67%**), not what
indicator you compute. So §25 **holds every signal fixed** and varies only the
fill hour. No re-fit, no new parameter search, no new contamination.

**Data basis — non-negotiable, and it nearly went wrong.** Signals AND fills both
come from the **Alpaca hourly snapshot** (`intraday.resample_daily()` rolls it up
to daily bars for the signals). Mixing the yfinance dividend-ADJUSTED daily file
with the Alpaca RAW hourly file would book the adjustment gap — up to **8.8%** on
SPY — as invented P&L on every fill. Consequently **§25 numbers are NOT
comparable with §1–§24**; the baseline is re-established inside this data.

**Regular session only** (bar starts 09:00–15:00 ET). Filling pre/post-market at
regular-session slippage would be fantasy and would bias the result optimistic.

**Arms** (discrete, no continuous knob): baseline = **09:00** (at the open,
today's behaviour) / 10:00 / 11:00 / 12:00 / 13:00 / 14:00 / 15:00 ET. Entries
and exits move together — filling entries at noon while exiting at the open
would be an incoherent experiment. IS-only selection, then OOS.

**PRE-REGISTERED RULE.** Adopt only if the IS-selected arm, out of sample:
(a) return >= baseline return,
(b) PF >= 1.30 **AND >= baseline PF + 0.10**,
(c) maxDD <= baseline maxDD x 1.5 AND <= 3.0pp absolute,
(d) >= 15 closed OOS trades,
(e) **< 10% of fills fell back** to the daily open (`n_fill_fallback`) — thin
    coverage must not masquerade as a result,
**and** the Bonferroni-corrected bootstrap CI on per-trade P&L **excludes zero**
in the candidate's favour.

**Plus the §23 MONOTONICITY CHECK, which matters more here than anywhere.** A
real time-of-day effect should vary *smoothly* across the session. A lone
winning hour with worse neighbours on both sides is a fitted parameter — exactly
how §23 was caught, and with a 67% lever the same trap is bigger here. A
non-monotone winner is reported as an artifact regardless of its numbers.

**Stated before the run:** EDGE claims are **0 for 4** (§14, §16, §20a, §23).
The honest prior is that this fails too. It is worth running because Phase 0
showed the effect is large enough to measure and this design adds no new
contamination — not because it is likely to pay.

Running trial count entering §25: ~33 registered comparisons plus grid arms.

### §25 RUN 1 — VOID. The data had 11 unadjusted stock splits.

The gate ran and returned "deterministic PASS on all 7 clauses, significance
INCONCLUSIVE". **Those numbers are discarded, not reported as a verdict**,
because the underlying data was corrupt.

**What gave it away.** A single stray line in the run log:
`bracket stop would be non-positive (entry 171.64, ATR 159.93)`. An ATR of
159.93 against a $171 price is a ~93% daily true range — impossible for a
mega-cap. Chasing it found the cause.

**The defect.** `build_intraday_snapshot.py` fetched Alpaca bars with the
default `adjustment=RAW`. Alpaca raw prices are **not split-adjusted**, so the
snapshot contained 11 corporate actions the simulator read as overnight
collapses:

| symbol | date | move | split |
|---|---|---|---|
| AAPL | 2020-08-31 | −74.8% | 4:1 |
| AMZN | 2022-06-06 | −94.9% | 20:1 |
| GOOGL | 2022-07-18 | −94.9% | 20:1 |
| NVDA | 2024-06-10 | −90.1% | 10:1 |
| AVGO | 2024-07-15 | −90.0% | 10:1 |
| TSLA | 2020-08-31 | −79.8% | 5:1 |
| …plus NVDA 4:1, TSLA 3:1, WMT 3:1, XLE 2:1, XLK 2:1 | | | |

Every one fired stop-losses on healthy positions, poisoned ATR and every
trend/momentum computation, and distorted the buy-and-hold benchmark. **Any
result computed on it is meaningless.**

**Why it slipped through.** The daily snapshot uses yfinance with
`auto_adjust=True`, so splits have never been an issue in this repo. The
intraday builder switched vendors — and I carried the *dividend* adjustment
concern across (it is documented in `intraday.py`) while missing that Alpaca
also leaves *splits* raw. **Changing data vendors changes more than one
assumption at a time.**

**Fixed two ways.** `adjustment=Adjustment.ALL` on the request, and — more
importantly — a **detector in `sanity_check()` that REFUSES to write a
snapshot** containing any >35% overnight move. A mega-cap does not move 35%
overnight; that is a corporate action, not a price. Nothing but a stray log
line caught this the first time, which is not a control.

**This is the sixth sim-vs-reality defect in this project** (§13 missing rails,
§19a fill timing, §19b global counters, §22 symbol order, §24 config-order
bias, and now §25 unadjusted splits). The standing lesson holds and hardens:
**before trusting any simulator number, ask what the real world does that the
simulation does not.**

§25 is re-run below on the corrected snapshot.

### §25 RUN 2 (adjusted data) — REJECTED. Filling at the open is the best arm.

Snapshot rebuilt with `adjustment=ALL` (sha256 7c6946a2…, 800,093 bars). Signals
and fills both from this one file. IS-only selection picked **10:00 ET**.

| fill hour | OOS return | PF | maxDD | trades |
|---|---|---|---|---|
| **09:00 (baseline, at the open)** | **+2.369%** | **1.712** | 1.339% | 163 |
| 10:00 (IS-selected) | +1.940% | 1.549 | 0.969% | 160 |
| 11:00 | +1.541% | 1.374 | 1.276% | 175 |
| 12:00 | +0.943% | 1.222 | 1.179% | 179 |
| 13:00 | +1.578% | 1.406 | 1.119% | 175 |
| 14:00 | +1.302% | 1.325 | 1.014% | 176 |
| 15:00 | +0.969% | 1.236 | 1.062% | 179 |

Clauses (a) and (b2) **FAIL** — the selected arm returns less than baseline and
its PF is lower, not +0.10 higher. The bootstrap is inconclusive
(diff −$2.41, CI [−$51.11, +$46.67]). **REJECTED; the incumbent stands.**

**The finding: filling at the open is the best of the seven arms**, and PF
declines fairly steadily across the session (1.712 → 1.549 → 1.374 → 1.222 →
1.406 → 1.325 → 1.236). Later fills are worse, not better. The bot's existing
09:35 open cycle (§19a) is already doing the right thing.

### THE POINT OF ALL THIS — the corrupt run said the OPPOSITE

Run 1, on split-corrupted data, reported the baseline as **the worst** arm
(PF 1.267 vs 1.427 at 10:00) and every deterministic clause **PASSING**. Run 2,
on correct data, reports the baseline as **the best** arm with two clauses
**FAILING**.

**The corruption did not merely add noise — it inverted the conclusion.** Had
run 1 been reported, the recommendation would have been "fill later in the day,"
which the corrected data says is precisely wrong. The only thing that surfaced
it was one implausible number in a log line (`ATR 159.93` on a $171 price).

Two lessons, recorded because they generalise beyond §25:
1. **An implausible intermediate value is worth more than a plausible final
   answer.** Run 1's headline numbers looked entirely reasonable.
2. **Changing data vendors changes more than one assumption at a time.** The
   dividend-adjustment difference was caught and documented; the split-
   adjustment difference, in the same switch, was not. A guard now refuses any
   snapshot containing a >45% session-over-session move
   (`build_intraday_snapshot.sanity_check`), verified to fire on an injected
   10:1 split and stay silent on the clean file.

**Phase 1 verdict: intraday fill timing does not improve results.** Combined
with Phase 0 (costs fine, effect size large), the honest conclusion is that the
large intraday lever exists but the bot cannot exploit it by shifting fill
hours — and shifting them later actively hurts. **EDGE claims are now 0 for 5.**

## §26 — TWO LIVE DIVERGENCES FOUND BY AUDITING PRODUCTION (2026-07-25)

Not a gate. An audit of the **running** bot against this repo, prompted by the
owner asking for a full rating. It found two things no backtest could have
surfaced, because both live entirely on the live side of the sim/live boundary.

### DIVERGENCE #7 — the repo and production had silently diverged

The bot placing orders was at commit `5b03654` (Phase D, 2026-07-22). This repo
was at `f5dfdf5`. **57 commits, 86 files, 8,151 insertions apart.** Nothing from
§14 onward was running: not the rebuilt snapshot, not `significance.py`, not the
edge report, not `simulate_ensemble`, not §20–§25, not scan rotation.

Config drift was small but load-bearing:

| key | running | HEAD | gated by |
|---|---|---|---|
| `risk.max_open_positions` | 5 | 8 | §13 |
| `risk.risk_sizing.risk_pct` | 0.1 | 5.0 | §11 |
| `risk.scan_rotation` | absent | true | §24 |
| `strategies.*.max_open_positions` | absent | 5/8/5 | §13 |

The cost, from the live ledger's block reasons: **68 entries refused for "max
open positions reached (5)"** — a ceiling §13 had raised to 8 two days earlier —
and slot allocation still decided by YAML ordering, the exact bias §24 exists to
remove. Live output was 7 executed entries in 10 days, 6 of them on one day,
then a full book that stopped turning over. **1 closed trade.**

Worse than the drift: the running checkout lived inside a **Claude
session-outputs scratch directory**, with the launchd jobs pointing at it. A
routine cleanup of that folder would have destroyed the only live track record
in the project — the one asset here that cannot be reconstructed.

**The generalisable lesson, and it is new in kind.** The first six divergences
were all *simulator vs reality*. This one is *repository vs reality*: every gate
in this document was correctly run, correctly recorded, and correctly merged —
and none of it was running. **A gate verdict is not in production until something
checks that it is.** Passing a gate and shipping a gate are different events, and
until now only the first was ever verified.

### DIVERGENCE #8 — the share-granularity floor (a live-only selection bias)

11 live entries were refused with `computed quantity is zero (account too small
for caps)`. All 11 were tsmom. The cause is arithmetic:

`size_order()` gives tsmom a notional of `equity x risk_per_trade_pct` =
**$999** on ~$100k. Shares are whole. So `qty = int(999 // price)`, and:

| symbol | price | qty @ full | qty @ 0.5 downsize | status |
|---|---|---|---|---|
| LLY | $1,182 | **0** | 0 | **can never be bought** |
| GS | $1,076 | **0** | 0 | **can never be bought** |
| CAT | $894 | 1 | **0** | dies on any downsize |
| TMO | $573 | 1 | **0** | dies on any downsize |
| AMD | $536 | 1 | **0** | dies on any downsize |

Two distinct defects sit inside that table.

1. **Two of 38 names are structurally unreachable.** GS was **APPROVED at full
   size** (`verdict: approve, scale: 1.0`) and still produced qty 0. No judge
   verdict, no rail, and no market condition was involved — the position size is
   simply smaller than one share.
2. **"Downsize" silently means "veto" for high-priced names.** A routine
   `scale: 0.5` on AMD at $536 halves $999 to $500 and rounds to zero shares.
   The judge is permitted to shrink a position (invariant 2); vetoing by
   arithmetic accident is a different act, and it is the one that happened.

**Correction, recorded rather than quietly dropped.** A first draft of this
section claimed the ledger files these as a generic risk rejection so the
judge's calibration scoreboard never sees them. **That is true of the RUNNING
bot (`5b03654`) and false of HEAD.** `main.py:588-610` already separates the two
causes — "sizing budget is below one share" vs "LLM downsize xN truncated an
N-share order to 0" — ledgers each with its own reason, and logs a
`rails_reject` judgment so calibration does see it. The reporting half of this
defect was fixed before the audit; reconciling production to HEAD delivers that
fix. **The trades are still lost either way** — better labelling does not buy
LLY at $1,182. What follows concerns the loss, not the label.

### SECOND CORRECTION (2026-07-25, before §27 was built on it)

The paragraph that stood here claimed:

> `simulate_ensemble()` sizes in continuous dollars and has no LLM downsize
> step. It happily takes a fractional position in LLY at $1,182. Every OOS
> number in this document therefore includes trades the live bot is incapable
> of placing.

**Half of that is wrong, and it is the half the argument rested on.**
`src/backtest.py:705` (and `:374` for the single-strategy path) call
**`risk.size_order()`** — the same function live uses, ending in
`int(dollars // price)`. Whole-share rounding is modelled in the backtest
*exactly* as it is live. LLY and GS produce qty 0 in the simulator too. The
backtest is **not** counting trades the live bot cannot place; on that half,
sim and live agree.

Found while designing §27 on top of this section, which is the only reason it
was found at all. A gate built on a false premise is precisely the failure this
document exists to prevent, so the error is corrected in place rather than
quietly dropped — the same treatment §22 gave §20a.

**What IS a genuine live-only divergence, and it is larger.** The backtest never
applies the judge's verdict: there is no `review["scale"]` anywhere in
`backtest.py`. And from the live ledger, the judge **downsizes 53% of all buys**
(77 of 146; scales `0.4:5, 0.5:49, 0.6:15, 0.7:6, 0.8:2`, median 0.5). So any
name whose full-size order is exactly **1 share** is taken in the simulator and
deleted live on roughly half of all decisions.

Measured against the frozen snapshot's last closes at ~$100k equity:

| | names | which |
|---|---|---|
| qty 0 at full size — **sim and live agree** | 2 | LLY $1,189 · GS $1,055 |
| qty 1 in sim, **0 live on any downsize** | 7 | **SPY · QQQ · DIA** · META · COST · CAT · AMD |
| unaffected | 29 | |

**24% of the universe, on 53% of decisions** — and the affected set is not
obscure names, it is the three index ETFs and four mega-caps. The exclusion is
still correlated with price, so it still strips the highest-priced names from a
momentum book: AMD was refused three times while showing +82% 63-bar momentum.

So the original conclusion survives — backtest trade counts overstate what live
can achieve — but the mechanism is the **absent judge**, not fractional sizing,
and the effect is bigger than first reported, not smaller.

This is the same species of defect as §24 (allocation decided by something that
is not the strategy) and it was invisible for the same reason: nothing compared
what the simulator *could* trade against what the broker *would* accept.

**Not fixed here — deliberately.** Every remaining candidate fix touches a gated
risk parameter and must be pre-registered like anything else:

- **fractional/notional orders — RULED OUT, not deferred.** Every entry goes
  through `broker.bracket_market_order()`, and Alpaca does not accept fractional
  quantities on `OrderClass.BRACKET` / `OTO`. Buying these names fractionally
  would mean submitting them **without protective stop legs**, trading a safety
  guarantee for reach. That is the wrong side of the trade and the option is
  closed, not pending a gate.
- raising the **position budget** (a gated risk parameter — needs a gate);
- flooring qty at 1 on an approved entry (changes true risk per trade — needs a
  gate, and would silently exceed the intended risk slice on a $1,182 share).

The observability fix is already in HEAD (see the correction above), so nothing
in this section ships without a gate. Registering one is the natural §27, and it
should be registered as a **CAPACITY claim** under METHOD NOTE 5 — the argument
is "the book can reach names it currently cannot", not "each trade gets better".
Treating it as an EDGE claim would misfire exactly as §19b did.

**Standing consequence.** Until this is resolved, every backtest number in this
document is measured on a universe the live bot cannot fully trade. That is not
a reason to discard them; it is a reason to expect live trade counts to run
below backtest trade counts, and to check that gap rather than assume it away.

## §27 — POSITION BUDGET (2026-07-25) — RULES PRE-REGISTERED

**Claim type: CAPACITY** (METHOD NOTE 5), declared before running. The claim is
*"the book reaches names it currently cannot"*, NOT *"each trade earns more."*
Typing this as EDGE would demand `ci_low > 0` — rejecting the candidate for
failing a claim it never made, which is the §19b misfire exactly.

`src/significance.py` now carries both tests in code (`.significant` for EDGE,
`.not_worse` for CAPACITY) so the rule stops being restated slightly differently
in each section.

### Motivation

§26's second correction: 2 of 38 names are unbuyable at any scale (LLY $1,189,
GS $1,055) and 7 more — **SPY, QQQ, DIA**, META, COST, CAT, AMD — are taken in
the simulator at qty 1 and deleted live whenever the judge downsizes, which is
**53% of all buys**. 24% of the universe, on half of all decisions.

### Why an arm moves TWO knobs

The effective budget is set by two coupled parameters, each binding a different
strategy:

| knob | binds | value | effective budget |
|---|---|---|---|
| `risk.risk_per_trade_pct` | tsmom, ma_crossover (notional path) | 1.0 | $999 |
| `risk.max_order_value_usd` | meanrev (clamps `risk_sizing`) | 2000 | $2,000 |

§11 already recorded that risk_pct 1.0/2.0/5.0 are byte-identical for meanrev
**because this cap clamps them**. So moving one knob tests one strategy. An arm
moves both in lockstep, which keeps this a single-parameter grid rather than a
2-D search — and `tests/test_gate_budget.py` pins that the cap can never clamp a
candidate arm's notional budget, so no arm can silently collapse to baseline.

| arm | `risk_per_trade_pct` | `max_order_value_usd` | budget @ $100k |
|---|---|---|---|
| baseline | 1.0 | 2,000 | $999 / $2,000 |
| b2.0 | 2.0 | 2,000 | $2,000 |
| c2.5 | 2.5 | 2,500 | $2,500 |
| d3.0 | 3.0 | 3,000 | $3,000 |
| e4.0 | 4.0 | 4,000 | $4,000 |

### The tension that makes this a real experiment

`max_portfolio_heat_pct: 4.0` caps total open stop-risk. Bigger positions burn
heat faster, so the book may hold **fewer** positions. Reach trades against
depth, `pure_checks` already models heat, and the ensemble simulator can see it.
This is not a free parameter to crank — it is a genuine trade-off, and the arm
grid extends to 4.0% specifically so the point where depth loss overwhelms
reach gain is inside the tested range rather than beyond it.

### Selection

IS-only, and **selected on REACH first, return as tiebreak**. Selecting on
return would quietly convert a capacity gate back into an edge hunt.

### PRE-REGISTERED RULE

Adopt the IS-selected arm only if, out of sample:

- **(a)** return ≥ baseline − 0.25pp
- **(b1)** PF ≥ 1.30 · **(b2)** PF ≥ baseline − 0.10
- **(c1)** maxDD ≤ baseline × 1.5 · **(c2)** maxDD ≤ 3.0pp absolute
- **(d)** ≥ 15 closed OOS trades
- **(e)** **distinct symbols traded strictly increases** — the actual capacity
  claim, demonstrated rather than assumed
- **(f)** Bonferroni-corrected bootstrap on per-trade P&L has **`ci_high > 0`**
  (not significantly worse)

plus the **§23 monotonicity check**. That check was itself rewritten during this
build: the first draft tested only unimodality, which **accepts a lone spike**
like `[1.0, 1.1, 9.0, 1.2, 1.0]` — precisely the fitted-parameter shape §23 was
caught by. A decorative guard is worse than none because it reads as a control.
It now also requires the winner's lead over its better neighbour to sit within
the spread of the remaining arms, with the scale taken from the data so the
guard has no tunable of its own.

### STATED BEFORE THE RUN — this gate under-measures the fix

`simulate_ensemble` has **no judge**, so the 7 names lost live on a downsize are
invisible to it. The gate can only see the LLY/GS half. **A marginal result here
corresponds to a materially larger live benefit.**

Recorded now, before any numbers exist, so it cannot be reached for afterwards
to argue a failed gate into a pass. It cuts the other way too: if the gate FAILS
on drawdown or PF, that failure is measured on the smaller half and the real
cost would be larger, not smaller.

**Separate sensitivity diagnostic, not part of the rule and not adopted on:**
re-run each arm applying the empirically observed judge-scale distribution
(`0.4:5, 0.5:49, 0.6:15, 0.7:6, 0.8:2, approve 1.0:67`) as a multiplier, to
estimate the invisible half. It introduces randomness and the judge is not
random, so it informs and never decides.

**Prior:** EDGE claims are 0 for 5. CAPACITY claims have a better record because
the bar is lower and honest about being lower — but §21b (universe 38→68) was a
capacity idea that failed on trade count, and the heat cap makes the same
outcome plausible here.

Runner: `scripts/gate_budget.py` — **committed**, unlike §25's ad-hoc heredoc,
so this gate can be re-executed exactly. Running trial count entering §27: ~38
registered comparisons plus grid arms.

### §27 RESULT — REJECTED. The budget is a LEVERAGE knob, not a reach knob.

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 6abb20b5…, verified before
the run). Ensemble simulator. IS winner on reach-then-return: `e4.0`.

| arm | OOS return | PF | maxDD | trades | **symbols** | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+1.83%** | **1.526** | **1.17%** | 187 | **37** | 5.38% |
| b2.0 | +3.43% | 1.501 | 2.49% | 183 | 37 | 10.98% |
| c2.5 | +4.37% | 1.501 | 3.10% | 183 | 37 | 13.87% |
| d3.0 | +5.25% | 1.498 | 3.77% | 183 | 37 | 16.81% |
| e4.0 [SELECTED] | +6.89% | 1.482 | 5.06% | 183 | 37 | 22.40% |

Clauses **(c1) FAIL** (maxDD 5.06% vs the 1.76% allowed by baseline × 1.5),
**(c2) FAIL** (5.06% > 3.0pp absolute), **(e) FAIL** (reach unchanged).
Significance passes the capacity bar (`ci_high > 0`, INCONCLUSIVE) and
monotonicity passes. **REJECTED; the incumbent stands.**

**What the numbers actually say.** Return scales ×3.8 from baseline to e4.0 and
drawdown scales ×4.3 — drawdown grows *faster* than return, and PF drifts
slightly DOWN (1.526 → 1.482). That is the signature of pure position-size
leverage, not of reaching better opportunities. The parameter buys return by
taking proportionally more risk, which is a decision about risk appetite and
emphatically not an edge.

Trade count is flat across every candidate arm (183) and reach is flat at 37 of
38 (only PG goes untraded, at every budget). The heat cap **never bound once**
(`heat-blk 0` in all ten runs), so the reach-versus-depth tension predicted at
registration simply did not materialise — worth recording, because the arm grid
was extended to 4.0% specifically to find that point and there isn't one.

### THE MORE IMPORTANT FINDING — clause (e) was UNSATISFIABLE by construction

Reach is **37 at every arm, in every run**, out of sample and in. No budget
value could ever have increased it. The clause registered as *"the actual
capacity claim"* could not have passed regardless of the parameter, so it tested
nothing.

That is the **second decorative control caught in a single session** — the §23
monotonicity check had the same disease hours earlier, passing any unimodal
series including the lone spike it existed to catch. Both looked like controls
and neither could fire.

**Binding on future sections:** a pre-registered clause must be shown to be
SATISFIABLE before the section is registered — run the extreme arm and confirm
the clause *can* flip. A clause that cannot fire is not a conservative test, it
is a decoration that makes a weak gate look strict.

### THIRD CORRECTION TO §26 — "LLY and GS can never be bought" is too broad

The gate traded **LLY 2 times and GS 5 times out of sample**. The claim in §26
was extrapolated from *today's* closes and the *notional* budget alone, and it
does not survive contact with the run:

- their OOS closes ranged **$621–$1,236 (LLY)** and **$441–$1,106 (GS)** — for
  much of the window they sat comfortably *below* the $999 notional budget;
- meanrev sizes through `risk_sizing`, clamped at `max_order_value_usd`
  **$2,000**, not $999 — so the high-priced names are reachable through that
  path at any price under $2,000.

What survives unchanged is the **observed live fact**: 11 real entries were
refused with `computed quantity is zero`, all of them tsmom, and one of those
(GS) had been **approved at full size**. The ledger is not in doubt. What was
wrong was generalising those eleven records into "these names can never be
bought" — they cannot be bought *by tsmom, at today's prices*, which is a much
narrower statement and the one that should have been written.

Three corrections to one section is not a good look, and it is recorded in full
rather than tidied: §26 was written from a live-ledger audit without re-deriving
its claims against the simulator, and every one of the three errors came from
that same shortcut.

### Where this leaves the live defect

The gate has **ruled out raising the budget** — on drawdown, decisively, at
every arm including the mildest. It cannot rule the *live* problem in or out,
because `simulate_ensemble` has no judge and the downsize-to-zero interaction is
invisible to it. §27 answered the question it could answer and the answer is no.

The remaining candidate is the **1-share floor** (a downsize may shrink a
position but never delete it). It is deliberately NOT adopted here: it is not
gateable — the arms would be byte-identical in a simulator with no judge, the
§19b misfire — so it needs an explicit owner decision recorded as such, in the
manner of §20a, rather than a gate verdict it cannot earn.

**Standing note for the next registration.** Clause (c1) — maxDD ≤ baseline ×
1.5 — is near-unpassable whenever baseline drawdown is very small (1.17% here),
because any increase in deployment breaches it. That is arguably correct for a
risk rail and **it does not change this verdict**; it is recorded now, after the
result, precisely so it cannot be quietly relaxed in a later section without the
change being visible.

### §26 ADDENDUM — the drift guard had the mirror-image blind spot (2026-07-25)

`src/deploycheck.py` shipped at ~15:00 to catch divergence #7. At ~15:25,
checking whether the launchd re-point had taken effect, it was pointed at the
production checkout and reported **clean**. It was not clean:

```
branch:      audit/production-drift-2026-07-25
HEAD:        045ca67   (4 commits AHEAD of origin/main)
PR #21:      OPEN — unreviewed
deploycheck: behind 0, config clean -> NO ALERT
```

`behind_upstream()` asks `HEAD..origin/main` — *what does main have that I
lack*. Nothing did. It had no way to ask the opposite. Divergence #7 was
production **behind** main; this was production **ahead** of it, running code no
review had passed, and the module written specifically to catch "the running
code is not the reviewed code" could not see it.

**Fixed** with `ahead_of_upstream()` (`origin/main..HEAD`), reported as its own
clause. One count covers both shapes: a feature branch, and `main` carrying
unpushed local commits — the second being the more dangerous, because the branch
name looks right. Checking the branch NAME instead would have missed it.
Injection-proven against the real state above rather than a synthetic one; the
repository was itself the failing case.

### THE PATTERN — three decorative controls in one session

| control | looked like | actually |
|---|---|---|
| §23 monotonicity check | rejects fitted parameters | passed `[1.0, 1.1, 9.0, 1.2, 1.0]` — the exact lone spike it existed to catch |
| §27 clause (e) | "the actual capacity claim" | unsatisfiable by construction; reach was 37/38 at every arm, so it could never fire |
| `deploycheck` | catches production drift | reported clean on this repo's own drifted production checkout |

Each was written in good faith, read as rigour, and could not do its job. Two
were caught only because something forced them against a real case; the third
because its own repository happened to be broken in the way it was built to
detect.

**Binding rule, now with three data points:**

> **A control must be demonstrated FIRING on the real failure it was built for.**
> Not on a hypothetical, not on a unit-test fixture that was written from the
> same mental model as the code. The §25 split detector met this bar — it was
> run against an injected 10:1 split before being trusted. The three above did
> not, and all three were hollow.

Corollary for pre-registration: before a clause is registered, run the extreme
arm and confirm the clause *can* flip. A clause that cannot fire does not make a
gate conservative; it makes a weak gate look strict, which is worse than an
absent one because it stops anybody looking further.

## §28 — ONE-SHARE FLOOR: NOT REGISTERED. The probe said the gate could not work.

**No gate was written for this.** That is the finding.

### Why it was going to be registered

§26's live defect: GS at $1,098 against a $999 notional budget was refused with
`computed quantity is zero`, while the judge had **APPROVED it at full size**
(`verdict: approve, scale: 1.0`). No verdict and no rail rejected that trade —
whole-share arithmetic did. Owner decision 2026-07-25: fix that case, honour
every downsize.

The design needed almost no code. The floor belongs in **`risk.size_order()`**,
the one function the simulator shares with live (`backtest.py:705`), so no new
sim/live gap is created — and the judge's authority then falls out of the
existing `int(full_qty * review["scale"])` in `main.py` with no special case:
approve keeps the floored share, any downsize truncates 1 back to 0.

I had told the owner this could not be gated. **That was wrong**, and the error
is worth naming: I was reasoning about the *downsize* case, which lives in
`main.py` where the simulator has no judge. The case actually chosen —
"approved at full size, still bought nothing" — is `size_order()` returning 0,
and `size_order()` is shared. Measurable after all.

### The satisfiability probe — the new rule's first save

Per the rule written after §27 (*a clause must be shown able to fire before its
section is registered*), the floor was implemented behind `risk.min_one_share`
and the arms were diffed **before** any rules were drafted.

| | trades | symbols | return |
|---|---|---|---|
| IS baseline | 486 | 38 | +9.366% |
| IS floored | 486 | 38 | +9.366% |
| **IS delta** | **0** | **0** | **0.000pp — byte-identical** |
| OOS baseline | 187 | 37 | +1.829% |
| OOS floored | 183 | 37 | +1.784% |
| **OOS delta** | **−4** | **0** | **−0.045pp** |

**Three independent reasons not to register:**

1. **Reach does not increase — clause (e) fails, exactly as in §27.** +0 symbols
   in-sample and out. LLY, GS, COST, CAT are *already* traded by the baseline,
   because meanrev sizes through the `max_order_value_usd` $2,000 clamp and
   because their prices sat under $999 for much of the window. The capacity
   claim — "reaches names it currently cannot" — is simply **false**.
2. **In-sample is byte-identical**, so IS-only selection would have had nothing
   to select on. A gate whose selection step is a coin flip is not a gate.
3. **Out of sample it is mildly negative**: fewer trades, lower return.

### The mechanism, which is the genuinely interesting part

The floor is **not additive — it is a perturbation.** The OOS trade-level diff
is **29 trades in, 33 out**, and most of the churn is in names that were never
budget-constrained at all: SPY, NVDA, GOOGL, XOM, JNJ, BAC. One forced share of
LLY on 2026-01-08 consumes a slot, heat and cash, which shifts every subsequent
entry decision down the line. The book reshuffles.

And the sharpest number: **the floored-in trades themselves earned +$448.71**,
while total P&L fell from +$1,828.92 to +$1,783.81 — **−$45.11**. The new trades
made money; the trades they displaced would have made more. Pure opportunity
cost. "This trade would have been profitable" is not the same claim as "taking
it makes the book better," and only the ensemble simulator can tell them apart.

(The magnitude — 0.045pp against a 1.83pp baseline — is inside the noise band
every other section has measured. The reason not to adopt is clause (e) being
false, not this number.)

### What this does to §26 divergence #8

It reframes it, and downward. "The bot refuses names whose single share exceeds
its position budget" reads like a bug and is closer to **a rail doing its job**:
a name you cannot size properly is a name your account is too small to trade,
and forcing the position spends a slot that a correctly-sized one would use
better. The defect is real and still ugly — an approved trade killed by
rounding — but **fixing it is not worth anything measurable**, and on this
snapshot it costs slightly more than it returns.

What stands unchanged: HEAD already *reports* the two causes distinctly and logs
a `rails_reject` judgment, so the judge's calibration sees it. Visibility was
the part worth having.

### Disposition

`risk.min_one_share` ships **false**, with the code path retained and tested —
the same treatment as `vol_target`, `regime_exposure` and `donchian`. Enabling
it would be adopting an unmeasured change, and the measurement that exists says
don't.

**Incidental fix found by the probe's tests:** `size_order()` raised
`ZeroDivisionError` on a non-positive price, which would have taken a whole
cycle down rather than skipping one bad symbol. It now returns 0 — fail-safe.
The bug predates §28 and had never been exercised because upstream freshness and
cross-vendor guards had always caught bad bars first.

### For the record: the §27 rule has now paid for itself twice

§27 clause (e) was unsatisfiable and nobody noticed until after the gate ran.
§28's clause (e) was unsatisfiable in exactly the same way, and the probe caught
it **before a single rule was written**. Checking satisfiability first cost one
script and about two minutes.

---

## §29 — UNCAP THE BOOK AND RAISE SIZING (owner decision, 2026-07-26)

**PRE-REGISTERED BEFORE THE SIZING SWEEP RAN.** The uncapped-count baseline
(arm B below) had already been measured when this was written and its numbers
appear here; the `risk_per_trade_pct` sweep — the arm that actually decides the
question — had not. Thresholds in "Decision rule" are fixed from this point.

### Why this is not §27 again

§27 raised `risk_per_trade_pct` 1.0 → 4.0 with `max_order_value_usd` 2000 → 4000
and was **REJECTED** on maxDD 5.06% against a 3.0pp ceiling. Two things have
changed, and neither is "we decided we liked the answer better":

1. **The ceiling moved, by owner decision, and is recorded as such.** The owner
   set a **10.0pp maxDD tolerance** on 2026-07-26. §27's 5.06% would clear it.
   This is a change of risk appetite, not a re-analysis — stated plainly so
   nobody later reads §29 as having refuted §27's arithmetic. It did not.
2. **The simulator §27 ran on was wrong.** It never applied the judge's
   downsize (divergence #8, live half). Measured this session: the judge cuts
   **53.5% of buys at mean scale 0.752**, so every OOS figure in this document
   before today — §27's included — is roughly **34% optimistic**. Confirmed
   directly: tsmom OOS +0.62% without the model, **+0.41% with it**.

### The finding that reframes the whole document

`max_order_value_usd: 2000` on ~$100k equity was a 2% ceiling on every position,
and it fired **before every other rail**. That single line is why this document
is full of rails that measured as inert:

| section | recorded verdict | actual cause |
|---|---|---|
| §11 | `risk_pct` 1.0 / 2.0 / 5.0 byte-identical | clamp fired first |
| §18 | `regime_exposure` REJECTED, "gross never nears the cap" | clamp fired first |
| §20b | per-strategy slots INERT | clamp fired first |
| heat cap | "four positions total ~$560 against a $4,000 cap" | clamp fired first |

Measured on the frozen snapshot, it held **average deployment at 2.5% of
capital**. The bot was ~97% in cash. That, not signal quality, is the leading
explanation for OOS returns sitting near zero.

### Arms

All on `memory/bars_snapshot_2020_2026-07-10.json` + `earnings_snapshot.json`,
slippage pinned 5 bps, 70/30 walk-forward, tsmom, `--judge-model` ON for every
arm so they are mutually comparable and comparable to live.

- **A — status quo ante**: caps as of 2026-07-25, judge model on.
  Measured: OOS **+0.41%**, 100 trades, PF 1.331, maxDD 0.5%, deployment 1.9%.
- **B — counts uncapped, sizing unchanged** (`max_open_positions: 0`,
  per-strategy 0, `max_trades_per_day: 15`, `max_order_value_usd: 0`).
  Measured: OOS **+1.94%**, 312 trades, PF 1.607, maxDD 0.73%, deployment 5.04%,
  0 heat blocks, 130 correlation blocks.
- **C — the sweep, NOT YET RUN**: arm B plus `risk_per_trade_pct` ∈
  {1, 2, 4, 6, 8}. This is the arm that decides the question, because arm B
  showed the count caps were never the binding constraint on deployment — the
  1%-of-equity sizing *target* is.

### Decision rule — fixed before arm C runs

Adopt the **highest** `risk_per_trade_pct` that satisfies ALL of:

- (a) OOS maxDD **≤ 10.0pp** (owner ceiling, 2026-07-26)
- (b) OOS profit factor **≥ 1.3** (the standing enablement bar, unchanged)
- (c) OOS return **> arm B's +1.94%** — scaling must buy something
- (d) OOS trades **≥ 15** (standing bar)
- (e) profit factor must not fall below **1.3**; if PF degrades monotonically
      with size while return rises, prefer the knee, not the maximum — §12
      measured exactly that shape for tsmom (2.398 → 1.977 → 1.730)

If no arm satisfies (a)-(d), `risk_per_trade_pct` **stays at 1.0** and that is
recorded as the result. Raising it anyway would be adopting a change the
measurement rejected, which is the one thing this document exists to prevent.

### Counter-evidence carried forward, not buried

§12 measured tsmom's PF **degrading** as slots rise (2.398 → 1.977 → 1.730)
while return rises — extra trades bought at a worse rate. Arm B is the first
test of that claim at non-trivial deployment, and **it did not replicate**: PF
went 1.331 → 1.607 as trades went 100 → 312. The honest reading is that §12 was
measured behind the clamp, at 2.5% deployment, and may simply not describe the
uncapped regime. It is not yet refuted — one arm is not a refutation — and it
remains the reason clause (e) exists.

### Known limitations of the judge model, stated up front

It reproduces the judge's *distribution*, not its *decisions*. Nothing can
recover which specific trades a model would have vetoed in 2024. The calibration
rests on **146 decisions over 7 live days in one market regime**, heavily
serially correlated, so the effective sample is far below 146 and the tails are
unmeasured. It is strictly closer to live than modelling no judge at all, which
is the bar it has to clear — not accuracy it does not have.

### §29 RESULT — ADOPTED at `risk_per_trade_pct: 8.0`

Arm C, run 2026-07-26 on the frozen snapshot, judge model on, counts uncapped:

| risk% | OOS return | trades | PF | maxDD | deployment | return/maxDD |
|---|---|---|---|---|---|---|
| 1 | +1.94% | 312 | 1.607 | 0.73% | 5.0% | 2.65 |
| 2 | +4.00% | 322 | 1.567 | 1.55% | 10.6% | 2.58 |
| 4 | +8.30% | 322 | 1.539 | 3.39% | 22.4% | 2.45 |
| 6 | +13.25% | 322 | 1.547 | 5.12% | 34.2% | 2.59 |
| **8** | **+17.45%** | 319 | 1.549 | **7.51%** | 43.6% | 2.32 |

Every arm clears (a) maxDD ≤ 10.0pp, (b) PF ≥ 1.3 and (d) trades ≥ 15. Arms
2-8 clear (c) return > +1.94%. Clause (e)'s knee does not trigger: PF is
1.607 → 1.567 → 1.539 → 1.547 → 1.549, which flattens rather than degrading
monotonically, so §12's slot-quality decay did not reappear as a sizing decay.
The rule selects the highest qualifying arm: **8.0**.

**What this result is NOT.** Return rose 9x and maxDD rose 10.3x; return/maxDD
went 2.65 → 2.32. Risk-adjusted quality got slightly *worse*. This is leverage,
correctly measured, not a better strategy — and it still loses outright to B&H
+36.21%, passing the enablement gate only through the exposure-matched hatch.
Anyone reading "+17.45%" without this paragraph has misread the table.

**The boundary was not found.** 8 was the top of the pre-registered range and it
cleared the ceiling with 2.5pp to spare. 10 might also clear. Extending the
sweep now, having seen the shape, would be shopping — it needs its own gate.

**Regime exposure, enabled on §18's own condition.** §18 rejected
`regime_exposure` as untestable because "gross never nears the cap ... Revisit
only if sizing scales up." At risk 8.0, deployment is 43.6% and it binds:

| arm | OOS return | PF | maxDD | return/maxDD |
|---|---|---|---|---|
| off | +17.45% | 1.549 | 7.51% | 2.32 |
| `down_max_gross_pct: 50` | **+20.25%** | **1.694** | **5.71%** | **3.55** |

Better on all three axes. Unlike the sizing raise this IS a risk-adjusted gain,
and it is the documented momentum-crash result (Kaminski-Lo, Daniel-Moskowitz).
**Adopted at 50.**

### §30 CANDIDATE — tighter down-regime gross cap. NOT ADOPTED.

A 30% arm scored better than 50 on every metric (+22.55%, PF 1.791, maxDD
4.90%, return/maxDD 4.60). It is **not adopted**, because 50 was the value §18
had already configured whereas 30 was chosen after seeing the OOS result.
Testing two values and keeping the winner is in-sample selection performed on
the holdout, and one contaminated pick is how a document like this stops being
trustworthy. A proper gate needs a pre-registered range and a fresh split.

### §31 CANDIDATE — `daily_loss_limit_pct` is now inconsistent with the DD ceiling

Not a backtest question; an operational one, flagged rather than silently
changed. `daily_loss_limit_pct: 3.0` flattens the book and engages HALT on a
single -3% day. At 5% deployment that was nearly unreachable. At **43.6%
deployment** a ~7% adverse move in the held names reaches it, and the owner's
accepted drawdown tolerance is now **10pp** — so the kill switch would fire and
liquidate at roughly a third of the drawdown the owner has said is acceptable.

Left at 3.0 deliberately. Raising a kill switch is a safety decision and it is
the owner's, not something to fold into a sizing gate.

### §31 RESOLVED — drawdown circuit breaker built, daily limit realigned

Owner approved both on 2026-07-26.

**`max_drawdown_pct: 10.0` (new).** Peak-to-trough from an equity high-water
mark. Blocks new **entries** only; exits always run. A rail that stopped you
selling during a drawdown would trap you in the book it exists to protect you
from, at the moment getting out matters most.

Deliberately distinct from `daily_loss_limit_pct`, which flattens and HALTs:

| | measures | acts |
|---|---|---|
| `daily_loss_limit_pct` | one session | **liquidate + HALT** — hard stop |
| `max_drawdown_pct` | peak to trough, any duration | **block entries** — soft brake |

A book that bled 10% over three weeks, never losing 5% in one session, passed
every rail the bot had before today.

**`daily_loss_limit_pct: 3.0 → 5.0`.** At 5% deployment a -3% day was nearly
unreachable; at 43.6% it is not. Against the owner's 10pp tolerance the old
value would have liquidated the book at roughly a THIRD of the accepted
drawdown — converting a survivable correction into a realised loss at the low.
Half the total tolerance is the reasoning: one day consuming half the drawdown
budget is a genuine emergency.

**Enforced in the simulator too.** Live reads a persisted high-water file; the
backtester keeps a running peak. Both call the same `risk.drawdown_pct()`, so
they cannot disagree about what a drawdown is — a live-only rail would have
been divergence #9, created by the work closing #8. Pinned by
`test_the_backtester_enforces_the_same_rail`.

At the adopted settings it does not bind: OOS maxDD is 5.71% against a 10.0
limit. It is a backstop, not an active constraint, which is the correct state
for a circuit breaker.

### Method note: a negative-control harness that lied

Two mutations happened to leave `src/risk.py` **exactly 11 bytes shorter** than
the original. CPython validates `.pyc` files on (mtime, size) at one-second
resolution, so the second mutation silently reused bytecode compiled during the
first — and the harness reported a **broken guard as verified**. The ratchet
test was green while the ratchet was disabled.

Caught because the same mutation went red when run standalone and green inside
the loop. Fixed by purging `src/__pycache__` and setting
`PYTHONDONTWRITEBYTECODE=1` per mutation; all three suites re-run, 18/18 red.

Worth recording because the failure direction is the dangerous one: a false RED
is noisy and self-correcting, a false GREEN is a rail you believe is tested and
is not. Any mutation-testing harness in this repo needs the same treatment.

---

## §31 — CROSS-ASSET RISK GATE (pre-registered 2026-07-27, before any run)

**Claim type: EDGE.** Not capacity, not leverage. The claim is that entries
taken while credit is deteriorating are *worse trades*, and that skipping them
raises per-trade expectancy. If it only shifts exposure it has failed.

### Why this idea, and why now

Prompted by comparing this repo against `TauricResearch/TradingAgents`
(94.7k stars, Apache-2.0). That project has no broker, no backtest engine and no
statistical gating — but it does have something this one does not: **information
breadth.** It reads fundamentals, macro, sentiment and prediction markets.

Every strategy here — `ma_crossover`, `tsmom`, `meanrev`, `xsmom`, `donchian` —
is a price-only rule over 38 of the most heavily arbitraged instruments in
existence. `:611` reports no OOS per-trade edge distinguishable from zero across
all five after Bonferroni correction. **That is the expected result for
price-only rules on SPY/QQQ/AAPL/NVDA, not a surprise.** News exists in the
system but only widens the universe through nominations (`main.py:592`); it has
never entered a signal.

So the one change worth testing is an input the price series does not already
contain.

### Why cross-asset prices rather than FRED

The obvious borrow is FRED macro. It carries a trap: FRED serves **current**
values and most macro series are revised. Backtesting against revised data is
lookahead, and it would quietly invalidate this gate rather than fail it loudly.
ALFRED vintages are the correct fix and are a separate project.

Cross-asset **prices** have no revision problem. They are point-in-time by
construction, they arrive through the same yfinance path as everything else, and
they carry the same orthogonality argument: information not contained in the
traded symbol's own price history.

Channel: **HYG:LQD** — high-yield credit against investment grade. When credit
risk appetite deteriorates, that ratio falls before equity indices do, often
enough to be worth one honest test. `UUP` (dollar) is fetched into the snapshot
for a later question and is **not** part of this gate.

### Data

Separate snapshot `credit_2020-01-01_2026-07-10.json.gz`,
sha256 `9b8ac92c77137a819e62339f50e35b34f1b85e9e2be4f126c7dedd7ccb457621`,
3 symbols (HYG, LQD, UUP), 4914 bars, yfinance `auto_adjust=True`.

Verified before registering: HYG/LQD/UUP each have **1638 bars**, identical to
SPY's 1638 in the price snapshot, and **zero** SPY session dates lack a HYG bar.
No alignment gap to paper over.

**The price snapshot is NOT rebuilt.** `bars_2020-01-01_2026-07-10.json.gz`
(sha256 `6abb20b5…`) stays byte-identical, so §31's arms are measured on exactly
the price data §27 and §29 were. Adding three tickers to it would have changed
its hash and silently orphaned every earlier verdict anchored to it.

### The rule under test

A filter over existing entry signals, in the shape of §23's `rvol_blocked`:
one implementation shared by live and both simulators, **fails open** on missing
data, and is **never applied to exits** — a position must always be able to
leave. Not a sixth strategy; the strategies are unchanged.

### Arms (3 — K deliberately small, it inflates the Bonferroni correction)

| arm | rule |
|---|---|
| **baseline** | no gate (incumbent) |
| **sma50** | block new entries while HYG:LQD < its own 50-bar SMA |
| **sma100** | block new entries while HYG:LQD < its own 100-bar SMA |

### Pass mark — fixed now, before any numbers exist

Selection on **IS only**; OOS reported once. Split 70/30 as in §27.

An arm is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` on OOS in full — return > 0, ≥15 trades,
  PF ≥ 1.3, and beats B&H *or* the risk-adjusted bar *or* the exposure-matched
  benchmark (`backtest.py:969`)
- **(b)** OOS profit factor **strictly greater** than baseline's. An EDGE claim
  that does not raise PF is not an edge claim
- **(c)** OOS max drawdown ≤ baseline's + 1.0pp absolute
- **(d)** ≥ 15 OOS trades **and** ≥ 60% of baseline's trade count. A filter that
  reaches the PF bar by taking 6 trades has not found an edge, it has found a
  smaller sample
- **(e)** Bonferroni-corrected block bootstrap (`significance.compare`, K=3)
  returns better-than-baseline, not INCONCLUSIVE

**(b), (d) and (e) are the ones that matter.** A gate that merely sits out bad
weather will pass (a) and (c) by lowering exposure — that is the capacity
result, and this is not a capacity claim.

### Prior — stated before the run, honestly

**EDGE claims in this repo are 0 for 5.** §23 (relative volume) was the closest
structural analogue and was REJECTED. The base rate for what follows is a
rejection, and the registration exists so that outcome cannot be renegotiated
afterwards.

Two specific ways this fails that are worth naming now:

1. HYG:LQD < SMA is a *slow* signal. It will likely be below its average through
   most of the drawdowns the drawdown breaker already handles, so it may be
   measuring the same thing twice.
2. Credit stress and equity weakness are contemporaneous more often than credit
   leads. If so this removes trades roughly at random with respect to outcome —
   which shows up as flat PF and lower trade count, i.e. (b) and (d) fail
   together.

**Running trial count entering §31:** ~41 registered comparisons plus grid arms
(38 entering §27, plus §27's 5 arms scored as one comparison, plus §29).

**Runner:** `scripts/gate_cross_asset.py` — committed, not a heredoc, so this
gate can be re-executed exactly.

### §31 RESULT — REJECTED. Credit direction does not sort this bot's trades.

Snapshot `bars_2020-01-01_2026-07-10.json.gz` (sha256 `6abb20b5…`) and
`credit_2020-01-01_2026-07-10.json.gz` (sha256 `9b8ac92c…`), both **verified by
the runner before scoring** — it refuses to run on a drifted file. Ensemble
simulator, 38 symbols, 70/30 split, IS ends 2024-07-23, OOS starts 2024-07-24.
The OOS credit slice carries 100 bars of lead-in so `sma100` is warm at its
first OOS bar rather than silently disabled for 100 sessions.

**In-sample** (selection here and only here, on profit factor — the EDGE measure):

| arm | IS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| baseline | +46.47% | 1.662 | 10.84% | 542 | 40.03% |
| **sma50 [IS winner]** | **+71.39%** | **1.784** | 9.95% | 767 | 59.11% |
| sma100 | +39.92% | 1.715 | 10.42% | 412 | 33.60% |

**Out-of-sample** (reported once):

| arm | OOS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| **baseline** | **+14.42%** | **1.296** | 11.11% | 429 | 70.59% |
| sma50 [SELECTED] | +12.42% | 1.266 | 10.19% | 330 | 60.18% |
| sma100 [not selected] | +16.82% | 1.400 | 8.85% | 309 | 57.75% |

Clause **(a) FAIL** — `enablement_gate()` refuses it twice over: OOS PF 1.266 <
1.3, and +12.42% beats neither B&H +49.70%, nor the risk-adjusted bar, nor the
exposure-matched benchmark +29.91% at 60.2% deployment. Clause **(b) FAIL** — PF
1.266 vs baseline 1.296, *lower*, and an EDGE claim that lowers profit factor
is not an edge claim. Clauses (c), (d1), (d2) pass. Clause **(e) FAIL** —
INCONCLUSIVE: +$37.64/trade vs +$33.62/trade, diff +$4.01 with a 98.33%
Bonferroni CI of [-$126.08, +$176.25]. The interval is thirty times wider than
the effect.

**REJECTED. The incumbent stands; `risk.credit_sma_period` ships as 0.**

### The failure mode was the one named in the registration

Registered beforehand: *"if the filter removes trades roughly at random with
respect to outcome, PF stays flat while the count drops — clauses (b) and (d)
fail together, and that pattern is a REJECT, not a near miss."*

That is what happened. The filter removed **99 of 429 OOS trades (23%)** and
profit factor moved from 1.296 to 1.266 — down, not up. Removing a quarter of
the trades changed per-trade expectancy by less than the noise in a single
trade. Credit direction is not sorting good entries from bad ones; it is
sampling them.

### The part worth reading twice

**`sma100` — the arm that was NOT selected — has the best OOS row in the table.**
Higher return than baseline, PF 1.400 against 1.296, and the lowest drawdown of
the three.

Selection happened in-sample, where `sma100` placed *third of three* on profit
factor (1.715 vs `sma50`'s 1.784). So the pre-registered procedure picked
`sma50`, and `sma50` lost out of sample.

This is precisely the situation pre-registration exists for. With the OOS table
in front of me I can construct a completely convincing story for `sma100`: a
slower credit average is less twitchy, it avoids whipsaw around the moving
average, of course 100 beats 50. That story would be written *after* seeing the
number it explains, which makes it worth nothing. Two candidate arms and a coin
flip produce a winner half the time.

**`sma100` is not adopted, and this result may not be used as evidence for it.**
Its OOS number is now burned: it has been looked at, so it can never again serve
as an out-of-sample test of that arm. Testing `sma100` honestly requires a fresh
pre-registration on a split this run did not touch — the same treatment §30 is
waiting on. Recorded here so that a later session cannot find the number,
reasonably conclude it looks promising, and quietly enable it.

**EDGE claims are now 0 for 6.**

### What this does and does not rule out

It does **not** show that cross-asset information is useless. It shows that
*this* measure (HYG:LQD versus its own SMA), used *this* way (a binary
all-symbols entry block), does not improve *these* strategies on *this*
snapshot. The registration named the likely reason in advance: credit stress and
equity weakness are largely contemporaneous, so a slow credit average mostly
re-detects drawdowns the circuit breaker already handles.

What is genuinely learned: a market-wide on/off switch is a blunt instrument.
Every strategy sees the same boolean on the same day, so the filter can only
move exposure in time — it cannot rank one symbol against another. The
cross-sectional version of this idea (rank symbols by sensitivity to credit,
rather than gate the whole book) is a different claim, needs its own
registration, and is not tested here.

The rail itself stays in the codebase, switched off, with its tests
(`tests/test_credit_gate.py`, 10 boundary-paired cases proving it cannot read
past the decision bar and fails open on every missing-data shape). A rejected
arm keeps its implementation so the next registration does not have to rebuild
and re-verify it — but it stays at 0, and a strategy change is never adopted by
enabling it and seeing what happens.

---

## §32 — WIDE-UNIVERSE CROSS-SECTIONAL FACTORS (pre-registered 2026-07-27, before the runner existed)

**Claim type: EDGE.** Per-trade expectancy, not exposure, not reach.

### Why, and why this is different from §14–§31

Every gate before this one moved a *rule* over the same 38 mega-caps. All five
strategies are price-only, and `:611` reports no OOS per-trade edge
distinguishable from zero across all of them. **That is the expected result for
those instruments, not bad luck** — SPY, AAPL and NVDA are the most heavily
arbitraged prices in existence.

`src/strategies/xsmom.py` has been a correct Jegadeesh–Titman 12-1
implementation since it was written, and has never been enabled. It could not
have worked: ranking needs dispersion, and the top 25% of 38 mega-caps is nine
names that move together. §21b's 38→68 expansion failed on trade count because
68 is still tiny.

§32 changes the **universe**, not the rules. That is the one variable no
previous section has moved.

### Data

`bars_wide_2020-01-01_2026-07-10.json.gz`, sha256
`825116a6d66e4d991a1928c3a5b9aed653966f52547e91b6b4afec78e24413a7`,
**500 symbols, 818,787 bars.** Built from current S&P 1500 membership
(1,504 fetched → 1,388 with a real 2020 → top 500 by median 2020 dollar volume).
Window matches the existing snapshot, so §32 stays comparable with §14–§31.

Dispersion this buys, measured before the run: trailing-1y returns run
p10 **−27.1%**, median **+10.1%**, p90 **+65.0%**. A 92-point spread to rank
across, where 38 mega-caps had almost none.

**SPY is force-included.** `simulate_ensemble` reads it for the regime label and
vol bucket (`backtest.py:640`) and `risk.regime_exposure` is enabled — a
universe without SPY silently stops that rail binding. The first build omitted
it; caught before it scored anything. It is rankable like any other symbol
(1 of 500).

#### Survivorship bias — named now, with its direction

Today's index membership backtested from 2020 omits everything that delisted or
was removed. This **inflates** results, and momentum exploits the deletion more
than buy-and-hold does.

Two things reduce it, neither eliminates it:

1. **S&P 1500, not S&P 500.** Names that fell from large-cap into mid/small are
   still present, recovering part of the loser distribution a large-cap-only
   list would silently delete.
2. **The comparison largely cancels it.** `backtest.py` computes
   `buy_hold_return_pct` from the *same* `sym_bars`, so both sides of the gate
   see the same survivor set.

Consequence, stated before the numbers exist: **a REJECTION here is trustworthy;
a PASS is provisional** and must be confirmed on a split this run never touched
before anything is enabled.

### Shared risk block — identical across ALL arms

Only the STRATEGY differs between arms. If the baseline kept `risk_per_trade_pct:
8.0` while candidates ran at 2.0, this would measure a sizing change wearing a
factor's clothes.

| key | §32 value | shipped | why |
|---|---|---|---|
| `risk_per_trade_pct` | **2.0** | 8.0 | 50 names × 8% is 400% of equity; cash runs out at ~12 positions |
| `max_position_pct` | **2.5** | 10.0 | equal-weight ceiling for a ~50-name book |
| `max_portfolio_heat_pct` | **20.0** | 4.0 | at 4.0 every arm is capped by heat rather than by its signal, and all six return the same answer |
| `max_trades_per_day` | **50** | 15 | at 15 the book takes four days to build, unequally across arms |
| `min_holding_days` | 2 | 2 | **unchanged — invariant 3** |
| `max_drawdown_pct` | 10.0 | 10.0 | unchanged |

**This means §32 measures SIGNAL QUALITY, not deployability at the shipped
rails.** An arm that passes here has not been shown to work at the current risk
posture, and Phase 4 must re-run any winner at shippable settings. Recorded now
so a pass cannot later be read as "ready to enable".

### Arms — the family is fixed HERE, K = 6

Fixing all six now is what stops this becoming "run momentum, fail, quietly try
low-vol", which is sequential testing with no correction.

| arm | strategy config |
|---|---|
| **baseline** | shipped ensemble (ma_crossover, tsmom, meanrev) on the wide universe |
| **xsmom-12-1-10** | xsmom only; `rank_lookback_bars 231`, `skip_bars 21`, `buy_top_fraction 0.10`, `exit_below_fraction 0.5` |
| **xsmom-12-1-20** | as above, `buy_top_fraction 0.20` |
| **xsmom-6-1-10** | xsmom only; `rank_lookback_bars 126`, `skip_bars 21`, `buy_top_fraction 0.10` |
| **lowvol-60-10** | lowvol only; `vol_period 60`, `buy_bottom_fraction 0.10`, `exit_above_fraction 0.5` |
| **both-10** | xsmom-12-1-10 **and** lowvol-60-10 enabled together, priority xsmom→lowvol |

`both-10` is an ensemble arm rather than a new blended-rank module on purpose: it
needs no new code, and it is how the live bot would actually run two
cross-sectional strategies.

### Pass mark — fixed before any number exists

Selection on **IS profit factor** (the EDGE measure). Selecting on total return
would let an arm win by holding more, which is a capacity result and not the
claim. OOS reported **once**. Split 70/30, as §27 and §31.

An arm is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` on OOS in full (`backtest.py:969`)
- **(b)** OOS profit factor **strictly greater** than baseline's
- **(c)** OOS max drawdown ≤ baseline's + 1.0pp absolute
- **(d)** ≥ **30** OOS trades (raised from §31's 15 — a 500-name universe has no
  excuse for a thin sample, and 30 is the same bar `review.py:24` sets for going
  live)
- **(e)** Bonferroni-corrected block bootstrap (`significance.compare`, **K=6**)
  returns **significantly better**, not INCONCLUSIVE

**(b) and (e) are the ones that matter.** A wide universe alone will raise
absolute return simply by having more to buy; that is capacity, and it is not
what is being claimed.

### Prior — before the run

**EDGE claims in this repo are 0 for 6.** Nothing about a wider universe changes
that base rate on its own.

Three ways this fails, named now so a near miss cannot be talked into a pass:

1. **Survivorship carries it.** Momentum looks strong precisely because the
   universe is the set of names that survived to 2026. Shows up as a large
   IS/OOS gap.
2. **Costs eat it.** Cross-sectional rebalancing turns over far more than the
   incumbent. At 5bps slippage a 300-trade OOS arm pays real money; PF falls
   toward 1.0 while gross return looks fine.
3. **It is beta, not alpha.** Buying the top decile of a 500-name universe in a
   bull sample is a leveraged long. Caught by `enablement_gate()`'s B&H and
   exposure-matched clauses, which is why (a) is non-negotiable.

**Running trial count entering §32:** ~44 registered comparisons plus grid arms
(41 entering §31, plus §31's three arms scored as one comparison).

**Runner:** `scripts/gate_wide_universe.py` — committed, hash-verifies the
snapshot before scoring, refuses to run on drift.

### §32 RESULT — REJECTED. And the run found something worse than a failed arm.

Snapshot `bars_wide_2020-01-01_2026-07-10.json.gz` (sha256 `825116a6…`),
hash-verified by the runner before scoring. 500 symbols, 70/30 split, ensemble
simulator, shared risk block. Total runtime 13m35s.

**In-sample** (selection here and only here, on profit factor):

| arm | IS return | PF | maxDD | trades |
|---|---|---|---|---|
| baseline | +62.16% | 1.493 | — | 2477 |
| xsmom-12-1-10 | +0.17% | 1.007 | — | 256 |
| xsmom-12-1-20 | −9.16% | 0.631 | — | 288 |
| xsmom-6-1-10 | +35.83% | 1.950 | — | 503 |
| **lowvol-60-10 [IS winner]** | **+79.15%** | **4.644** | — | 541 |
| both-10 | +74.73% | 3.115 | — | 707 |

**Out-of-sample** (reported once). OOS buy-and-hold on this universe: **+29.03%**.

| arm | OOS return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|
| baseline | **−2.55%** | 0.864 | 10.77% | 358 | 23.5% |
| xsmom-12-1-10 [not selected] | +63.17% | 5.061 | 11.62% | 269 | 47.6% |
| xsmom-12-1-20 [not selected] | +32.05% | 3.094 | 8.36% | 370 | 47.1% |
| xsmom-6-1-10 [not selected] | +38.43% | 2.273 | 12.56% | 461 | 60.0% |
| **lowvol-60-10 [SELECTED]** | **+6.71%** | 1.347 | 7.93% | 490 | 83.2% |
| both-10 [not selected] | +5.55% | 1.262 | 7.72% | 564 | 83.5% |

Clause **(a) FAIL** — `enablement_gate()`: +6.71% beats neither B&H +29.03%, nor
the risk-adjusted bar, nor the exposure-matched benchmark +24.16% at 83.2%
deployment. Clauses (b), (c), (d) pass. Clause **(e) FAIL** — INCONCLUSIVE:
+$13.69/trade vs −$7.13/trade, diff +$20.82, 99.17% Bonferroni CI
[−$42.57, +$98.70].

**REJECTED. Nothing is enabled. EDGE claims are now 0 for 7.**

---

### The finding that matters more than the verdict

**In-sample selection did not predict out-of-sample performance on this data.**

| arm | IS PF | OOS PF | IS rank | OOS rank |
|---|---|---|---|---|
| lowvol-60-10 | 4.644 | 1.347 | **1** | 4 |
| both-10 | 3.115 | 1.262 | 2 | 5 |
| xsmom-6-1-10 | 1.950 | 2.273 | 3 | 3 |
| baseline | 1.493 | 0.864 | 4 | 6 |
| xsmom-12-1-10 | 1.007 | 5.061 | 5 | **1** |
| xsmom-12-1-20 | 0.631 | 3.094 | **6** | 2 |

Spearman rank correlation IS→OOS: **−0.543 on profit factor, −0.600 on total
return.** The in-sample best finished 4th; the in-sample **worst** finished 2nd.

**Stated with the caution it deserves: n = 6 arms.** A Spearman of −0.543 at n=6
is nowhere near significant (|ρ| ≈ 0.83 would be needed at p=0.05), so this is
**not** evidence that selection actively anti-predicts. What it is evidence of:
the strong positive relationship the entire walk-forward procedure assumes is
**not present here**, and cannot be assumed in §14–§31 either.

**The likely cause is that this split is not a sample, it is a regime cut.**
IS covers 2020-01→2024-07: the COVID crash, the 2021 melt-up, the 2022 bear.
OOS covers 2024-07→2026-07. Those are not two draws from one distribution, they
are two different market environments. "Out-of-sample" has been functioning as
"out-of-regime" — which is a harder and more honest test, but it also means a
single 70/30 split cannot rank strategies reliably in either direction.

This applies **retroactively to every gate from §14 onward.** It does not
invalidate the rejections — a rejection under a noisy selector is still a
failure to demonstrate an edge, and the bar was never cleared. It does mean
that if any section had produced a PASS, that pass would have rested on a
selection step with no demonstrated predictive validity.

**Consequence: the next experiment is a METHOD claim, not another factor.**
Hunting more arms with a selector that does not rank is how a false positive
eventually gets adopted. §33 tests the selection procedure itself across
multiple folds.

---

### The trap, larger than in §31

`xsmom-12-1-10` returned **+63.17% OOS with a profit factor of 5.061**, against
buy-and-hold's +29.03% — more than double the benchmark, at a comparable
drawdown. It is the single best OOS row this repository has ever produced.

It was ranked **fifth of six in-sample** (PF 1.007) and was not selected.

Everything about the finding above says this number should be treated as
approximately uninformative. The selection metric did not rank; the arm that
looks spectacular here is the one the procedure judged nearly worst four years
earlier; and the OOS window is a single strong bull regime in which a
concentrated top-decile momentum book is a leveraged long.

**`xsmom-12-1-10` is NOT adopted, and its OOS number is now burned** — it has
been observed, so it can never again serve as an out-of-sample test of that arm.
Recorded here in full, and prominently, precisely because it is the most
tempting number in the repo and the easiest one for a later session to find,
rationalise and enable.

---

### Two further findings worth keeping

**The incumbent ensemble does not generalise.** The baseline went **−2.55% OOS
with PF 0.864** on 500 names, having been comfortably positive on 38. Whatever
`ma_crossover`/`tsmom`/`meanrev` are doing, it does not survive contact with a
wide universe. That is a fact about the shipped strategies, measured here for
the first time.

**A wide universe produces positions with no stop.** The run logged repeated
`bracket stop would be non-positive (entry 185.96, ATR 101.74) — falling back to
plain market order`. Investigated: these are **real** market data, not
corruption — CZR, NCLH, CCL, APA and OKE during the March 2020 crash, and CAR in
April 2026, at ATR/price ratios of 0.36–0.62. On 38 mega-caps this essentially
never happened. On 500 names it does, and the fallback means those positions run
**without stop-loss protection**. Independent of any gate verdict, the wide
universe is not shippable until that path is handled deliberately.

### What §32 does and does not rule out

It does not show that cross-sectional factors are useless — it shows that a
single 70/30 split on 6.5 years cannot tell which of six candidates is better,
in either direction. Both statements about momentum and low-volatility here are
unresolved rather than answered.

`lowvol` stays in the registry, **disabled**. `xsmom` stays **disabled**. A
rejected arm keeps its implementation so the next registration need not rebuild
it; it does not get enabled to see what happens.

---

## §33 — DOES WALK-FORWARD SELECTION PREDICT? (pre-registered 2026-07-27, before the runner existed)

**Claim type: METHOD.** A new type. §14–§32 tested *strategies* using a fixed
procedure; §33 tests *the procedure*. Nothing in this section can enable or
disable a strategy.

### Why this comes before any further factor hunting

§32 measured a Spearman rank correlation of **−0.543** between in-sample and
out-of-sample profit factor across six arms. The in-sample best finished fourth;
the in-sample worst finished second. At n=6 that is not significant in either
direction — which is exactly the problem. **The procedure has never been shown
to rank at all.**

Every gate from §14 onward selects on one in-sample window and reports one
out-of-sample number. If that selection step carries no information, then:

- every REJECTION remains sound (the bar was not cleared, whatever the selector
  did), but
- any PASS would rest on a coin flip dressed as a method, and
- continuing to hunt arms is simply buying more chances for a false positive.

Running another EDGE claim before answering this would be negligent. §32's own
`xsmom-12-1-10` row — +63.17% OOS at PF 5.061, ranked fifth of six in-sample —
is what that false positive would look like when it arrives.

### The hypothesis under test

> Selecting the in-sample best arm produces better out-of-sample performance
> than picking an arm at random from the same family.

That is the assumption every previous section has relied on without checking.

### Design — expanding-origin folds

The same six §32 arms (the family is already fixed and already burned, so no new
multiple-comparison budget is spent on *strategies* here; the trials counted are
folds, not arms).

Five expanding-origin folds on the wide snapshot. Expanding rather than rolling
because it matches deployment: the bot always has all history up to today.

| fold | IS bars | OOS bars |
|---|---|---|
| 1 | 0–600 | 600–800 |
| 2 | 0–800 | 800–1000 |
| 3 | 0–1000 | 1000–1200 |
| 4 | 0–1200 | 1200–1400 |
| 5 | 0–1400 | 1400–1638 |

Every fold runs all six arms IS and OOS: 60 simulations.

### Metrics — fixed now

1. **Per-fold Spearman** between IS and OOS profit-factor rank across the six
   arms, and the mean across folds.
2. **Selection lift**: OOS profit factor of the IS-selected arm minus the *mean*
   OOS profit factor of all candidate arms in that fold. Positive means
   selecting beat picking at random.
3. **Hit rate**: folds in which the IS-selected arm placed in the top half OOS.

### Pass mark — the procedure is VALIDATED only if all hold

- **(a)** mean per-fold Spearman **> 0**
- **(b)** mean selection lift **> 0**
- **(c)** hit rate **≥ 4 of 5** folds

Deliberately a low bar. This is not asking the procedure to be good; it is
asking it to be better than a coin, which is the minimum for any of the last
nineteen sections to mean what they say.

### Prior — before the run

**Expected to FAIL.** §32's point estimate was negative, the incumbent ensemble
does not generalise across universes, and the folds span violently different
regimes (COVID crash, 2021 melt-up, 2022 bear, 2023–26 recovery). A procedure
that ranks strategies across regime boundaries would be a genuinely surprising
result.

### The remedy, committed BEFORE seeing the outcome

So it cannot be invented to fit whatever comes back:

**If §33 FAILS**, single-split selection is retired. Its replacement — to be
pre-registered as §34 and tested on its own terms — is **fold-majority
selection**: an arm is selectable only if it ranks in the top half in **≥60% of
folds**, and the number reported is the pooled out-of-fold result rather than
one window's. Any future EDGE claim runs under that method.

**If §33 PASSES**, the existing verdicts stand as they are, and §14–§32's
rejections keep exactly the weight they already have.

Either way **no strategy is enabled by this section.**

**Running trial count entering §33:** ~50 registered comparisons plus grid arms.
§33 adds 5 folds as one METHOD comparison.

**Runner:** `scripts/gate_fold_stability.py` — committed.

### §33 RUN 1 — **INVALID.** The verdict was VALIDATED and it was an artifact.

Recorded in full rather than deleted. A discarded run is an unrecorded degree of
freedom, and this one printed the most convenient possible answer.

**What it reported:**

```
[PASS] (a) mean spearman > 0        [+0.691]
[PASS] (b) mean selection lift > 0  [+0.474]
[PASS] (c) hit rate >= 4/5          [5/5]
VERDICT: the walk-forward selection procedure is VALIDATED on this data.
```

All three clauses, 5/5 folds. It would have licensed continuing to hunt
strategies with the existing method.

**Why it is invalid.** The OOS windows were 200 bars, sliced as `[is_end, stop)`
with no history. `xsmom-12-1` requires **253 bars** of lookback:

| fold | xsmom-12-1-10 | xsmom-12-1-20 | xsmom-6-1-10 | lowvol-60-10 | both-10 |
|---|---|---|---|---|---|
| 1 | **0** | **0** | 148 | 237 | 237 |
| 2 | **0** | **0** | 103 | 202 | 202 |
| 3 | **0** | **0** | 156 | 194 | 194 |
| 4 | **0** | **0** | 108 | 155 | 155 |

*OOS trade counts.* Both 12-1 momentum arms placed **zero trades in every
fold** — they could not signal at all. Their profit factor was 0.000 by
construction.

**The mechanism that manufactured the result:** those two arms also ranked last
*in-sample* (PF 0.99 and 0.63, on their own merits). So two of six arms sat at
the bottom of **both** rankings for entirely unrelated reasons — genuinely weak
in-sample, structurally mute out-of-sample — and that alone forces a large
positive rank correlation. Meanwhile `both-10` returned byte-identical numbers
to `lowvol-60-10` in every fold, because its momentum half contributed nothing,
so it was not an independent arm either.

**Three of five candidate arms were not data points.** A rank correlation over
six arms where three are degenerate measures the fold design, not the selector.

**Whose error this is.** Mine, in the fold design. §31 handled the identical
lead-in problem correctly for the credit series — *"the OOS slice keeps `period`
bars of lead-in before the first OOS price bar, so arm sma100 is not silently
disabled for its first 100 sessions"* — and the same reasoning was not applied
here three hours later.

**How it was caught.** Not by the pass mark, which was satisfied. By reading the
per-arm OOS trade counts in the trials log while the run was still going. A
profit factor of exactly 0.000 repeated across folds is not a weak arm, it is an
arm that never traded.

**The correction.** Each arm's OOS slice now carries lead-in equal to **that
arm's own** `strategies.max_lookback_bars(cfg)` — 253 bars for the 12-1 arms,
148 for 6-1, 100 for low-vol — so every arm's first possible signal lands exactly
on the fold boundary. No contamination: a strategy cannot signal before it has
its lookback, so no trade can open inside the lead-in.

The runner now **raises** on any arm with zero OOS trades rather than scoring it.
A zero silently ranks last; a raise cannot be ignored.

**Nothing from Run 1 is carried forward.** Its numbers are not quoted anywhere as
evidence, and the +0.691 Spearman in particular must never be cited: it measures
a broken slice.

### §33b RESULT — **NOT VALIDATED.** In-sample selection does not predict.

Corrected run, per-arm lead-in, snapshot hash-verified. Five expanding-origin
folds, six arms, 60 simulations.

| fold | IS→OOS Spearman | selection lift | selected arm's OOS rank | |
|---|---|---|---|---|
| 1 | −0.943 | −0.163 | 4/5 | MISS |
| 2 | −0.657 | −0.636 | 5/5 | MISS |
| 3 | +0.371 | **+4.900** | 1/5 | HIT |
| 4 | +0.829 | +0.400 | 2/5 | HIT |
| 5 | −0.486 | −1.226 | 4/5 | MISS |

```
[FAIL] (a) mean spearman > 0        [-0.177]
[PASS] (b) mean selection lift > 0  [+0.655]
[FAIL] (c) hit rate >= 4/5          [2/5]
```

**Clause (b) is not evidence and is not cited as such.** It clears only on fold
3's +4.900 outlier. The **median lift is −0.163** and three of five folds are
negative; drop fold 3 and the mean is −0.406. A criterion carried by one
observation out of five is a criterion that has not been met in any meaningful
sense, and it was included in the registration precisely so it could not be
quietly leaned on now.

**The procedure is NOT VALIDATED.**

### What the result actually says

Not "selection is backwards". The per-fold Spearman spans **−0.943 to +0.829**
— from near-perfect inverse ranking to near-perfect correct ranking, across
adjacent windows of the same data. The mean of −0.177 is indistinguishable from
zero, and with that spread it is not a meaningful summary of anything.

The honest statement is: **selecting the best in-sample arm carries no reliable
information about which arm performs out of sample.** Sometimes it is right,
sometimes it is exactly wrong, and nothing in-sample tells you which fold you
are in.

That is a more useful finding than either tidy alternative. "Anti-predictive"
would have been exploitable — invert the selector. "Predictive" would have
validated §14–§32. Neither is true.

### Consequences for §14–§32

**Every rejection stands.** A rejection is a failure to clear a numeric bar, and
the bar was not cleared regardless of what the selector did. §31, §32 and the
five before them are unaffected.

**No pass from that procedure would have been trustworthy.** This is the load-
bearing point. Seven EDGE claims have been rejected; had one been accepted, it
would have rested on a selection step now shown to carry no information. §32's
`xsmom-12-1-10` — +63.17% OOS at PF 5.061, ranked fifth of six in-sample — is
exactly what that false positive looks like, and it is now doubly disqualified.

The 0-for-7 record was never the problem. **The instrument was.**

### The registered remedy, triggered

Single-split selection is **RETIRED**. Its replacement, committed to before this
run and now in force for every future EDGE claim:

**Fold-majority selection** — an arm is selectable only if it ranks in the top
half in **≥60% of folds**, and the number reported is the **pooled out-of-fold**
result rather than one window's. Pre-registered separately as §34 and tested on
its own terms before anything runs under it.

### Honest limits of §33b itself

Five folds, six arms, one snapshot, 6.5 years. This is not a proof that
walk-forward selection can never work here — it is a demonstration that it has
not been shown to work, on the only data available, using the exact procedure
nineteen prior sections relied on. A method that has never been validated is not
the same as a method proven useless, and §34 has to clear its own bar rather
than inherit trust from this rejection.

**No strategy was enabled or disabled by §33. `lowvol` and `xsmom` remain off.**

---

## §34 — IS FOLD-MAJORITY SELECTION ANY BETTER? (pre-registered 2026-07-27, before the runner existed)

**Claim type: METHOD.** §33b retired single-split selection. This tests whether
the registered replacement is actually an improvement, rather than assuming it
because it sounds more careful.

### The trap this registration exists to avoid

Fold-majority selection picks the arm that ranks top-half in ≥60% of folds. The
obvious way to evaluate it is leave-one-fold-out: hold out fold *k*, select using
the other four, score on *k*.

**That is lookahead.** For fold 3, "the other four" includes folds 4 and 5 — the
future. A deployment standing at fold 3 has folds 1–2 and nothing else. An
evaluation that quietly uses later folds measures an oracle, not a method, and
it would flatter the replacement exactly when it matters most.

So §34 scores the **causal** variant: to select for fold *k*, only folds
**1…k−1** may be used. Fold 1 has no history and is not scored. That leaves
**four evaluations** — which is a small number, said now rather than discovered
later.

The non-causal variant is computed too, reported as a **diagnostic upper bound**
only. It decides nothing. If the causal and oracle numbers diverge sharply, that
gap is itself the finding.

### Three selectors, same held-out folds

| | rule |
|---|---|
| **S1 single-split** | argmax candidate IS profit factor in fold *k*. The retired method §33b rejected. |
| **S2 fold-majority (causal)** | among candidates, those ranking top-half in ≥60% of folds 1…k−1; tie-break on mean OOS PF over those folds. |
| **S3 random** | expected value = mean candidate OOS PF in fold *k*. The bar any selector must clear to be worth having. |

Scored on the held-out fold's realised **OOS profit factor**.

### Pass mark — fixed before computing anything

Fold-majority replaces single-split only if **all** hold:

- **(a)** mean held-out OOS PF **> S3 random**
- **(b)** hit rate (selected arm in the top half of candidates) **≥ 3 of 4**
- **(c)** it beats **S1 single-split** on both (a) and (b)

Clause (c) matters most. A replacement that is merely a different way to be
wrong is not a replacement.

### Prior — before the run

**No strong expectation, and that is the honest position.** §33b showed
single-split carries no information; it does not follow that fold-majority
carries any. Aggregating across folds helps only if arm quality is stable across
regimes, and §32/§33b are precisely the evidence that it may not be.

Most likely outcome: **all three selectors indistinguishable at n=4**, which
would mean the repository has no validated way to choose between strategies at
all — a harder finding than either alternative, and one that would have to be
stated plainly rather than worked around.

### What §34 cannot do

Four evaluations, six arms, one snapshot, and the **same measurements that
produced §33b**. This is not a fresh sample. A pass here is a licence to test
fold-majority on new data, **not** permission to trust it or to enable anything
under it. Written now so a pass cannot be read as more than it is.

**Running trial count entering §34:** ~51 registered comparisons plus grid arms.
No new simulations — §34 re-reads the trials log §33b already wrote.

**Runner:** `scripts/gate_selector_compare.py` — committed.

### §34 RESULT — **DOES NOT REPLACE.** No selector here beats chance.

Four held-out folds (fold 1 unscorable — no history). No simulations; computed
from the trials log §33b wrote.

| selector | mean held-out OOS PF | hit rate |
|---|---|---|
| **S3 random** (the bar) | **2.590** | — |
| S1 single-split *(retired)* | 3.449 | 2/4 |
| **S2 fold-majority (causal)** | **2.604** | **2/4** |
| *oracle variant — diagnostic only* | *2.819* | *3/4* |

```
[PASS] (a) fold-majority mean PF > random
[FAIL] (b) fold-majority hit rate >= 3/4
[FAIL] (c) beats single-split on BOTH
```

**Fold-majority does not replace single-split.** Clause (a) clears by 0.014 —
2.604 against 2.590 — which is a rounding artefact, not an improvement.

### The diagnostic that settles the interpretation

The **oracle** variant selects using every fold except the held-out one,
including folds in the future. It cheats, deliberately, to establish an upper
bound on what any amount of cross-fold history could buy.

It scored **2.819** — barely above random's 2.590, and *below* the retired
single-split method's 3.449.

**Even with lookahead, aggregating across folds cannot pick a winner.** That
rules out the comfortable explanation. Fold-majority is not underperforming
because it lacks history; it underperforms because **arm quality is not stable
across folds at all.** The instability is in the data, not in the estimator, and
no smarter estimator over these measurements will fix it.

### Single-split's higher mean is not a reprieve

S1 scored 3.449, the best of the three — and it stays retired. Its hit rate is
**2/4**, identical to fold-majority and to a coin. The mean is carried entirely
by fold 3, where `lowvol-60-10` returned PF 9.121; strip that fold and the
ordering collapses. §33b rejected it on five folds of evidence and one outlier
in four does not overturn that.

### The honest state of this repository

**There is no validated way to choose between strategies here.** Not
single-split, not fold-majority, not an oracle with the future in hand. Three
selectors, all indistinguishable from picking at random on this data.

That is the most important sentence written in this file today, and it is
harder than either alternative would have been.

### What follows from it — the constructive part

If selection cannot be trusted, then **stop selecting.** Every future EDGE claim
must be built so its verdict does not depend on a selector:

1. **Pre-specify a single arm** before the run. No family, no choosing, no
   multiple-comparison budget spent on selection. One hypothesis, one number.
   This is weaker per run and honest per run.
2. **Or require the whole family to move.** If an effect is real it should lift
   every arm in the family, not one. A result that appears in one arm and not
   its neighbours is what selection noise looks like.
3. **Or require an effect large enough to survive any selection** — a margin so
   wide that which arm you picked cannot change the verdict.

Every EDGE claim from here carries the stated limitation: *selection in this
repository is unvalidated, so this result rests on a pre-specified hypothesis
rather than a chosen one.*

### Limits of §34 itself

Four evaluations, six arms, one snapshot, and the same measurements that
produced §33b. It cannot prove fold-majority is useless in general — only that
it showed no advantage here, on the data available, against the method it was
registered to replace. As pre-registered: a pass would have been a licence to
test on fresh data. A failure is not proof of the negative either.

**Nothing enabled. `lowvol` and `xsmom` remain off. EDGE claims stand at 0 for 7.**

---

## §35 — ONE HYPOTHESIS, NO SELECTION (pre-registered 2026-07-27, before the runner existed)

**Claim type: EDGE.** The first claim in this repository built under the rule
§34 imposed: **the verdict must not depend on a selector.**

### Why this is shaped differently from §14–§32

Every prior EDGE claim ran a family of arms, picked the in-sample winner, and
reported its out-of-sample number. §33b and §34 showed that picking step carries
no information — single-split, fold-majority and even an oracle holding the
future are all indistinguishable from random on this data.

So §35 does not choose. **One hypothesis, fixed now, tested once.** If it fails
there is no second arm to fall back on, and that is the point: a result that
cannot be produced by choosing cannot be an artifact of choosing.

### The hypothesis, and where it comes from

> Cross-sectional 12-1 momentum earns a positive per-trade edge on a wide,
> liquid US equity universe, after costs, relative to the incumbent ensemble.

Buy the top decile by trailing 12-month return skipping the most recent month;
exit when a holding falls below the median. Parameters are the **literature
convention** — Jegadeesh & Titman (1993), 231-bar lookback with a 21-bar skip —
**not** values tuned on any data in this repository.

That provenance matters. I have seen §32's numbers, so I cannot claim a
hypothesis chosen today is untainted by them. What I can claim, and what is
checkable, is that these parameters were published in 1993 and are the standard
specification, so they were not reverse-engineered from this repo's results.
`xsmom-12-1-10` in §32 used the identical parameters for the same reason.

### Data — a period this repository has never touched

`bars_wide_2014-01-01_2019-12-31.json.gz`, sha256
`dca8dc85b8770f74697262750b767e796635f5b481c54efb475b12d9687916cd`,
**500 symbols, 754,888 bars**, liquidity-screened on **2014** only.

Verified before registering: **zero date overlap** with
`bars_wide_2020-01-01_2026-07-10.json.gz`. 385 of 500 symbols are shared; the
dates are disjoint, which is what matters. SPY force-included for the regime
rail.

**No IS/OOS split.** There is nothing to select, so there is nothing to split
for. The entire 2014–2019 period is out-of-sample with respect to every number
this repository has ever produced.

#### Survivorship bias is WORSE here — stated plainly

The universe is today's S&P 1500 membership. Over 2014–2019 that is **twelve
years** of survivorship, against six for the 2020 snapshot. Every company that
existed in 2014 and failed before 2026 is absent, and momentum exploits that
absence more than buy-and-hold does.

Consequence, fixed before the numbers exist: **a REJECTION here is trustworthy,
because the bias runs in the candidate's favour. A PASS is provisional and
cannot be adopted on this evidence alone** — it would require a
survivorship-free universe to confirm, which this repository does not have and
cannot build from free data.

### Arms — two, and neither is chosen

| | |
|---|---|
| **baseline** | shipped ensemble (ma_crossover, tsmom, meanrev), same universe, same risk block |
| **xsmom-12-1** | xsmom only; `rank_lookback_bars 231`, `skip_bars 21`, `buy_top_fraction 0.10`, `exit_below_fraction 0.5` |

Shared risk block identical to §32's (`risk_per_trade_pct 2.0`,
`max_position_pct 2.5`, `max_portfolio_heat_pct 20.0`, `max_trades_per_day 50`),
so this measures signal, not sizing. `min_holding_days 2` and `1Day` unchanged.

### Pass mark — fixed now

The hypothesis is ADOPTED only if **all** hold:

- **(a)** clears `enablement_gate()` in full (`backtest.py:969`)
- **(b)** profit factor **strictly greater** than baseline's
- **(c)** max drawdown ≤ baseline + 1.0pp
- **(d)** ≥ 30 trades
- **(e)** block bootstrap `significance.compare` returns **significantly
  better** at **K = 8**

**On K = 8.** Only one comparison is made here, so K = 1 would be the
conventional choice. It is set to 8 deliberately: this is the **eighth** EDGE
claim this repository has run, and family-wise error accumulates across a
research programme even when the hypotheses differ. After seven failures, an
eighth result at conventional significance is weak evidence, and the correction
should say so rather than the prose having to.

### Prior — before the run

**EDGE claims are 0 for 7.** Momentum specifically has been tested twice on the
2020–2026 universe and rejected both times.

The one honest reason to expect anything different: 2014–2019 contains no
COVID crash and no 2022 bear, and cross-sectional momentum is documented to fail
hardest at sharp reversals. A calmer sample is where it should look best. **That
is also the reason to distrust a pass** — "it works in the easy period" is not
an edge, and clause (a)'s buy-and-hold and exposure-matched comparisons are what
stop that argument.

### What a pass would and would not mean

It would mean one pre-specified hypothesis survived one clean period at a
conservative correction. It would **not** mean the strategy is enabled, that
alpha is demonstrated, or that the go-live gate (30 closed trades / 60 days,
`review.py:24`) is closer. Those need live evidence that no backtest produces.

**Running trial count entering §35:** ~52 registered comparisons plus grid arms.

**Runner:** `scripts/gate_s35_momentum.py` — committed.

### §35 RESULT — **REJECTED**, and it kills the most tempting number in this file.

Snapshot `bars_wide_2014-01-01_2019-12-31.json.gz` (sha256 `dca8dc85…`),
hash-verified before scoring. 500 symbols, whole period, no split, no selection.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+64.36%** | **1.431** | 11.12% | 4,524 | 500 | 69.6% |
| **xsmom-12-1** | **−4.04%** | **0.758** | 18.49% | 248 | 141 | 21.1% |
| buy-and-hold | +90.41% | — | 21.1% | — | — | — |

```
[FAIL] (a) clears enablement_gate() in full
[FAIL] (b) PF strictly > baseline
[FAIL] (c) maxDD <= baseline + 1.0pp
[PASS] (d) trades >= 30
       INCONCLUSIVE: -$16.28/trade vs +$14.23/trade, diff -$30.51,
       99.38% CI [-$91.39, +$104.13]  (Bonferroni K=8)
[FAIL] (e) significantly better
```

**The hypothesis lost money.** −4.04% over six years, in a period where
buy-and-hold returned **+90.41%** and the incumbent ensemble returned +64.36%.
Profit factor 0.758 — it lost more on its losers than it made on its winners.
Every clause fails except the trade count.

**EDGE claims are now 0 for 8.**

### Why this rejection is worth more than the seven before it

It is the first claim in this repository whose verdict **cannot** be an artifact
of selection, because there was no selection. One hypothesis, its parameters
published by Jegadeesh & Titman in 1993, fixed in a commit before the runner
existed, tested once on a period with **zero date overlap** with anything this
repo had touched, corrected at K=8 for the whole research programme.

Every earlier rejection was measured with an instrument §33b later showed to be
uninformative. This one was not.

### What it does to §32's `xsmom-12-1-10`

§32 produced the single most seductive row in this file: **+63.17%
out-of-sample at profit factor 5.061**, better than double buy-and-hold. It was
flagged there as untrustworthy on procedural grounds — it ranked fifth of six
in-sample, and §33b showed the ranking carried no information. That was an
argument about method, not about the strategy.

§35 settles it with evidence. **The identical parameters — 231-bar lookback,
21-bar skip, top decile — lost 4% on a clean, untouched six-year period while
the market rose 90%.**

A rule that returns +63% in one window and −4% in another is not an edge that
comes and goes. It is noise, and §32's number was a draw from it. The temptation
is now closed by measurement rather than by caution, which is a much stronger
place to leave it.

### The prior said this was momentum's best case

Registered before the run: 2014–2019 contains no COVID crash and no 2022 bear,
and cross-sectional momentum is documented to fail hardest at sharp reversals,
so a calm sample is where it should look **best**.

It lost money anyway, on a universe carrying **twelve years of survivorship
bias running in its favour**. That combination — the friendliest period, the
friendliest universe, the literature's own parameters — is why this rejection
should be read as strong.

### What is NOT concluded

That cross-sectional momentum does not exist. It is one of the most replicated
anomalies in finance and this is one implementation, on one universe, inside one
bot's risk rails, at 5bps costs, holding a top decile with a median exit. Any of
those choices could be what failed.

What **is** concluded: **this bot, as built, does not extract it** — and after
eight pre-registered attempts, the base rate for the next idea should be set
accordingly.

### Where that leaves the search

Eight EDGE claims, eight rejections, one of them now clean. Three of the four
most-cited retail anomaly families have been tested here and none survived:
time-series momentum (§14–§21), cross-sectional momentum (§32, §35), low
volatility (§32), plus relative volume (§23) and cross-asset credit (§31).

**No strategy is enabled. `lowvol` and `xsmom` remain off.** The live record
stands at 1 closed trade against a 30-trade gate, and nothing in this section
moves it.

---

## §36 — THE REFEREE BECOMES A PROGRAM (2026-07-28, tooling — not a claim)

This section registers no hypothesis and produces no verdict about the market.
It records a change to **how** every future section is run, which is exactly the
kind of change that must be written down before it is used.

### Why

Seven gate runners (`scripts/gate_*.py`), **1,464 lines**, each re-implementing
the same five steps around `backtest.simulate_ensemble`,
`backtest.enablement_gate` and `significance.compare`. Compute was never the
constraint — §32 scored 500 symbols in 13m35s. Hand-writing a runner per
hypothesis was.

The owner asked whether the bot could continuously learn new strategies. It
cannot, and it should not yet: §33b and §34 established that in-sample selection
carries no information here — fold-majority scored 2/4 and an oracle holding the
future scored 2/4, the same as a coin. **More hypotheses through an uninformative
referee produces false positives faster, not alpha.** §35 is the worked example.

So the referee was automated instead of the idea supply.

### What must not be lost, and how it is kept

Every rejection in this file means something only because the claim, arms, pass
mark and honest prior were committed **before the runner existed**. Automation
that weakens that is worse than no automation.

`research/specs/<id>.yaml` states the claim as data. `register_gate.py` records
its canonical sha256 in `research/registrations.jsonl` **with the full spec**.
`run_gate.py` then refuses three ways:

1. **not registered** — no frozen pass mark exists, so a result could not be wrong
2. **altered since registration** — and it names the field, e.g.
   `clauses.1.pp: 1.0 -> 3.0`, because "the spec changed" only starts a search
3. **snapshot drift** — the data is not what the registration named

Re-registration is permitted while a claim has no verdict (editing before any
data is seen is authoring) and **refused once a verdict exists**. Every
registration appends; nothing is rewritten.

The hash is taken over the parsed structure, not the file bytes: reformatting is
not tampering, and a freeze that cries wolf is one people learn to bypass.

Two fields are mandatory and carry no machine meaning — `prior` and
`failure_modes`. A registration that does not say what the author expected, and
how the result could fool them, is rejected by the validator.

### What this does NOT do

- **It cannot enable anything.** A passing verdict is a recommendation; enabling
  remains the owner's decision (invariant 2).
- **It generates no hypotheses.** Deliberately. Revisit only if a selection
  method ever clears its own gate.
- **It does not fix §33.** Throughput is not information. The first claim run
  under §34's fold-majority method is still the open question.

### Known gaps, named rather than discovered later

- **§27 cannot be reproduced through it, and not because of the runner.**
  `gate_budget.py` already carries `SUPERSEDED_BY = "§29 (2026-07-26)"` and warns
  that re-running it measures a phantom: §29 replaced both levers it tests
  (`max_order_value_usd` → 0, `risk_per_trade_pct` → 8.0). Its verdict stands on
  a bot that no longer exists.
- **§31 needs aux lead-in slicing.** Its credit series must be sliced with a
  lead-in equal to the longest SMA period so the first OOS bar has history. The
  spec format passes aux snapshots whole. Until that is expressed declaratively,
  §31 stays on its committed runner.

### §36 VALIDATION — the runner reproduces §35 exactly

Before being trusted with a new claim, `scripts/run_gate.py` was pointed at
§35 via `research/specs/s35.yaml`, transcribed from the committed registration.
A referee that cannot reproduce a verdict it has already seen is not a referee,
it is a new source of error.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| baseline | +64.36% | 1.431 | 11.12% | 4,524 | 500 | 69.59% |
| xsmom-12-1 | −4.04% | 0.758 | 18.49% | 248 | 141 | 21.10% |
| buy-and-hold | +90.41% | — | — | — | — | — |

```
[FAIL] (a) enablement_gate     [FAIL] (b) pf_gt_baseline
[FAIL] (c) maxdd_within        [PASS] (d) min_trades
[FAIL] (e) significantly_better
       INCONCLUSIVE: -$16.28/trade vs +$14.23/trade, diff -$30.51,
       99.38% CI [-$91.39, +$104.13]  (Bonferroni K=8)
VERDICT: REJECTED   (908s wall, 1 worker)
```

Every figure matches the §35 table above — returns, profit factors, drawdowns,
trade counts, symbol counts, all five clause outcomes, and the bootstrap CI to
the cent. Checked arithmetically rather than by eye, since eyeballing would be
the weakest link in a change whose entire point is mechanical checking.

**This is one reproduction, not two.** The plan called for a second,
structurally different gate; reading the code retired both candidates and the
reasons are recorded above under "Known gaps". The second validation is
determinism at production scale instead — the same gate re-run at `--workers 2`,
which must produce an identical verdict.

**Nothing about §35's conclusion changes.** It was REJECTED on 2026-07-27 and it
is REJECTED now. EDGE claims remain 0 for 8. The only new fact is that the
generic runner is faithful to the bespoke one.

### §36 VALIDATION 2 — the verdict does not depend on worker count

Same spec, same frozen hash, re-run at `--workers 2`:

| | 1 worker | 2 workers | identical |
|---|---|---|---|
| every arm summary | | | ✅ |
| all five clauses | | | ✅ |
| bootstrap CI | | | ✅ |
| **wall clock** | **908s** | **556s** | — |

`spec_sha256`, `candidate`, `passed`, `clauses`, `comparison` and `arms` all
compare equal. Per-arm seconds differ (748/159 vs 554/350) because the two arms
now contend for CPU — that is timing, not result, and it is deliberately not
part of what a verdict record claims.

The wall-clock number is the one that matters for the Bizon. Run serially a gate
costs the SUM of its arms; run in parallel it costs its SLOWEST arm. Here that
is 908s → 556s on two cores, bounded below by the 748s baseline arm. A six-arm
gate on 64 cores stops costing the sum and starts costing the longest one.

That is the whole benefit, and it is worth being precise about what it is not:
**faster gates do not make the referee more informative.** §33b and §34 stand
unchanged. This buys throughput on a measuring instrument whose calibration is
still the open problem.

---

## §37 — FAMILY COHERENCE ON THE FINANCIAL CRISIS (pre-registered 2026-07-28, before the snapshot finished building)

**Claim type: EDGE.** The first use of §34's shape 2. Spec
`research/specs/s37.yaml`, frozen at `eeb75c54dfa8f90b…` before any number from
the 2007-2013 snapshot was read.

### Why this shape

§34 retired selection here: single-split, fold-majority and an oracle holding
the future all scored 2/4, indistinguishable from a coin. Its prescription was
to stop selecting, and it named three shapes. §35 used shape 1 (one
pre-specified arm). This is shape 2:

> *"Require the whole family to move. If an effect is real it should lift every
> arm in the family, not one. A result that appears in one arm and not its
> neighbours is what selection noise looks like."*

So there is **no candidate and nothing is chosen.** Three momentum lookbacks —
6-1, 9-1, 12-1 (126/189/231 bars, 21-bar skip) — each face the full five-clause
pass mark, and **one failure sinks the claim.** No selector can be credited or
blamed for the verdict, which is the entire point.

### Data — the first bear market this repository has ever scored

`bars_wide_2007-01-01_2013-12-31.json.gz`, sha256 `2ba815e1b0d2ca5d…`,
**500 symbols, 880,384 bars**, screened on 2007 median dollar volume so the
universe cannot peek at the test window. 1,181 fetched → 1,025 with 2007 history
→ 500 kept.

Zero date overlap with anything previously scored here. §35 named "benign
period — no COVID crash and no 2022 bear" as a failure mode and it has stood
unanswered since.

### Pass mark — every family member, all five

`(a)` enablement_gate · `(b)` PF > baseline · `(c)` maxDD ≤ baseline + 1.0pp ·
`(d)` trades ≥ 30 · `(e)` significantly better. **K = 9** — eight EDGE claims
precede this; three arms are tested but they are ONE claim, so the count rises
by one.

### Prior

**This fails.** EDGE claims stand at 0 for 8, and §35 already ran xsmom-12-1 on
2014-2019 where it lost 4.04% while the market gained 90.41%. The reason to run
it anyway is that if momentum has a regime where it earns its keep, a crisis is
the most-claimed one — and a three-arm requirement makes a lucky single-arm
result impossible to mistake for an effect.

### Failure modes — survivorship is worse here than anywhere previous

**The universe comes from TODAY'S index membership, so every company that went
bankrupt or was delisted in the 2008 crisis is absent by construction.** The
crash is systematically softened and long-only momentum benefits most. Direction
named in advance: this inflates every arm, so **a PASS here is weak evidence
while a FAIL is strong.**

Also registered: the 2007 liquidity screen itself reflects pre-crisis
conditions; the three arms share a snapshot and universe so "all three moved"
is far weaker than three independent confirmations; low trade counts make wide
intervals likely and INCONCLUSIVE is not support; and a PASS would still owe a
walk-forward on a period without a survivorship advantage before it meant
anything.

### The recent period is a DIAGNOSTIC, not a claim

The owner asked for 2022-2026 as well. **§32 already scored
`bars_wide_2020-01-01_2026-07-10` — 500 symbols, the xsmom and lowvol families —
and rejected them.** 2022-2026 is a subset of that period, so a run there
re-reads measurements that have already driven ~51 registered comparisons.

It is therefore reported the way §34 reported its oracle variant: **a diagnostic
that decides nothing.** It answers "does this look like it works lately?", which
is a fair question that cannot carry a verdict.

### §37 RESULT — **REJECTED, 0/3.** And for the first time, *significantly worse*.

Snapshot `bars_wide_2007-01-01_2013-12-31.json.gz` (sha256 `2ba815e1b0d2ca5d…`),
hash-verified. 500 symbols, whole period, no split, no selection. 479s wall on
4 workers.

| | return | PF | maxDD | trades | symbols | deploy |
|---|---|---|---|---|---|---|
| **baseline** | **+0.78%** | 1.030 | 10.63% | 567 | 323 | 10.44% |
| xsmom-6-1 | −14.90% | 0.356 | 26.32% | 186 | 129 | 12.04% |
| xsmom-9-1 | −25.92% | 0.013 | 26.11% | 184 | 94 | 7.49% |
| xsmom-12-1 | −23.35% | 0.011 | 33.43% | 138 | 69 | 8.12% |
| buy-and-hold | **+92.48%** | — | 55.9% | — | — | — |

**Every member fails every clause except trade count. 0/3 clear.**

### What is new here

Every prior EDGE rejection was *inconclusive* or *not better*. This is the first
time in this file that the significance test returns **SIGNIFICANTLY WORSE**,
and it does so for all three arms with the entire Bonferroni-corrected interval
below zero:

| arm | $/trade vs baseline | 99.44% CI (K=9) |
|---|---|---|
| xsmom-6-1 | −$81.51 | [−$145.32, −$17.13] |
| xsmom-9-1 | −$142.27 | [−$180.06, −$106.14] |
| xsmom-12-1 | −$170.56 | [−$212.54, −$127.14] |

**The damage is monotone in lookback.** 6-1 → 9-1 → 12-1 gets steadily worse,
in per-trade P&L and in profit factor (0.356 → 0.013 → 0.011). That is a
dose-response, not noise: the longer the formation window, the worse it did.
The family requirement was written to catch a lucky single arm; instead all
three agreed, in an ordered way.

### Why this rejection is stronger than §35's

§35 tested one arm on a benign period and lost. This tested three arms on a
period containing the worst crisis in modern market history — **the regime where
trend-following makes its loudest claim** — and the universe is tilted in
momentum's favour by construction, because every firm that went bankrupt in 2008
is absent from today's index membership. Lehman, Bear Stearns and Washington
Mutual are not in this snapshot. It holds the 2008 crash **as experienced by the
survivors**.

Momentum lost badly on data selected to flatter it, in the regime it is most
often sold for. That was the registered asymmetry: a PASS would have been weak,
a FAIL is strong. It failed.

**EDGE claims stand at 0 for 9.**

### An unregistered observation that matters more than the claim

**The baseline returned +0.78% over seven years while buy-and-hold returned
+92.48%.** That is not what §37 registered to test and it is not a verdict — but
it should not hide behind the momentum result.

Part of it is exposure: the ensemble averaged **10.44% deployment**, so it was
in cash most of the time, and `enablement_gate` reports the exposure-matched
benchmark at +11.13%. Even against that adjusted bar the incumbent underperforms.

This is a registered-in-hindsight question, so it decides nothing here. It is
written down as the thing to pre-register next: **the incumbent ensemble's own
edge over a matched-exposure benchmark, on a period it was never tuned on.**

---

## §38 — THE SAME FAMILY ON 2022-2026. **DIAGNOSTIC — decides nothing.**

Spec `research/specs/s38.yaml`, frozen `43de6f9b403a82f1…`. Snapshot
`bars_wide_2022-01-01_2026-07-24.json.gz` (sha256 `081dc08727a8cd8e…`), 500
symbols, 571,488 bars, screened on 2022 liquidity. Identical arms, clauses and
K to §37 so the two are readable on one scale. 116s wall.

`claim: DIAGNOSTIC` is a first-class type, not a caption: §32 already scored
2020-2026 with these families, this period is a subset, and a result from
re-read data is a re-description of evidence already spent.

### The numbers, against §37's

| arm | §37 (2007-2013) | §38 (2022-2026) |
|---|---|---|
| baseline | +0.78% · PF 1.030 · 10.4% deploy | **−11.62%** · PF 0.453 · 8.3% deploy |
| xsmom-6-1 | **−14.90%** · PF 0.356 | **+115.83%** · PF 2.435 |
| xsmom-9-1 | **−25.92%** · PF 0.013 | **+96.81%** · PF 2.800 |
| xsmom-12-1 | **−23.35%** · PF 0.011 | **+94.20%** · PF 2.716 |
| buy-and-hold | +92.48% | +45.11% |

And the significance test, same arms, same K=9, opposite signs:

| arm | §37 | §38 |
|---|---|---|
| xsmom-6-1 | SIGNIFICANTLY **WORSE** −$81.51/trade | SIGNIFICANTLY **BETTER** +$131.71/trade |
| xsmom-9-1 | SIGNIFICANTLY **WORSE** −$142.27 | SIGNIFICANTLY **BETTER** +$160.04 |
| xsmom-12-1 | SIGNIFICANTLY **WORSE** −$170.56 | SIGNIFICANTLY **BETTER** +$157.24 |

**A complete sign reversal, significant in both directions, on the same
strategy with the same parameters.** This is §34's "arm quality is not stable"
finding made vivid — not merely unstable, but confidently opposite depending on
which seven years you point it at.

§38 was REJECTED anyway, on drawdown alone: 17.7-22.1% against a 13.35% ceiling.
All four other clauses passed for all three arms.

### The mechanical explanation, and why it deflates the good number

**Deployment.** xsmom averaged **7.5-12.0%** invested in §37 and **66.6-80.3%**
in §38. `xsmom` only buys names whose momentum is positive. Through 2007-2013
almost nothing qualified, so it sat in cash and the few trades it did place were
bad. Through 2022-2026 nearly everything qualified, so it was near fully
invested while the market rose 45%.

So the +115% is substantially a **participation** number, not a selection edge.
The per-trade significance test compares means over 1,260 trades at 80%
deployment against 292 trades at 8% — very different animals, and that test does
not adjust for exposure. `enablement_gate`'s exposure-matched benchmark did pass
for all three, so the effect is not *purely* beta; but per-trade significance
across such different exposure profiles is a weaker statement than it looks.

### The trap this registration named in advance, and walked into

> *"If these arms happen to look good here while failing in §37, that is the
> textbook shape of a period-specific fluke, and §34 already established this
> repository cannot tell such a fluke from an effect. It would be a reason to
> distrust the number, not to enable anything."*

Written before the run. That is exactly what happened, and the pre-registration
is the only reason it reads as a warning rather than as a discovery.

**Nothing is enabled. §37's REJECTED stands. EDGE claims remain 0 for 9.**

### What both runs agree on, which nobody registered

The incumbent ensemble is the weak arm in both periods:

- §37: **+0.78%** over seven years while buy-and-hold made **+92.48%**
- §38: **−11.62%** over four and a half years while buy-and-hold made **+45.11%**

Two independent periods, one of them never touched before, and the live
configuration loses to buy-and-hold in both — losing money outright in the
recent one. Low deployment (8-10%) explains part of it, and neither figure is a
registered claim.

**This is now the most important open question in this file**, and it is about
the incumbent rather than any candidate: *does the live ensemble have any edge
over a matched-exposure benchmark on periods it was not tuned on?* It should be
pre-registered and answered before another candidate strategy is tested.

---

## ALREADY-SEEN OBSERVATION (2026-07-28) — the incumbent against its own exposure

**Not a result. Not a claim. Arithmetic on numbers already in this file**, written
down before §39 is registered so that §39 cannot later be read as discovering it.

`enablement_gate` defines the fair bar for a bot whose rails keep it mostly in
cash: `buy_hold_return_pct × (avg_deployment_pct / 100)`. Applied to the
incumbent ensemble's own baseline runs:

| period | incumbent | B&H | deploy | exposure-matched bar | |
|---|---|---|---|---|---|
| 2007-2013 (§37) | +0.78% | +92.48% | 10.44% | **+9.65%** | fails |
| 2014-2019 (§35) | +64.36% | +90.41% | 69.59% | +62.92% | passes by 1.44pp |
| 2022-2026 (§38) | −11.62% | +45.11% | 8.31% | **+3.75%** | fails |

Roughly **one in three**, and the single pass is a 1.44pp margin at 70%
deployment — the one period where the bot was mostly invested.

### Why this is not §39

Every one of these numbers was produced as a *baseline* for someone else's
claim. Reading them now is legitimate — they exist — but a "test" whose answer
can be looked up is not a test. §38 is the standing lesson: a result from data
already read is a re-description of spent evidence.

So §39 runs on **2000-2006**, which this repository has never scored. What the
table above does is make the prior honest: §39's registration can say *"the
incumbent probably fails"* and mean it, with the reason on the page rather than
in someone's head.

### The uncomfortable framing

`enablement_gate` is this repository's standard for **"may a strategy be enabled
live?"** It has rejected nine candidates. It has never once been pointed at the
strategies that are already running.

---

## §39 — THE INCUMBENT FACES ITS OWN GATE (pre-registered 2026-07-29, before the run)

Spec `research/specs/s39.yaml`, frozen `bd5b2be88f59c8d1…`. Snapshot
`bars_wide_2000-01-01_2006-12-31.json.gz` (sha256 `432cec564ccee6ab…`), 500
symbols, 878,288 bars, screened on 2000 liquidity. A period this repository had
never scored.

**One arm, no candidate.** The subject is the shipped configuration exactly as
production runs it — no `shared` risk overlay, because imposing one would mean
measuring a bot that does not exist. Every clause compares it to benchmarks
derived from its own run.

### §39 RESULT — **REJECTED. The incumbent fails its own gate, 3 of 4.**

| | | |
|---|---|---|
| incumbent | **+5.26%** | PF 1.202 · maxDD 11.51% · 101 trades · 85 symbols · **8.07% deployed** |
| buy-and-hold | **+138.54%** | maxDD 28.2% |
| exposure-matched bar | **+11.18%** | 138.54% × 8.07% |

```
[FAIL] (a) enablement_gate  PF 1.202 < 1.3; return beats neither B&H, the
                            risk-adjusted bar, nor the exposure-matched bar
[FAIL] (b) beats_exposure_matched  +5.26% vs +11.18%
[FAIL] (c) beats_buy_hold          +5.26% vs +138.54%
[PASS] (d) min_trades              101 vs 30
```

**It fails the fair bar, not just the harsh one.** Clause (c) — losing to a
138% market while 92% in cash — is close to arithmetically inevitable and is
the least interesting failure here. Clause (b) is the one that matters: scaled
to the exposure it actually carried, the bot returned **less than half** what
simply holding the index at the same average weight would have returned. The
selection is subtracting value, not merely under-deploying.

And `enablement_gate` — the function that rejected nine candidates — rejects the
incumbent on the same profit-factor floor it applies to them: **1.202 against a
1.3 minimum.**

### Where this leaves the count

| period | incumbent vs exposure-matched | |
|---|---|---|
| 2000-2006 (§39, unseen) | +5.26% vs +11.18% | **fails** |
| 2007-2013 (§37) | +0.78% vs +9.65% | fails |
| 2014-2019 (§35) | +64.36% vs +62.92% | passes by 1.44pp |
| 2022-2026 (§38) | −11.62% vs +3.75% | fails |

**One pass in four**, and that pass is a 1.44pp margin in the single period
where the bot was mostly invested. §39 is the only one of the four that was a
genuine pre-registered test; the other three are the already-seen observation
recorded before it.

### What this does and does not license

It does **not** say the live bot loses money — the paper ledger has one closed
trade and decides nothing. It says the shipped configuration, run over four
periods spanning 26 years, does not clear the bar this project holds candidates
to, and fails it on unseen data in the direction its own prior predicted.

**Nothing is being tuned in response.** Fitting parameters to 2000-2006 now
would manufacture precisely the overfit §33 and §34 established this repository
cannot detect. The registered failure modes stand: survivorship flatters the
incumbent here too, and the universe breadth (500 symbols vs the 38 it was tuned
on) is an uncontrolled confound.

### The honest reading

Nine candidate rejections were all asking *"is this new thing better than what
we run?"* — a question whose answer barely matters if what we run does not clear
the bar either. The candidates were being measured against an incumbent that
had never been measured.

**EDGE claims 0 for 10.** Nothing enabled. `mode: paper` unchanged.

### Process note — the first run crashed after computing everything

`run_gate.py` picked the candidate arm with `spec["arms"][1]`, which raised
IndexError on a one-arm spec **after** a 388-second run had produced every
number. Validation had been relaxed to permit one arm; that line was not
updated, and no test caught it because `evaluate()` is always called with an
explicit candidate — the bug lived in the gap between a tested function and its
caller.

Fixed by extracting `default_candidate()` with its own tests, and confirmed by
control that restoring the old line turns the new test RED. The re-run used the
identical frozen spec (`bd5b2be8…`, unchanged), and the simulation is
deterministic — 268s versus 388s is CPU contention, not a different experiment.

---

## §40 — WHERE DOES THE CAPITAL GO? (2026-07-29) — **DIAGNOSTIC. And it found the thing.**

Not a claim. No verdict. `scripts/census.py`, SHIPPED config unmodified
(`risk_per_trade_pct 8.0`, `max_position_pct 10.0`,
`max_portfolio_heat_pct 4.0`, `max_open_positions 0`).

### The census

| period | signals | executed | rate | fully in cash |
|---|---|---|---|---|
| 2000-2006 | 422,592 | **101** | 0.02% | **84.3%** of bars |
| 2007-2013 | 423,926 | **155** | 0.04% | **86.8%** |
| 2014-2019 | 336,408 | 1,732 | 0.51% | 18.9% |
| 2022-2026 | 245,213 | **29** | 0.01% | **92.4%** |

Median deployment is **0.00%** in three of the four periods.

### What blocks them

| period | top blocker | share of all signals |
|---|---|---|
| 2000-2006 | **drawdown** | **94.58%** |
| 2007-2013 | **drawdown** | **96.71%** |
| 2014-2019 | zero_qty 63.36%, **drawdown 20.65%** | |
| 2022-2026 | **drawdown** | **99.43%** |

### THE DRAWDOWN CIRCUIT BREAKER IS A ONE-WAY LATCH

`risk.update_high_water` ratchets the peak upward and **never down** — correct,
and documented as such: *"a drawdown rail measured against a peak that follows
equity downward would never fire."* Entries are blocked at
`max_drawdown_pct: 10.0`; exits keep running.

Combine those two and follow it through:

1. Equity peaks at **P**.
2. A drawdown of ≥10% blocks every entry.
3. Open positions exit normally. The book goes to cash.
4. In cash, equity is **flat** — permanently ~0.9P.
5. The peak stays **P**, so the drawdown stays ≥10%.
6. **The bot never buys again. For the rest of the run.**

There is no reset, no decay, no recovery path. A circuit breaker that cannot
re-close is a kill switch. That is why 2000-2006 executed 101 trades out of
422,592 signals, and why 2022-2026 executed **twenty-nine**.

It also explains the 2014-2019 outlier exactly: a smooth bull market rarely
draws down 10%, so the latch mostly never tripped — the only period the bot
participated is the one where the rail never fired.

### This is a LIVE defect, not a backtest artifact

`pre_trade_checks` reads the same persisted high-water mark
(`memory/.equity_highwater.json`) through the same `update_high_water`. The live
bot has the identical latch. Measured tonight:

```
peak $99,933.29   equity $99,783.49   drawdown 0.15%   cap 10.0%
entries blocked: NO   headroom: 9.85pp (trips at $89,939.96)
```

Production has not tripped. **It will on the first 10% drawdown, and will then
stop buying permanently** until someone deletes that file by hand.

### What it does to the ten rejections

§29 adopted this rail on 2026-07-26. **§31, §32, §33, §33b, §34, §35, §37, §38
and §39 all ran after that date.** Every one of them measured a bot that locks
itself out of the market after its first 10% drawdown.

That does not make those verdicts wrong, and none of them is being withdrawn.
It does mean the question they answered was narrower than the question they
appeared to answer: **"no edge found" and "no edge expressible" have been
indistinguishable**, and §40 is the first evidence that the second was in play.

The specific mistake to avoid: treating this as *"so the strategies are fine
after all."* Nothing here shows that. §38 already demonstrated the same arms
flipping sign across periods, and §34 showed no selector beats a coin. A bot
that can trade is a precondition for measuring edge, not evidence of it.

### Second finding: 63% of 2014-2019's signals died at zero quantity

`zero_qty` — the computed position rounds below one share — blocked **213,155**
signals in the one period the bot was actually invested. That is `min_one_share`
(§28), registered as a candidate and never gated.

### The correction I owe: three of four rows were not the incumbent

§35, §37 and §38 each applied a shared overlay (`risk_per_trade_pct 2.0`,
`max_position_pct 2.5`) so their arms differed only by strategy. Production
ships **8.0** and **10.0**. Only §39 measured the real thing, yet the
ALREADY-SEEN OBSERVATION above presented all four rows as "the incumbent". At
shipped config, measured here:

| period | incumbent | exposure-matched bar | |
|---|---|---|---|
| 2000-2006 | +5.26% | +11.18% | fails |
| 2007-2013 | **+2.59%** | +9.02% | fails |
| 2014-2019 | **+69.02%** | +62.36% | **passes by 6.66pp** |
| 2022-2026 | **−5.26%** | +1.84% | fails |

Still one pass in four. The margin in the passing period is wider than the
earlier table showed (6.66pp, not 1.44pp), and the failures are unchanged.

### Nothing was changed

No config value, no rail, no threshold. **The fix is a separate pre-registered
decision.** `config.yaml` already records a heat cap being raised and reverted
the same day on reasoning that later stopped holding; loosening a risk rail
because a diagnostic pointed at it is the same move. The latch has no reset
path, which is a correctness question rather than a tuning one — but it is the
owner's call, made deliberately, not at the end of a long session.

---

## §41 — CAN THE DRAWDOWN CIRCUIT BREAKER RE-CLOSE? (CAPACITY, K=11)

**VERDICT: REJECTED.** One period of four passed. The claim was registered as a
conjunction — s41a, s41b, s41c and s41d were written and frozen together, before
any of them ran — so one failure sinks it. `risk.drawdown_decay` stays absent
from `config.yaml` and the latch is still in place.

| spec | period | frozen sha |
|---|---|---|
| s41a | 2000-2006 | `3b5314bc1729b081…` |
| s41b | 2007-2013 | `8ffb2bc25e97a08e…` |
| s41c | 2014-2019 | `fd0b3acd77a3262e…` |
| s41d | 2022-2026 | `ab96d91b99890f1f…` |

### Results

| period | arm | return | PF | maxDD | trades | deploy |
|---|---|---|---|---|---|---|
| 2000-2006 | baseline | +5.26% | 1.202 | 11.51% | 101 | 8.07% |
| | decay | +78.04% | 1.436 | **17.67%** | 1,888 | 66.61% |
| 2007-2013 | baseline | +2.59% | 1.117 | 12.04% | 155 | 9.75% |
| | decay | +84.92% | 1.487 | **21.15%** | 1,870 | 70.21% |
| 2014-2019 | baseline | +69.02% | 1.486 | 11.59% | 1,732 | 68.97% |
| | decay | +99.43% | 1.572 | 11.59% | 2,087 | 83.28% |
| 2022-2026 | baseline | −5.26% | 0.449 | 10.78% | 29 | 4.09% |
| | decay | +35.06% | 1.245 | **15.73%** | 1,598 | 69.52% |

| clause | s41a | s41b | s41c | s41d |
|---|---|---|---|---|
| (a) maxdd_within pp 3.0 | **FAIL** 17.67 vs 14.51 | **FAIL** 21.15 vs 15.04 | PASS 11.59 vs 14.59 | **FAIL** 15.73 vs 13.78 |
| (b) not_worse | PASS* | PASS* | PASS* | PASS* |
| (c) min_trades 30 | PASS | PASS | PASS | PASS |
| (d) deployment_at_least 25.0 | PASS | PASS | PASS† | PASS |

\* every (b) is INCONCLUSIVE — see below. † non-binding by construction, declared
in the spec before the run.

### What is established

**§40's diagnosis is confirmed beyond argument.** The latch was the binding
constraint on deployment, and it was not close. Removing it moved 2022-2026 from
**29 trades to 1,598** and deployment from 4.09% to 69.52%; 2000-2006 from 101 to
1,888. Whatever else is true, the bot was not choosing to sit in cash — it was
locked out.

**The failure was predicted in the frozen prior**, quoted verbatim from all four
specs: *"the honest expectation is that maxdd_within is the clause most likely to
fail: a bot that re-enters after a drawdown is a bot that can take a second
drawdown, and 3.0pp is not a generous allowance."* Pre-registration working as
intended — the outcome was named before it happened rather than explained after.

**The rejection reason is consistent in three periods of four.** The decay arm
roughly doubles maximum drawdown: +6.16pp, +9.11pp, +4.95pp. Letting the bot
trade costs about twice the drawdown, and the registered mark refuses it on
exactly that ground.

### Two things that must not be over-read

**Return is not evidence here, and was deliberately not a clause.** More
deployment means more exposure to the market's own direction; on a rising period
the decay arm will look better for reasons that have nothing to do with the rail.
Anyone quoting +78%, +85% or +99% from this table as a result is misreading the
spec. Exposure-matched, the decay arm clears its bar on three periods and misses
on 2000-2006 (+78.04% against +92.28%) — a mixed picture, and **§38 already
established that this ensemble reverses sign between eras.** No edge claim of any
kind may be drawn from §41.

**Every (b) PASS is INCONCLUSIVE and carries no information.** The confidence
intervals exclude nothing: `[$-276, $+172]`, `[$-198, $+187]`, `[$-48, $+64]`,
`[$-246, $+557]`. The point estimates even move in opposite directions across
periods — $52.11→$41.34 on 2000-2006 against $16.70→$45.41 on 2007-2013.
"Not significantly worse" at these sample sizes is a statement about statistical
power, not about trade quality.

### An anomaly worth flagging rather than explaining away

On 2014-2019 the two arms report **identical maximum drawdown to the digit —
11.59% both** — while deployment rose 68.97% → 83.28% and trades rose 1,732 →
2,087. That is plausible if the period's worst drawdown is a single market-wide
drop both arms were fully exposed to, and 2014-2019 is the one period where the
latch barely operated (drawdown was 20.65% of blocks, not 94-99%). It is
recorded here as unexplained rather than assumed benign; anyone building on §41
should confirm it before relying on that row.

### A correction to this session's own commentary

After s41a alone, the interim summary given to the owner stated that *"removing
the lockout did not make the bot beat its own benchmark."* s41b, s41c and s41d
each contradict it on the exposure-matched measure. The claim was generalised
from one period — the precise error the four-spec conjunction exists to prevent,
committed in the narration rather than in the spec. Recorded rather than edited
away, in the same spirit as §33 Run 1.

### What happens next — and what must not

**`pp: 3.0` will not be widened and §41 re-scored.** The mark was frozen before
the data existed, and re-scoring a rejected candidate against a loosened bar is
the move this entire programme exists to prevent. `config.yaml` still carries the
record of a heat cap raised and reverted the same day on reasoning that stopped
holding.

The legitimate follow-up is a **different claim**, not a second look at this one.
§41 tested the decay at unchanged sizing, which confounds "the bot trades more"
with "the bot risks more": deployment rose roughly eightfold in three periods, so
of course drawdown grew. A §42 could pair the decay with reduced per-trade sizing
and ask whether return improves **at matched drawdown**. That is a new
hypothesis, requiring a fresh registration citing this rejection in its prior,
and it is the owner's decision to make — not an automatic next step.

**EDGE claims remain 0 for 10.** §41 is CAPACITY and does not touch that record.

### Nothing was adopted

`risk.drawdown_decay` is absent from `config.yaml`, so `risk.decayed_peak()` is
the identity function in production. Verified after the change: 2022-2026
reproduces §40 exactly — −5.26%, PF 0.449, 29 trades, 4.09% deployment, 243,814
drawdown blocks, 245,213 signals. Every result from §1 to §40 stands unaltered.

**The latch is still live, and still has no reset path.** §40's warning is
unchanged: production trips at a 10% drawdown and then stops buying permanently
until `memory/.equity_highwater.json` is deleted by hand. §41 establishes that
the obvious fix costs more drawdown than the pre-registered allowance permits —
it does not establish that the latch is acceptable.

---

## §42 — DIAGNOSTIC: what the judge does to the census (W2-1, 2026-07-29)

**Not a claim. No verdict. Nothing is enabled or rejected by this section.**
`scripts/census.py` on the SHIPPED config, run twice, changing exactly one
thing: whether `backtest.judge_model` is on. Both runs come from the same
commit, so the judge is the only variable.

### The control reproduces §40 to the digit

| period | §40 executed | judge-OFF today | §40 in cash | judge-OFF today |
|---|---|---|---|---|
| 2000-2006 | 101 | **101** | 84.3% | **84.3%** |
| 2007-2013 | 155 | **155** | 86.8% | **86.8%** |
| 2014-2019 | 1,732 | **1,732** | 18.9% | **18.9%** |
| 2022-2026 | 29 | **29** | 92.4% | **92.4%** |

Returns match too (+5.26 / +2.59 / +69.02 / −5.26). This matters: it is what
licenses reading the table below as *the judge's effect* rather than as drift
from four days of unrelated commits.

### The paired result

| period | executed off → on | deployment off → on | return off → on | PF off → on | drawdown share off → on |
|---|---|---|---|---|---|
| 2000-2006 | 101 → **753** | 8.07% → 20.95% | +5.26% → +9.45% | 1.202 → **1.122** | 94.58% → 74.45% |
| 2007-2013 | 155 → **269** | 9.75% → 10.92% | +2.59% → +2.42% | 1.117 → **1.082** | 96.71% → 93.76% |
| 2014-2019 | 1,732 → **3,057** | 68.97% → 69.25% | +69.02% → +56.71% | 1.486 → **1.396** | 20.65% → 20.97% |
| 2022-2026 | 29 → **54** | 4.09% → 4.72% | −5.26% → −7.71% | 0.449 → **0.367** | 99.43% → 96.86% |

### Two findings, in opposite directions

**1. Modelling the judge makes the bot trade MORE — in all four periods.**
1.7x to 7.5x more executions. That reads backwards, since the judge only ever
SHRINKS a position, until the latch is put back in the picture: smaller
positions produce shallower drawdowns, a shallower drawdown trips the 10% rail
less often, and a rail that trips less often blocks fewer entries. On 2000-2006
the drawdown share of all blocks falls 94.58% -> 74.45% and executions go 101 ->
753.

So **§35-§41 measured a bot MORE locked out than production is.** §40's
headline — "84-92% fully in cash, 94-99% of blocks are the drawdown rail" —
overstates the real bot's lockout. The latch is still the dominant blocker in
three of four periods (74%, 94%, 97%), and §40's diagnosis stands in direction.
Its magnitude does not.

**2. Profit factor falls in all four periods.** 1.202→1.122, 1.117→1.082,
1.486→1.396, 0.449→0.367, and return falls in three of four. The judge-less
simulator was **flattering the incumbent's trade quality**, which is the more
important of the two findings and the one that would have been easiest to miss,
because it runs against the direction of the first.

### What this does NOT do

**It reverses nothing.** All ten EDGE verdicts are rejections. A simulator that
overstated the incumbent's profit factor makes a rejection *more* robust, not
less — the candidate had to beat a bar that was set too high, and still failed.
No verdict is withdrawn, re-scored, or revisited on this basis, and the EDGE
tally stands at **0 for 10**.

**It does not re-open §41.** §41's rejection was measured judge-less, so whether
the decay arm still roughly doubles maxDD with the judge on is unmeasured. That
is a reason to register a NEW claim, never a reason to re-score a rejected one.

**It is not evidence about live.** The judge model reproduces a *distribution*
(n=164 decisions over 12 days, one regime, heavily serially correlated), not the
judge's actual decisions. Nothing here says the live bot would have executed 753
times on 2000-2006. It says a simulator that models the haircut behaves
differently from one that ignores it, by enough that the difference is not
ignorable.

### Renumbering note

The deferred owner decision — pair the drawdown decay with reduced per-trade
sizing and test at MATCHED drawdown — becomes **§43** when it is registered.
This DIAGNOSTIC took §42 because it ran first. Numbers are permanent; the
earlier plan text calling that claim "§42" is superseded here rather than
edited away.

---

## §43 — THE INCUMBENT FACES ITS OWN GATE ON A SIMULATOR THAT MATCHES LIVE

**REJECTED, 0 of 4.** Specs `s43a`–`s43d`, frozen together 2026-07-29 as one
claim (`f3f5901e6a99cce5…`, `9bd8d5394c287d89…`, `4edd461cfcb0bb34…`,
`bcc25284ec0c668a…`), K=12, conjunction — all four must pass. Raw output:
`research/s43_run_2026-07-29.txt`.

**§39's pass mark, verbatim, clause for clause.** Same four clauses, same
`n: 30`, same single arm, same shipped `config.yaml` with no risk overlay. The
only thing that moved is the fidelity of the simulator: Wave 2 closed
divergences #8 (the judge), #11 (live peak sampling) and #12 (raw bars). That
is the design — any change in verdict is attributable to fidelity, not to a bar
someone quietly made easier.

`judge_model: true` on all four, and the runner's refusal check confirms it
fired rather than merely being declared: 129,179–419,540 entries sized per
period, 72,932–237,450 cut, 3,123–10,384 vetoed.

### Result

| period | return | exposure-matched bar | margin | PF | deploy | trades |
|---|---|---|---|---|---|---|
| 2000-2006 | +9.45% | +29.02% | **−19.57pp** | 1.122 | 20.95% | 753 |
| 2007-2013 | +2.42% | +10.10% | **−7.68pp** | 1.082 | 10.92% | 269 |
| 2014-2019 | +56.71% | +62.61% | **−5.90pp** | 1.396 | 69.25% | 3,057 |
| 2022-2026 | −7.71% | +2.13% | **−9.84pp** | 0.367 | 4.72% | 54 |

Clauses (a), (b) and (c) FAIL in all four periods. Clause (d) `min_trades`
passes in all four. Nothing is enabled; the incumbent stands as the incumbent
only because nothing has been proposed to replace it.

### The finding: the one pass in this project's history was an artifact

2014-2019 is the only period in which this bot has ever cleared its own gate.
§35 recorded it passing the exposure-matched bar, and on the judge-off census it
clears by **+6.66pp** (+69.02% against a +62.36% bar). With the judge modelled
it **fails by −5.90pp** (+56.71% against +62.61%). Deployment barely moves
(68.97% → 69.25%), so the bar stays where it was and the bot falls under it.

That single flip changes the honest count from **one period in four** to
**zero in four**. The pass was not a marginal result that survived scrutiny — it
was produced by a simulator that omitted a component the live bot had been
running for weeks.

### The prior called every number before the run

The frozen prior computed clause (b) for all four periods from §42's
already-published census and named the expected margins. **Every cell matched
the run exactly** — +9.45/+29.02, +2.42/+10.10, +56.71/+62.61, −7.71/+2.13, and
profit factors 1.122 / 1.082 / 1.396 / 0.367.

This is worth stating plainly in both directions. It is pre-registration working
as intended: the prediction was specific to a fraction of a percentage point and
could have been embarrassed by any of forty numbers. It is also **weaker
evidence than it looks**, and the spec's own failure modes say so: the census
inputs were already known, so (b) and (c) were arithmetic, not discovery. The
genuinely new information is clause (a) `enablement_gate`, which is not
computable from a census, and it failed in all four.

### What §43 does NOT license

- **It does not reverse anything.** Ten EDGE claims were rejections and remain
  rejections. §43 is the eleventh EDGE rejection. **EDGE claims: 0 for 11.**
- **It does not say the live bot loses money.** The paper ledger holds **two**
  closed round-trips, both winners. n=2 decides nothing in either direction.
- **It is not a licence to tune.** Fitting parameters to any of these periods
  now would manufacture exactly the overfit §33 and §34 established this
  repository cannot detect. No threshold is being adjusted in response.
- **Survivorship still flatters the incumbent** in all four periods, so these
  FAILs are strong evidence and a PASS would have been weak. The 500-symbol
  universe against 38 tuned symbols remains an uncontrolled confound.

### The honest reading

§39 established that the incumbent fails its own gate on one unseen period.
§43 establishes that it fails on all four, on the first simulator this project
has had that models what the live bot actually does — and that the lone
historical pass does not survive the repair.

The question this programme has been asking since §14 is *"is this candidate
better than what we run?"* §43 says the more useful question is whether what we
run should be running at all. That is the owner's decision, not a gate's, and it
is now supported by four periods spanning 26 years rather than one.

**Nothing enabled. `mode: paper` unchanged. `drawdown_decay` still absent from
`config.yaml`. EDGE 0 for 11.**
