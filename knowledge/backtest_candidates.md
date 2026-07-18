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
