# US Equity Market Mechanics — Core Reference

*Foundational knowledge base module. Every trading agent decision (order type, timing, risk check) depends on understanding this plumbing.*

## 1. Market structure: exchanges and where trades actually happen

US equities do not trade on one exchange — trading is fragmented across 16+ registered exchanges plus dozens of off-exchange venues, all linked by the National Market System (NMS), which requires trades to be routed to whichever venue shows the best price ([Nasdaq, "The 2026 Intern's Guide to the Market Structure Galaxy"](https://www.nasdaq.com/articles/2026-interns-guide-market-structure-galaxy)).

- **Listing venue vs. execution venue are different things.** A stock is *listed* on NYSE or Nasdaq, but the actual trade can execute on any exchange or dark pool. Consolidated tape reporting still attributes it to the listing tape: Tape A = NYSE-listed, Tape C = Nasdaq-listed, Tape B = everything else (Cboe exchanges, NYSE Arca — mostly ETFs, NYSE American — small caps) ([Nasdaq market structure guide](https://www.nasdaq.com/articles/2026-interns-guide-market-structure-galaxy)).
- **Dark pools and ATSs.** Alternative Trading Systems (ATSs), including dark pools, let institutions trade large blocks without displaying orders publicly pre-trade — trade prices are only reported after execution. Dark pools account for roughly 40% of US equity trading volume as of the mid-2020s ([TradeAlgo, "What Are Dark Pools?"](https://www.tradealgo.com/trading-guides/tools/what-are-dark-pools-definition-history-and-how-they-work-in-2026); [Congressional Research Service, "Dark Pools in Equity Trading"](https://www.congress.gov/crs-product/R43739)). Implication for a retail agent: displayed order-book liquidity understates true market depth, and large orders you place may get partially filled off-exchange without you seeing that liquidity beforehand.
- **Maker-taker pricing.** Exchanges pay rebates to "makers" (limit orders that add liquidity) and charge fees to "takers" (orders that remove liquidity), which shapes order routing incentives at the broker level — largely invisible to a retail trader but explains why brokers route orders where they do ([Market Data News, exchange fee models](https://marketdatanews.com/market-structure)).

## 2. Trading hours

- **Regular session:** 9:30 a.m. – 4:00 p.m. Eastern Time, Monday–Friday (excluding market holidays) ([Charles Schwab, circuit breakers explainer](https://www.schwab.com/learn/story/what-are-stock-market-circuit-breakers)).
- **Extended hours:** Pre-market (as early as 4:00 a.m. ET) and after-hours (until 8:00 p.m. ET) trading exist on ECNs/exchanges but with materially lower liquidity and wider spreads — a news/event-driven agent must treat extended-hours price action as a weaker, higher-slippage signal than regular-session action ([TradeZero fee schedule showing 4 AM–8 PM windows](https://tradezero.com/documents/ecdb187e037e0806eb19036c138e99b5b3cadeb5.pdf)).

## 3. Order types (the vocabulary your agent's execution layer must speak)

Every order carries: ticker, side (buy/sell), size, and price instruction ([Nasdaq, "The 2026 Intern's Guide to Trading"](https://www.nasdaq.com/articles/2026-interns-guide-trading)).

- **Market order:** execute immediately at the best available price. Guarantees fill, not price — dangerous in low-liquidity names or fast-moving news events (slippage risk).
- **Limit order:** execute only at a specified price or better. Guarantees price, not fill.
- **Stop order / stop-loss:** becomes a market order once a trigger price is touched — the mechanical backbone of automated risk control.
- **Stop-limit:** becomes a limit order (not market) once triggered — avoids market-order slippage but can fail to fill in a fast gap-down.
- **Non-displayed / hidden orders:** rest in the book without showing size, used to reduce market impact for larger orders ([Nasdaq market structure guide](https://www.nasdaq.com/articles/2026-interns-guide-market-structure-galaxy)).

## 4. Settlement cycle: T+1

Since **May 28, 2024**, the standard US equity settlement cycle is **T+1** (trade date plus one business day), down from the previous T+2, per SEC Rule 15c6-1(a) adopted February 15, 2023 ([SEC, "Shortening the Securities Transaction Settlement Cycle"](https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq); [Investor.gov bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin)). Canada moved to T+1 the same week. A further global move toward T+1 in the UK/EU/Switzerland is targeted for **October 11, 2027** ([ICMA Group](https://www.icmagroup.org/market-practice-and-regulatory-policy/repo-and-collateral-markets/t-1-the-shortening-of-standard-settlement-cycles/)).

**Why this matters for an agent:** cash from a sale is available to reinvest one business day sooner than under the old regime, which slightly improves capital efficiency for active rebalancing strategies, and reduces (but does not eliminate) settlement-related constraints on same-day round trips in a cash account.

## 5. Market-wide circuit breakers (crash protection at the exchange level)

Since April 2013 (Rule 80B, amended), market-wide circuit breakers trigger off **intraday percentage declines in the S&P 500** relative to the prior day's close, in three tiers ([Investopedia, "Circuit Breaker"](https://www.investopedia.com/terms/c/circuitbreaker.asp); [Investor.gov glossary](https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers)):

| Level | Decline trigger | Effect | Time restriction |
|---|---|---|---|
| Level 1 | −7% | 15-minute halt, all US exchanges | No halt if triggered at/after 3:25 p.m. ET |
| Level 2 | −13% | Another 15-minute halt | No halt if triggered at/after 3:25 p.m. ET |
| Level 3 | −20% | Trading closes for the remainder of the day | Applies any time during the session |

This is a hard, mechanical **kill-switch precedent already built into US market infrastructure** — a trading agent's own guardrails should mirror this logic at the portfolio level (see the guardrails knowledge base module).

## 6. Regulatory bodies your agent operates under

- **SEC (Securities and Exchange Commission):** federal regulator of securities markets, exchanges, and broker-dealers.
- **FINRA (Financial Industry Regulatory Authority):** self-regulatory organization overseeing broker-dealers; sets rules like the historical Pattern Day Trader rule (see the guardrails module for the 2026 replacement framework).
- **DTCC/DTC (Depository Trust Company):** clearing and settlement infrastructure underlying the T+1 cycle.

## 7. What actually moves prices (the news-reaction backbone)

Prices move on new information relative to what was already expected — not on the news itself in isolation. This is why:
- A company can report record profits and the stock can fall (if results missed *expectations*).
- Scheduled macro events (FOMC decisions, CPI, jobs reports) concentrate a disproportionate share of average market returns into short windows around the release — see the event/news-driven research module for the quantitative evidence.
- Reaction speed matters enormously: institutional/HFT participants react within milliseconds to seconds of a release; a retail-speed agent (seconds to minutes of latency) cannot compete on raw speed and should instead focus on strategies that resolve over hours/days/weeks (drift, not the initial tick).

## 8. Practical implications for this agent's design

1. Treat displayed liquidity as a floor, not the whole picture (dark pool activity is invisible pre-trade).
2. Default to limit orders for entries in anything but the most liquid large-caps/ETFs; reserve market orders for exits where guaranteed execution matters more than price.
3. Build stop-loss logic on stop or stop-limit orders, understanding stop-limit can fail to fill during a gap.
4. Respect T+1 cash settlement timing in any backtest-to-live capital allocation logic.
5. Mirror the exchange's own circuit-breaker philosophy at the strategy level: pre-defined, automatic, non-negotiable halt thresholds (see guardrails module).
6. Because the agent cannot win a latency race against institutional players, its event-driven edge (if any) should target drift/multi-day reaction windows, not the first few seconds after a headline.

**Sources:** [Nasdaq — Market Structure Galaxy Guide](https://www.nasdaq.com/articles/2026-interns-guide-market-structure-galaxy) · [Nasdaq — Guide to Trading](https://www.nasdaq.com/articles/2026-interns-guide-trading) · [SEC T+1 FAQ](https://www.sec.gov/exams/educationhelpguidesfaqs/t1-faq) · [Investor.gov T+1 Bulletin](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin) · [Investopedia — Circuit Breakers](https://www.investopedia.com/terms/c/circuitbreaker.asp) · [Investor.gov — Circuit Breakers Glossary](https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers) · [Schwab — Circuit Breakers Explained](https://www.schwab.com/learn/story/what-are-stock-market-circuit-breakers) · [TradeAlgo — Dark Pools](https://www.tradealgo.com/trading-guides/tools/what-are-dark-pools-definition-history-and-how-they-work-in-2026) · [CRS — Dark Pools Report](https://www.congress.gov/crs-product/R43739) · [Market Data News — Exchange Fee Models](https://marketdatanews.com/market-structure) · [ICMA — T+1 Global Rollout](https://www.icmagroup.org/market-practice-and-regulatory-policy/repo-and-collateral-markets/t-1-the-shortening-of-standard-settlement-cycles/)
