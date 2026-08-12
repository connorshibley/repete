# Trading Agent Knowledge Base — Master Index

*This is external, unverified research — same status as `knowledge/principles.md` and `knowledge/swing_trading_playbook.md` elsewhere in this repo. Nothing here is auto-adopted as a live signal or gated candidate; it is reference material for designing and evaluating strategy hypotheses. Researched and backtested equity strategies, US market mechanics, a news/data source guide, and an agent harness design (guardrails, triggers, tools, skills), phased long-term → swing → active short-term. Last assembled August 2026.*

> **Not financial, legal, or tax advice.** Everything below is research synthesis with inline citations to primary/authoritative sources. Nothing here is a recommendation to buy or sell any security. Paper-trade extensively before connecting any of this to a live brokerage account — see the guardrail checklist in Section 4.

---

## How this knowledge base is organized

| File | Contents |
|---|---|
| [`research/us_market_mechanics.md`](research/us_market_mechanics.md) | How the US equity market actually works: exchanges, dark pools, order types, T+1 settlement, circuit breakers, tape structure — the plumbing every other module assumes. |
| [`research/momentum_trend_following.md`](research/momentum_trend_following.md) | Cross-sectional momentum, time-series trend-following (200-SMA), dual momentum, sector rotation — theory, live track records, and a codeable rule-set. |
| [`research/factor_investing.md`](research/factor_investing.md) | Fama-French factors, live factor ETF track records (MTUM/VLUE/QUAL/USMV/SIZE), multi-factor combination evidence, a codeable rule-set. |
| [`research/event_news_driven.md`](research/event_news_driven.md) | Post-earnings drift, analyst-revision strategies, merger arbitrage, FOMC drift, **plus the full news/data source guide** (premium wires, affordable APIs, SEC EDGAR, macro calendars, alt-data). |
| [`research/options_overlays.md`](research/options_overlays.md) | Covered calls, cash-secured puts, protective puts/collars, LEAPS — Cboe benchmark index data, live fund track records (QYLD/JEPI), a codeable overlay rule-set. |
| [`research/guardrails_architecture.md`](research/guardrails_architecture.md) | Position sizing math, stop-loss/drawdown rules, kill-switch patterns, current US regulatory mechanics (2026 PDT rule replacement, wash sales, NRA tax mechanics), software architecture patterns, broker API comparison, an 18-item guardrail checklist. |
| [`harness/agent_harness_design.md`](harness/agent_harness_design.md) | **The build plan.** Translates all research above into a phased (long-term → swing → active) agent design: triggers, tools, skills, and guardrails per phase. |
| [`backtests/run_backtests.py`](backtests/run_backtests.py) | Backtest code (6 strategies vs. SPY benchmark). |
| [`backtests/backtest_results.json`](backtests/backtest_results.json) | Raw backtest output. |
| [`backtests/sma_trend_vs_spy.png`](backtests/sma_trend_vs_spy.png) | Growth-of-$1 and drawdown chart, SMA trend timing vs. buy-and-hold SPY. |

---

## 1. Does the evidence support strategies that beat the S&P 500 long term?

**Short answer: yes, but selectively, and mostly through a risk-adjusted lens rather than a "beats SPY in every window" lens.** The strongest, most repeatable edges in the literature and in this project's own backtests are:

1. **Multi-factor combination (value + momentum + quality)**, not any single factor alone. AQR's own long-run research shows a combined multi-factor portfolio achieving a Sharpe ratio of ~1.74 versus 0.29-0.87 for any single factor in isolation ([`factor_investing.md`](research/factor_investing.md), Section 9) — diversification *across factors* is itself the edge, much like diversification across assets.
2. **Momentum has the most consistent live track record** of the single factors tested: iShares MTUM has returned 16.79%/yr since 2013 launch vs. SPY's ~10.8-10.9%/yr since 1993 inception (different windows, but directionally consistent with this project's own matched-window backtest: MTUM 12.73% CAGR vs. SPY 12.09% CAGR over the same 2013-2024 window — see table below).
3. **Trend-following (200-day SMA) does not reliably beat SPY on raw return**, but it reliably and substantially **cuts drawdown and volatility** — this project's own backtest found 5.94% CAGR (vs. SPY's 8.29% over the same longer window) but with max drawdown of only -20.5% vs. SPY's -56.5%, roughly matching the qualitative conclusion of Meb Faber's published research. Whether this is "beating the market" depends entirely on whether the investor values the smoother ride (higher Sharpe: 0.56 vs. 0.51) over raw CAGR.
4. **Covered-call and put-writing overlays underperform buy-and-hold on total return** in the Cboe benchmark index data (BXM 8.3%/yr vs. SPY 10.9%/yr since inception; QYLD and JEPI similarly lag their respective benchmarks on a total-return basis), but the **Cboe PUT Index (cash-secured put writing) shows the best risk-adjusted profile found across this entire research effort**: 9.54% CAGR vs. SPY's 9.80% (essentially matched), but Sharpe 0.65 vs. 0.49 and max drawdown -32.7% vs. -50.9% ([`options_overlays.md`](research/options_overlays.md)).
5. **Naive sector-momentum rotation, as backtested here without transaction costs, did not beat SPY** (7.89% vs. 8.37% CAGR) — and real-world transaction costs on a monthly-rebalanced, high-turnover strategy would only widen that gap. This is an important negative result: not every intuitive-sounding rotation strategy clears the bar.

**Bottom line for the agent's design:** the credible, defensible "beat the market" theses to build around are (a) a multi-factor tilted core (Phase 1), (b) trend-following as a risk-reduction overlay rather than a return-enhancement play (Phase 1), and (c) selective options premium-selling for risk-adjusted (not raw-return) improvement (Phase 3) — not naive single-signal rotation strategies.

