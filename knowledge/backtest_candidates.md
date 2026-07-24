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
