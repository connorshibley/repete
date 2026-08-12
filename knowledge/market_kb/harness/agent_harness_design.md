# Trading Agent Harness Design — Guardrails, Triggers, Tools, Skills

*This module translates the research and guardrails findings into a concrete build plan for the agent itself, phased as requested: **Phase 1 — long-term position trading, Phase 2 — swing trading, Phase 3 — active/short-term trading.** Each phase adds capability on top of the previous one; guardrails only get stricter as trading frequency increases, never looser.*

---

## 0. Overall architecture: sense → think → act

Per the guardrails research ([`guardrails_architecture.md`](../research/guardrails_architecture.md), Section 5), the agent should be a single **event-driven loop** reused unchanged across backtest, paper, and live modes:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    SENSE     │ --> │    THINK     │ --> │     ACT      │
│ market data, │     │  strategy /  │     │ order mgmt,  │
│ account state│     │  risk model  │     │ broker API   │
│ news, events │     │  (per phase) │     │ + audit log  │
└──────────────┘     └──────────────┘     └──────────────┘
        ▲                                         │
        └───────────── event queue ───────────────┘
```

Every "think" decision — trade or no-trade — must be logged with the full input snapshot (market data used, strategy version, computed size/stop, guardrail checks passed/blocked, resulting order ID). This is a FINRA-modeled requirement (guardrails doc, Section 5.5), and it is also simply good debugging practice for a solo builder.

---

## 1. Phase 1 — Long-term position trading agent

**Objective:** implement the two strategies with the strongest, most robust long-term evidence from the research: trend-following (200-day SMA) as a risk-reduction overlay, and factor-tilted core holdings (momentum + quality) as the return driver, consistent with the momentum/factor backtests already run ([`momentum_trend_following.md`](../research/momentum_trend_following.md), [`factor_investing.md`](../research/factor_investing.md)).

### Triggers (event sources)
- **Scheduled, low-frequency:** end-of-day close (compute 200-day SMA position, factor scores); monthly rebalance date (first trading day of month).
- **Calendar-driven:** quarterly index reconstitution dates (Russell/S&P rebalance) if trading factor ETFs directly, since these create temporary liquidity/flow effects.
- No intraday triggers needed at this phase — the strategy's entire edge (per the research) comes from multi-week/multi-month holding periods, so intraday noise is a distraction, not a signal.

### Tools required
- **Data:** daily OHLCV feed (Perplexity finance connector or a broker's market-data API) — no need for tick/intraday data at this phase.
- **Execution:** any broker API with reliable market/limit orders and a paper-trading sandbox — Alpaca recommended for a first build (guardrails doc, Section 6).
- **Calendar:** market holiday/trading-day calendar (Alpaca `/calendar` endpoint) to correctly compute "first trading day of month."

### Skills (capabilities the agent's code must have)
1. Compute a trailing moving average and detect crossover state changes without lookahead (use only data available as of the prior close).
2. Compute cross-sectional or trailing factor scores (12-1 month momentum; quality proxies) across a fixed universe of sector/factor ETFs.
3. Generate monthly-rebalance target weights and translate them into buy/sell orders.
4. Track portfolio drift vs. computed target weights between rebalances (in case of large moves) to decide whether an off-cycle rebalance is warranted.

### Guardrails specific to this phase
- Position sizing: fixed-fractional target weights per the constructed multi-factor rule-set (factor research, Section 9) — no single-name/single-ETF concentration beyond a hard cap (e.g., 25-35% in any one sleeve).
- Portfolio-level drawdown circuit breaker (guardrails doc, Section 2.3): if portfolio equity falls more than a pre-set % from peak, force a move to the SMA-signal's "risk-off" cash/bond allocation regardless of factor signals.
- No day-trading logic at all in this phase — this sidesteps the (now-changing) PDT/margin-deficit mechanics entirely (guardrails doc, Section 4.1), which is a meaningful simplicity win for a first build.
- Human-in-the-loop: require manual confirmation for the first N live rebalances before allowing fully unattended execution.

---

## 2. Phase 2 — Swing trading agent (add-on)

**Objective:** add a higher-frequency (multi-day to multi-week hold) layer that trades on top of the Phase 1 core — e.g., sector rotation momentum and event/earnings drift signals from the research ([`event_news_driven.md`](../research/event_news_driven.md)), while keeping the Phase 1 book as the "strategic" allocation.

### Triggers
- **Intraday-adjacent, not tick-level:** end-of-day scans for breakout/momentum setups, PEAD (post-earnings-announcement drift) candidates the morning after an earnings release, and sector-momentum re-ranking weekly rather than monthly.
- **Earnings calendar events:** ingest a forward earnings calendar (e.g., via a data API) as a first-class trigger — the agent should flatten or avoid new entries in a name 1-2 days before its earnings date (gap risk) and can consider entries in the drift window (2-60 trading days) after a large earnings-surprise beat, per the PEAD literature reviewed.
- **Stop/target price alerts:** now needed since holds are shorter and stop discipline matters more; requires a streaming or frequently-polled price feed rather than end-of-day-only data.

### Tools required
- **Data:** near-real-time (delayed is acceptable) price feed in addition to daily OHLCV; an earnings-calendar API (e.g., Financial Modeling Prep, Finnhub — see news/data source guide in `event_news_driven.md`); a fundamentals/estimates feed to compute earnings surprise vs. consensus.
- **Execution:** stop and stop-limit order support (not just market/limit) — verify the chosen broker API supports these natively rather than requiring agent-side price-watching (Alpaca and IBKR both support server-side stop orders).
- **News:** a lightweight news API (Marketaux, NewsAPI.org, or Benzinga) to flag major unscheduled news on held/candidate names, even if full NLP sentiment scoring is deferred to Phase 3.

### Skills
1. Compute ATR-based stop distances and position sizes (guardrails doc, Section 1.2/2.2) — this phase is where ATR sizing starts to matter, since Phase 1's monthly rebalance didn't need per-trade stop math.
2. Parse an earnings calendar and cross-reference it against current/candidate holdings to enforce the pre-earnings blackout window.
3. Compute earnings-surprise magnitude (actual vs. consensus EPS) to rank PEAD candidates.
4. Rank sector/industry momentum on a rolling weekly basis and generate rotation trade lists.
5. Track open positions against ATR-based stop and target levels continuously (or on each data poll) rather than only at rebalance time.

### Guardrails specific to this phase
- Per-trade risk cap: 1-2% of equity risked per trade (guardrails doc, Section 2.1), sized via ATR stop distance — this is now a hard requirement, not optional, because trade frequency and single-name concentration both rise relative to Phase 1.
- Daily/weekly loss limits (guardrails doc, Section 2.4): a bad week of swing trades should force a mandatory pause and review, independent of the portfolio-level drawdown breaker inherited from Phase 1.
- Earnings blackout enforcement is itself a guardrail (gap risk cannot be stopped out intraday).
- Transaction-cost and slippage modeling become mandatory in backtests at this phase — the sector-rotation backtest already run showed it underperforming buy-and-hold *before* costs, so any related live strategy needs an even higher hurdle to justify trading.

---

## 3. Phase 3 — Active / short-term trading agent (add-on)

**Objective:** add the highest-frequency layer — options overlays (covered calls / cash-secured puts per the options research) and faster news/event reaction — while explicit regulatory and infrastructure guardrails now bind hardest.

### Triggers
- **Options-specific:** monthly/weekly options-expiration cycle triggers for covered-call or put-writing overlay roll dates (28-35 DTE per the codeable rule-set in `options_overlays.md`); IV-rank threshold crossings to decide whether premium-selling is attractive that cycle.
- **News/webhook, low-latency:** push-based news classification (not polling) so the agent can react within minutes, understanding it is not competing on millisecond speed (market mechanics doc, Section 7) — the edge here is targeting the drift window, not the first tick.
- **Volatility triggers:** VIX level/spike thresholds (guardrails doc, Section 3.3) that can pause new option-selling entries (selling premium into a volatility spike without adjusting size is a classic blow-up pattern) or, conversely, flag richer premium opportunities if the strategy is designed to harvest elevated IV deliberately.
- **Market-wide circuit breaker / LULD state** (market mechanics doc, Section 5; guardrails doc, Section 3.4): now a first-class, unconditional order-blocking event given the shorter time horizons involved.

### Tools required
- **Options data:** an options-chain data feed with Greeks and IV (many broker APIs, e.g., Alpaca's options API tier, IBKR, or a dedicated options data vendor).
- **Execution:** broker API with native multi-leg/spread order support if the strategy uses collars or spreads, plus the same stop/stop-limit infrastructure from Phase 2 for the underlying-equity portion.
- **Compliance/margin:** logic to read the account's current margin/intraday-margin-deficit state directly from the broker (guardrails doc, Section 4.1) — with the 2026 FINRA rule replacing the old PDT framework, the agent must check its *specific broker's* current implementation (old $25k/4-trade rule vs. new IMD regime) rather than hard-coding either assumption.
- **News NLP:** if building genuine headline classification, a low-latency news API plus a lightweight sentiment/relevance classifier; otherwise, rely on curated event calendars (FOMC, CPI, jobs reports) which are fully scheduled and require no NLP.

### Skills
1. Price and manage covered-call / cash-secured-put positions: select strikes by delta (~30-delta per the rule-set), track assignment risk, and execute rolls.
2. Read and act on the account's live margin/day-trade-count (or IMD) state before submitting any order that could trigger a restriction.
3. Classify or route incoming news/events to decide "ignore," "flatten," or "act" — with "ignore" as the safe default for anything not matching a pre-defined, tested pattern.
4. Enforce outbound message-rate throttling (guardrails doc, Section 3.2) to prevent a logic bug from generating runaway order submissions — this risk is highest at this phase given the higher trigger frequency.

### Guardrails specific to this phase (the strictest tier)
- **All Phase 1 and Phase 2 guardrails still apply and are not relaxed.**
- Full kill-switch wiring to automated triggers (data staleness, broker API error spikes, VIX spikes, MWCB Level 1+, LULD pauses) — this is the phase where an unattended failure could compound fastest (guardrails doc, Sections 3.3-3.4).
- Mandatory pilot-phase rollout at minimal size before scaling (FINRA 15-09 pattern, guardrails doc Section 3.2) — never deploy a new Phase 3 strategy at target size on day one.
- Idempotent order submission and startup reconciliation against the broker's live open-orders endpoint are now essential, not optional, given trade frequency (guardrails doc, Section 5.4).
- Wash-sale tagging (guardrails doc, Section 4.2) becomes practically important at this phase since short-term round trips in the same name are common.
- Human sign-off required for any change that loosens a risk-control parameter, permanently (guardrails doc, Section 3.5) — the agent must never be able to widen its own guardrails autonomously, regardless of phase.

---

## 4. Cross-phase summary table

| | Phase 1: Long-term | Phase 2: Swing | Phase 3: Active/short-term |
|---|---|---|---|
| Typical hold | Months | Days-weeks | Intraday-days |
| Core signal | 200-SMA trend, multi-factor tilts | Sector momentum, PEAD | Options premium, fast news |
| Data cadence | Daily close | Daily + earnings calendar | Real-time/streaming + options chain |
| Order types | Market/limit, monthly | + Stop/stop-limit | + Multi-leg options |
| Sizing | Fixed-fractional target weights | + ATR-based per-trade | + Options-delta-based |
| New regulatory surface | None beyond standard brokerage | Earnings blackout | Margin/IMD, wash sale, options assignment |
| Kill-switch criticality | Low (monthly cadence self-limits damage) | Medium | High (fully automated) |

## 5. Build sequencing recommendation

1. Build and paper-trade Phase 1 end-to-end first, including its guardrails and audit logging, before writing a single line of Phase 2 code — this validates the broker-agnostic execution layer and event-driven loop on the lowest-risk, lowest-frequency strategy.
2. Only add Phase 2's stop/ATR/earnings-calendar machinery once Phase 1 has run cleanly in paper trading for a meaningful trial period (guardrails doc, Section 7, item 2).
3. Defer Phase 3 (options, fast news) until Phase 2 has demonstrated stable guardrail behavior — options assignment risk and margin mechanics are the most operationally complex parts of this whole system and should not be a first project.

**Sources:** synthesized from [`guardrails_architecture.md`](../research/guardrails_architecture.md), [`momentum_trend_following.md`](../research/momentum_trend_following.md), [`factor_investing.md`](../research/factor_investing.md), [`event_news_driven.md`](../research/event_news_driven.md), [`options_overlays.md`](../research/options_overlays.md), and [`us_market_mechanics.md`](../research/us_market_mechanics.md) — see those files for primary-source citations underlying each claim.
