# Backtest candidates (from external knowledge review, 2026-07-16)

Rules distilled from the autonomoustrading.io review + our own research.
NOTHING here goes live without passing `backtest.enablement_gate`
(walk-forward OOS: positive, PF>=1.3, >=15 trades, beats B&H raw /
risk-adjusted / exposure-matched).

## 1. Earnings-blackout entry filter
**Spec:** block new entries in single names (ETFs exempt) within N trading
days before a scheduled earnings report (test N = 3, 5). Exits unaffected.
**Rationale:** gaps break stop assumptions; overnight gap risk is
uncompensated for a 2-day-minimum swing hold.
**Data gap:** needs an earnings calendar source (Alpaca doesn't provide one on
the free tier; candidates: yfinance calendar, Financial Modeling Prep free
tier). Blocked until a source is wired into backtest.py.

## 2. Time-varying stop width by vol regime
**Spec:** stop_atr_mult 2.0 in low-vol regime, test 2.5–3.0 in high-vol
(regime from src/regime.py, deterministic at entry).
**Rationale:** fixed 2×ATR may whipsaw in high-vol regimes.
**Testable now:** `python src/backtest.py --param stop_atr_mult 2.0 2.5 3.0`
per regime bucket (needs a small harness extension to condition on regime).

## 3. Regime gate on tsmom entries — ADOPTED 2026-07-16
**Spec:** block tsmom entries when SPY < SMA50 (regime down/*), test vs
current behavior (tsmom already requires symbol > its own SMA200).
**Rationale:** the judge repeatedly flags long momentum in down regimes; make
it a testable deterministic rule instead of paying LLM skepticism per trade.
**Result (walk-forward 2020-01→2026-07, 25 symbols):** won IS (+4.10% PF 3.46
vs ungated +3.51% PF 2.27, 106 vs 155 trades — the improvement comes from the
2022 bear inside the IS window), OOS neutral (+1.453% PF 2.52 vs ungated
+1.45% PF 2.56 — SPY sat above SMA50 for nearly the whole 2024-26 OOS window,
so the gate barely fired). Met all pre-registered adoption conditions
(IS winner, enablement gate PASS, OOS >= ungated). Live as
`strategies.tsmom.index_sma_period: 50`.

## 4. Meanrev max-positions-per-cycle cap — REJECTED 2026-07-16
**Spec:** limit meanrev to 1 new entry per cycle (dips cluster on market-wide
down days, concentrating correlated entries).
**Result (walk-forward 2020-01→2026-07, 25 symbols):** won IS (+0.72% PF 1.19
vs uncapped +0.56% PF 1.11) but clearly LOST OOS: +0.71% PF 1.55 / 133 trades
vs the uncapped +1.40% PF 2.19 / 171 trades on the same window. The clustered
dip entries the cap removes were profitable out-of-sample — an in-sample
mirage. Config unchanged (no cap). The param-gated code path remains for
future retests; both variants logged in memory/backtest_trials.jsonl.
