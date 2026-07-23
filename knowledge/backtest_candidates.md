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