---

## 2. Backtest results summary (this project's own runs)

All backtests use the Perplexity Finance connector's daily OHLCV data, **price-return only (dividends not reinvested)**, no transaction costs modeled, no lookahead (signals lagged one day where applicable). See `backtests/backtest_results.json` for full precision.

| Strategy | Window | CAGR | Volatility | Sharpe | Max Drawdown |
|---|---|---|---|---|---|
| Buy & Hold SPY | 2005-2024 (19.9yr) | 8.29% | 19.12% | 0.51 | -56.5% |
| 200-Day SMA Trend Timing (SPY/BIL) | 2005-2024 (19.1yr) | 5.94% | 11.42% | 0.56 | -20.5% |
| Sector Momentum Rotation (top-3 of 9) | 2006-2024 (18.75yr) | 7.89% | 15.21% | 0.58 | -42.1% |
| — SPY same window | 2006-2024 | 8.37% | 15.42% | 0.60 | -52.2% |
| MTUM (momentum factor ETF) | 2013-2024 (11.7yr) | 12.73% | 19.22% | 0.72 | -34.1% |
| — SPY same window | 2013-2024 | 12.09% | 16.89% | 0.76 | -34.1% |
| VLUE (value factor ETF) | 2013-2024 (11.7yr) | 6.72% | 18.55% | 0.44 | -39.5% |
| QUAL (quality factor ETF) | 2013-2024 (11.5yr) | 11.70% | 17.28% | 0.73 | -34.1% |
| USMV (low-vol factor ETF) | 2011-2024 (13.2yr) | 9.96% | 13.68% | 0.76 | -33.1% |
| — SPY same window | 2011-2024 | 12.65% | 16.69% | 0.80 | -34.1% |
| SIZE (size factor ETF) | 2013-2024 (11.7yr) | 9.72% | 17.46% | 0.62 | -39.2% |
| QYLD (covered-call, price-only) | 2013-2024 (11.05yr) | **-2.84%** (see caveat) | 14.94% | -0.12 | -40.7% |
| Dual Momentum proxy (SPY/TLT/cash) | 2008-2024 (16.5yr) | 5.76% | 13.59% | 0.48 | -27.0% |
| — SPY same window | 2008-2024 | 9.66% | 15.91% | 0.66 | -42.6% |

**Critical data-quality caveat on QYLD:** the -2.84% CAGR above is a price-only artifact — the finance connector's OHLCV data has no adjusted-close/total-return field, and QYLD's entire return thesis is its ~11-12%/yr distribution, which is invisible in a price-only series. The economically meaningful comparison uses fund-fact-sheet **total return**: QYLD ~8.26%/yr since inception NAV vs. its Nasdaq-100 benchmark ~18.87%/yr ([`options_overlays.md`](research/options_overlays.md)) — still a real underperformance, but nowhere near as dramatic as the raw price-only backtest suggests. **Any agent or dashboard built on this data must use total-return figures for income-focused instruments (QYLD, JEPI, and any dividend-heavy ETF), never raw close-price CAGR.**

**Data source note:** the "cross-validated" requirement was partially met — all backtests here use one source (the Perplexity Finance connector), since `yfinance` was rate-limited and `stooq.com` blocked automated access from this environment. Fund-fact-sheet figures cited throughout the research files (from issuer websites and Cboe) served as an independent secondary check on the connector-derived numbers, and in every case they were directionally consistent.

---

## 3. The agent's phased build order

See [`harness/agent_harness_design.md`](harness/agent_harness_design.md) for full detail. Summary:

1. **Phase 1 — Long-term position trading:** 200-SMA trend overlay + multi-factor (momentum/quality) tilted core, monthly rebalance, no day-trading logic, minimal regulatory surface.
2. **Phase 2 — Swing trading:** adds sector-momentum rotation and PEAD (post-earnings drift) signals, ATR-based per-trade stops and sizing, earnings-calendar blackout logic.
3. **Phase 3 — Active/short-term trading:** adds options overlays (covered calls / cash-secured puts), fast news/event reaction, and the strictest guardrail tier (kill-switches wired to automated triggers, margin/IMD awareness, wash-sale tagging).

Guardrails only tighten as phases advance — nothing from an earlier phase is ever relaxed.

---

## 4. Guardrail checklist before any live capital

The full 18-item checklist lives in [`guardrails_architecture.md`](research/guardrails_architecture.md#7-recommended-guardrail-checklist-before-connecting-to-a-live-brokerage-account). Highest-priority items:

1. Full event-driven backtest → extended paper trading → small live "shakedown" allocation, in that order — never skip a stage.
2. Hard position-size and per-trade risk caps enforced in code, independent of what any sizing formula outputs.
3. A portfolio-level max-drawdown circuit breaker and separate daily/weekly loss limits.
4. A single, low-friction kill switch wired to automated triggers (data staleness, broker errors, VIX spikes, market-wide circuit breakers, LULD pauses).
5. Human sign-off required for any change that loosens a risk control — the agent must never widen its own guardrails.
6. Confirm your specific broker's current PDT/margin treatment — FINRA replaced the classic $25k/4-day-trade Pattern Day Trader rule with a new "intraday margin deficit" framework effective June 4, 2026, phasing in through October 2027 ([`guardrails_architecture.md`](research/guardrails_architecture.md), Section 4.1). Check which regime your broker is currently running before assuming either applies.

---

## 5. Key open questions / next research if continuing

- Real transaction-cost and slippage modeling for the swing/active phases (not yet incorporated into any backtest here).
- A genuinely independent second data source for backtest cross-validation (both `yfinance` and `stooq` were blocked in this environment).
- Live paper-trading validation once Phase 1 code exists — backtests here are historical only, not forward-tested.

