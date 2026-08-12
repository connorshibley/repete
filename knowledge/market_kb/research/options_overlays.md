# Options Overlay Strategies on an Equity Portfolio: A Risk-Adjusted-Return Assessment vs. S&P 500 Buy-and-Hold

*Research report — August 2026*

## Executive summary

Options overlays — covered calls, cash-secured puts, protective puts, and collars — are widely marketed as ways to "enhance income" or "manage risk" on an equity portfolio. Roughly four decades of Cboe benchmark-index data and a deep academic literature support a consistent, nuanced conclusion:

- **Covered call writing (BXM, BXN)** reliably cuts volatility and drawdowns by roughly 25–35% relative to the underlying index, and has occasionally produced comparable or better *risk-adjusted* (Sharpe) returns over multi-decade samples — but it structurally caps upside and **underperforms badly in strong, sustained bull markets**, which is exactly the regime the U.S. equity market has spent much of the last 15 years in ([Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf); [Cboe BXN quick reference guide](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf)).
- **Put-writing (PUT index)** has, surprisingly, matched or nearly matched the S&P 500's absolute compound return since 1986 while running roughly one-third less volatility and a materially shallower max drawdown, giving it a *better* long-run Sharpe ratio than the S&P 500 itself in Cboe/academic samples — one of the more genuinely attractive risk-adjusted results in the options-overlay literature ([Bondarenko, Cboe, "Historical Performance of Put-Writing Strategies"](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)).
- **Protective puts and collars** are insurance: they reliably shrink drawdowns in crashes but, priced with a persistent volatility risk premium, drag down long-run compounded return — often more than proportionally to the risk they remove. Independent academic work (AQR, CXO Advisory, and a 2026 *Financial Analysts Journal* study) is unusually blunt that systematic put-buying is a poor long-run risk-adjusted trade versus simply holding less equity ([AQR, "Risk and Return of Equity Index Collar Strategies"](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Risk-and-Return.pdf?sc_lang=en); [Israelov, "Pathetic Protection"](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2934538_code437885.pdf?abstractid=2934538&mirid=1); [Baltussen, Martens & van der Linden via Evidence Investor](https://www.evidenceinvestor.com/post/portfolio-insurance-crash-protection)).
- **Live covered-call ETFs (QYLD, JEPI)** confirm the theory in real-world, fee-and-slippage-inclusive form: both have paid large distributions but badly lagged their underlying indices' total return since inception, especially in the 2023–2025 AI-driven rally ([Global X QYLD fact sheet](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf); [J.P. Morgan JEPI fact sheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/FS-JEPI.PDF); [Morningstar JEPI analysis](https://www.morningstar.com/etfs/arcx/jepi/quote)).
- **LEAPS as stock replacement** trade capital efficiency and defined risk for time decay and reduced dividend capture; they are a leverage/capital-allocation tool, not a risk-reduction tool.
- The most defensible use of options in a systematic strategy is **not** as a persistent income enhancer but as a **tactical risk-management guardrail** — sized modestly, deployed selectively (e.g., in elevated-valuation or high-volatility regimes), and evaluated on drawdown mitigation rather than raw return.

---

## 1. Covered call writing — mechanism and long-run track record

### 1.1 Mechanism

A covered call ("buy-write") strategy holds a long equity position and sells (writes) a call option against it, typically out-of-the-money (OTM) or at-the-money (ATM), collecting premium in exchange for capping upside above the strike. If the underlying stays below the strike at expiration, the writer keeps the full premium plus the stock; if it rises above the strike, the writer's gain is capped at strike + premium, and the stock is often called away or the position must be rolled.

### 1.2 Cboe S&P 500 BuyWrite Index (BXM)

The **Cboe S&P 500 BuyWrite Index (BXM)** is the benchmark for this strategy on the S&P 500. It holds a long S&P 500 position and sells a near-month, at-the-money SPX call each month, held to expiration and cash-settled, with a new ATM call written immediately after ([Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)).

Cboe's own official performance statistics, calculated from June 20, 1986 through June 30, 2025 ([Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)):

| Metric | BXM (Covered Call) | S&P 500 |
|---|---:|---:|
| Annualized return | 8.3% | 10.9% |
| Annualized volatility | 10.8% | 15.3% |
| Maximum drawdown | −35.8% | −50.9% |
| Beta | 0.62 | 1.00 |
| Sharpe ratio | 0.52 | 0.54 |
| Sortino ratio | 0.71 | 0.81 |

Note that in this most recent Cboe update, the S&P 500's Sharpe ratio (0.54) very slightly *exceeds* BXM's (0.52) — a reversal from the pattern found in earlier academic samples (see §1.3) and a reminder that the strategy's relative attractiveness is highly period-dependent, largely because of how much of the sample includes the post-2009 mega-cap-driven bull market.

Calendar-year returns make the "cap during rallies" dynamic explicit ([Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)):

| Year | BXM | S&P 500 |
|---|---:|---:|
| 2013 | 13.3% | 32.4% |
| 2019 | 15.7% | 31.5% |
| 2021 | 20.5% | 28.7% |
| 2023 | 11.8% | 26.3% |
| 2024 | 20.1% | 25.0% |
| 2022 (down year) | −11.4% | −18.1% |
| 2018 (down year) | −4.8% | −4.4% |

BXM outperformed the S&P 500 only in flat/down years (2018, 2022); in every strong bull year shown, BXM captured roughly 40–65% of the index's gain. This is the well-documented asymmetry of covered calls: **volatility and drawdown reduction is real and persistent, but the strategy structurally forfeits a large share of upside in strong bull markets.**

Schwab's summary of the same Cboe data over the full 1986–2023 window states it plainly: BXM's annualized return was 8.2% vs. 10.5% for the S&P 500, with volatility "almost 30% lower" and a worst drawdown of 35.8% vs. 51% ([Schwab, "Covered Calls: Beyond the Basics"](https://www.schwab.com/learn/story/covered-calls-beyond-basics)).

### 1.3 Academic verification (Whaley, Feldman & Roy)

The BXM index itself was created at Cboe's commission by Prof. Robert Whaley. His original study (data June 1988–December 2001) found the S&P 500 had a mean monthly return of 1.187% (std dev 4.103%) vs. BXM's 1.106% (std dev 2.663%) — i.e., BXM gave up only ~8 basis points of monthly return while cutting risk by roughly a third, producing risk-adjusted outperformance on the order of 0.2%/month ([Whaley, "Return and Risk of CBOE Buy Write Monthly Index"](https://www.whaley.info/_files/ugd/1362e1_28ef09f741b04464b1ab570210992fbd.pdf)). A follow-up study extending the sample to nearly 16 years found BXM's monthly Sharpe ratio of 0.225 vs. 0.159 for the S&P 500 and 0.121 for the Russell 2000 — a 42% risk-adjusted performance advantage over the S&P 500 in that sample ([Feldman & Roy, PM Research](https://www.pm-research.com/content/iijinvest:::14:::2:::66.full.pdf)). These findings, however, are sample-dependent: as shown above, Cboe's current 1986–2025 statistics put BXM's Sharpe ratio slightly *below* the S&P 500's, illustrating that "covered calls improve Sharpe ratio" is not an unconditional truth — it depends heavily on which market regimes dominate the sample window.

### 1.4 Cboe Nasdaq-100 BuyWrite Index (BXN)

The **Cboe Nasdaq-100 BuyWrite Index (BXN)** applies the identical mechanism to the Nasdaq-100 (NDX): hold NDX, sell a near-month, near-the-money NDX call each month ([Cboe BXN quick reference guide](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf); [Cboe Nasdaq BuyWrite Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_NASDAQ_BuyWrite_Indices_Methodology.pdf)). The index launched September 9, 2005 with history back to December 30, 1994 ([Nasdaq BXN overview](https://indexes.nasdaq.com/Index/Overview/BXN)).

Cboe's quick-reference data (September 2002–October 2013) ([Cboe BXN QRG](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf)):

| Metric | BXN | 30-Yr Treasury (Citi) | S&P GSCI |
|---|---:|---:|---:|
| Annualized return | 7.5% | 5.5% | 2.5% |
| Standard deviation | 13.4% | 14.9% | 24.2% |

Over that same 10-year window, BXN's volatility ran roughly 30% below NDX's, and Cboe's own commentary is explicit about the asymmetric payoff: "Stock indexes can outperform buy-write indexes during strong bull markets for stocks… Buy-write indexes have the potential to outperform stock indexes during bear markets or range-bound stock markets" ([Cboe BXN QRG](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf)). Average gross monthly premium collected was about 2.3% of the underlying (Jan 2008–Oct 2013).

The 2013–2025 record makes the bull-market drag unmistakable: since QYLD's inception (an ETF tracking a variant of BXN — see §4), the Nasdaq-100 has compounded at roughly 18.9% annualized vs. ~8.3% for the covered-call NAV over the same period ([Global X QYLD fact sheet/presentation](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf)) — a gap of more than 10 percentage points per year sustained for over a decade, driven by the historic concentration of mega-cap tech gains that a monthly at-the-money call systematically forfeits.

---

## 2. Cash-secured puts / put-writing — the Cboe PUT Index

### 2.1 Mechanism

A cash-secured put-write strategy sells (writes) a put option collateralized by cash or T-bills equal to the exercise value, collecting premium. If the underlying stays above the strike, the writer keeps the premium; if it falls below, the writer is obligated to buy the underlying at the strike, effectively "buying the dip" at a discount equal to the premium received. Economically, by put-call parity, a fully collateralized put-write on an index is nearly equivalent to a covered call at the same strike, but it is structured, marketed, and psychologically experienced differently (starting in cash rather than starting long stock).

### 2.2 The Cboe S&P 500 PutWrite Index (PUT)

The **Cboe S&P 500 PutWrite Index (PUT)** tracks a strategy that sells a monthly at-the-money SPX put and holds the remaining collateral in T-bills, rolling on the standard monthly expiration ([Cboe/Bondarenko, "Historical Performance of Put-Writing Strategies"](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)). Price history dates back to June 30, 1986; the index was formally launched in 2007.

This is the standout finding of the entire options-overlay literature. Over the 32.5-year period June 30, 1986–December 31, 2018 ([Bondarenko/Cboe research paper](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)):

| Metric | PUT | S&P 500 |
|---|---:|---:|
| Compound annual return | 9.54% | 9.80% |
| Standard deviation | 9.95% | 14.93% |
| Sharpe ratio | 0.65 | 0.49 |
| Sortino ratio | 0.85 | 0.70 |
| Maximum drawdown | −32.7% | −50.9% |
| Cumulative return (growth of $1) | $19.35 (1,835%) | $20.85 |

PUT nearly matched the S&P 500's absolute compound return (9.54% vs. 9.80%) while running about one-third less volatility, giving it a materially higher Sharpe ratio — 0.65 vs. 0.49. Its maximum drawdown (−32.7%) was far shallower than the S&P 500's (−50.9%), and its longest drawdown was also shorter.

Cboe's own official PUT fact sheet, updated through more recent data, corroborates the pattern from January 3, 2007 forward ([Cboe PUT Index fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_PUT-Index.pdf)):

| Metric | PUT | S&P 500 |
|---|---:|---:|
| Annualized return | 6.4% | 10.4% |
| Annualized volatility | 11.0% | 15.6% |
| Maximum drawdown | −32.7% | −50.9% |
| Beta | 0.61 | 1.00 |
| Sharpe ratio | 0.46 | 0.58 |
| Sortino ratio | 0.60 | 0.85 |

Consistent with the covered-call findings, PUT's relative Sharpe-ratio advantage is regime-dependent: it looked much stronger through the 1986–2018 sample (which includes the 1987 crash, dot-com bust, and 2008 GFC) than through the 2007–2025 sample (dominated more by a long, low-volatility bull run). Both samples agree on the structural point: **PUT consistently delivers roughly a third less volatility and a much shallower max drawdown than the S&P 500, at the cost of giving up some absolute return in strong bull markets.**

### 2.3 One-Week PutWrite (WPUT) and premium economics

A shorter-dated variant, the **Cboe S&P 500 One-Week PutWrite Index (WPUT)**, sells one-week ATM puts 52 times a year rather than monthly. Over January 2006–December 2018, WPUT collected larger aggregate annual gross premium (37.1% average) than PUT (22.1% average) because of higher trading frequency, but delivered lower compound annual return (4.51% vs. 5.97% for PUT) and a materially shallower max drawdown (−24.2% vs. −32.7%) over that sub-period ([Bondarenko/Cboe research paper](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)). Gross premium collected is a headline number, not a return — both strategies had periods of negative net returns despite positive gross premium.

---

## 3. Protective puts and collars — the cost of insurance

### 3.1 Mechanism and honest framing

A **protective put** buys an OTM put against a long equity position, paying a premium in exchange for a floor on losses below the strike. A **collar** finances that put purchase (in whole or part) by simultaneously selling an OTM call, capping upside in exchange for reducing or eliminating the net cost of the put. Both strategies are best understood as **insurance products**, and — like most insurance — they have negative expected value in isolation; they are a rational purchase only when the utility of loss avoidance (or a specific liquidity/mandate constraint) outweighs the average cost.

### 3.2 Cboe protective put and collar indices

The **Cboe S&P 500 5% Put Protection Index (PPUT)** buys a 5% OTM monthly SPX put against a long S&P 500 position. Over June 1986–December 2018, PPUT's cumulative return was 708% (growth of $1 to $8.08) versus 1,835% for PUT and roughly comparable for the S&P 500's outright return, with a compound annual return of 6.64% vs. 9.80% for the S&P 500 and 9.54% for PUT ([Bondarenko/Cboe research paper](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)). Its Sharpe ratio (0.33) was materially below both PUT's (0.65) and the S&P 500's (0.49) over the same period — the clearest evidence in Cboe's own dataset that buying downside insurance is a return drag over the long run, even though PPUT's maximum drawdown (−38.9%) was less severe than the S&P 500's (−50.9%).

The **Cboe S&P 500 95-110 Collar Index (CLL)** holds the S&P 500, buys a 5% OTM quarterly put, and sells a 10% OTM monthly call, rolling at each SPX expiration ([Cboe Collar Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_Collar_Indices_Methodology.pdf)). An older Cboe-commissioned study (Asset Consulting Group, through ~2010) found total growth since mid-1986 of 368% (6.2% annualized) for CLL vs. 807% (9.0% annualized) for the S&P 500, with CLL's and the S&P 500's volatility differing by about 30%, and CLL's worst monthly loss (−8.0%) far smaller than the S&P 500's (−16.8%) ([Asset Consulting Group / Cboe](https://cdn.cboe.com/resources/indices/documents/pap-assetconsultinggroup-cboe-feb2012.pdf)).

### 3.3 Academic verdict: collars structurally underperform, largely because of the volatility risk premium

AQR's peer-reviewed analysis of CLL over July 1986–December 2014 quantifies the drag precisely ([AQR, "Risk and Return of Equity Index Collar Strategies"](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Risk-and-Return.pdf?sc_lang=en)):

| Metric | CLL (Collar) | S&P 500 |
|---|---:|---:|
| Excess return (annualized) | 3.2% | 7.3% |
| Volatility (annualized) | 10.7% | 15.7% |
| Sharpe ratio | ~35% lower than S&P 500 | baseline |
| Beta | 0.62 | 1.00 |

AQR's key finding: because CLL's beta is 0.62, its equity-risk-premium capture alone would predict an excess return of about 4.5% (0.62 × 7.3%); its *actual* excess return of 3.2% implies an annualized loss of roughly 1.3% attributable specifically to the **volatility risk premium** — the fact that the OTM put purchased is systematically more expensive (in implied-vol terms) than the OTM call sold. AQR's blunt conclusion: "Black swans would have to be unreasonably frequent to make CLL competitive with the S&P 500 Index" — specifically, one black-swan event roughly every 6.6 years would be required for parity ([AQR](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Risk-and-Return.pdf?sc_lang=en)). CXO Advisory's independent replication of the same dataset reaches an identical conclusion ([CXO Advisory, "Equity Index Collar Performance"](https://www.cxoadvisory.com/equity-options/equity-index-collar-performance/)).

An even more pointed critique comes from Roni Israelov's *Journal of Alternative Investments* paper, "Pathetic Protection: The Elusive Benefits of Protective Puts." Using both the Cboe PPUT index and Monte Carlo simulation, Israelov finds that **static equity divestment (simply holding less stock) produces better peak-to-trough drawdown outcomes per unit of expected return than buying put protection — 97% of the time over short horizons and 100% of the time over longer horizons** in his tests, particularly once a realistic volatility risk premium is priced into the puts ([Israelov, "Pathetic Protection"](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2934538_code437885.pdf?abstractid=2934538&mirid=1); [Alpha Architect summary](https://alphaarchitect.com/pathetic-protection/)).

A 2026 *Financial Analysts Journal* study spanning 222 years of market data (Baltussen, Martens & van der Linden) reinforces the point in a modern, high-profile venue: a monthly 5%-OTM SPX put overlay, sized to 5% volatility, returned **−2.5% per year** from mid-1986 through 2021 once compared on a risk-scaled basis to other defensive strategies (multi-asset trend, quality stocks, low-risk stocks). The put overlay lost an average of 0.54% in up months — the regime that occurs most often — and while it did deliver a cumulative 13.7% gain across the deepest (>20%) drawdowns since 1986, it ranked only fifth-best among defensive strategies once shallower, more common drawdowns (>2%) were included ([Evidence Investor summary of Baltussen, Martens & van der Linden, *Financial Analysts Journal* 2026](https://www.evidenceinvestor.com/post/portfolio-insurance-crash-protection)).

**Bottom line on insurance strategies:** protective puts and collars do what insurance is supposed to do — they meaningfully shrink drawdowns during crashes (the 2008 and 2020 episodes are the clearest examples in the Cboe data). But priced options carry a persistent volatility risk premium, so systematically holding this insurance is a long-run performance drag that, per multiple independent academic sources, is larger than the risk-adjusted benefit for most investors — a straightforward reduction in equity exposure is a more capital-efficient way to achieve the same volatility target.

---

## 4. Live covered-call ETF track records: QYLD and JEPI vs. their benchmarks

Theoretical index math is one thing; real, fee-bearing, tradable products are the test that matters for practitioners.

### 4.1 Global X Nasdaq 100 Covered Call ETF (QYLD)

QYLD (inception December 11, 2013) tracks the Cboe Nasdaq-100 BuyWrite V2 Index, selling monthly at-the-money NDX calls against a full Nasdaq-100 replicating portfolio ([Global X QYLD presentation](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf); [StockAnalysis.com QYLD profile](https://stockanalysis.com/etf/qyld/)).

Global X's own Q4 2024 fact sheet shows the magnitude of the upside cap ([Global X QYLD presentation](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf)):

| Period (annualized where >1yr) | QYLD (NAV) | Nasdaq-100 |
|---|---:|---:|
| 1 Year | 19.13% | 25.80% |
| 3 Years | 5.82% | 9.70% |
| 5 Years | 7.30% | 20.15% |
| 10 Years | 8.57% | 18.51% |
| Since inception | 8.26% | 18.87% |

A gap of roughly 10.6 percentage points per year, annualized, since inception. Independent trackers confirm the magnitude of cumulative divergence: since December 12, 2013, QYLD's total return (dividends reinvested) is up about 178–189% cumulatively, while the Invesco QQQ Trust (tracking the same underlying Nasdaq-100) is up roughly 512–737% over comparable windows ([Yahoo Finance/Zacks coverage of QYLD](https://finance.yahoo.com/markets/stocks/articles/qyld-12-yield-looks-generous-191821678.html); [Total Real Returns QYLD tracker](https://totalrealreturns.com/n/QYLD); [Seeking Alpha](https://seekingalpha.com/article/4749654-qyld-tech-exposure-with-a-double-digit-yield)).

QYLD's drawdown mitigation, however, is real and consistent with theory ([Global X QYLD presentation](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf)):

| Drawdown episode | QYLD | Nasdaq-100 |
|---|---:|---:|
| Dec 2015–Feb 2016 | −9.20% | −12.13% |
| Aug–Dec 2018 | −18.02% | −21.80% |
| Feb–Mar 2020 (COVID crash) | −23.81% | −27.10% |
| 2022 (full-year bear) | −20.61% | −34.00% |
| Jul–Oct 2023 | −6.01% | −10.74% |
| Jul–Aug 2024 | −7.51% | −12.62% |

QYLD's per-drawdown cushioning (typically 3–13 percentage points shallower) is consistent and real, but the cumulative cost of the capped upside in the intervening bull-market stretches has been an order of magnitude larger than the drawdown savings — the central empirical lesson of the entire covered-call category.

### 4.2 JPMorgan Equity Premium Income ETF (JEPI)

JEPI (inception May 20, 2020) is actively managed: it holds a lower-beta equity sleeve (roughly 0.20–0.60 beta reported across sources) and sells S&P 500-linked call exposure via equity-linked notes (ELNs) rather than writing exchange-listed options directly on its own holdings ([J.P. Morgan JEPI fact sheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/FS-JEPI.PDF)).

J.P. Morgan's own fact sheet (data through June 30, 2026) shows the since-inception gap versus the S&P 500 benchmark ([J.P. Morgan JEPI fact sheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/FS-JEPI.PDF)):

| Period (annualized) | JEPI (NAV) | S&P 500 Index |
|---|---:|---:|
| 1 Year | 7.77% | 22.32% |
| 3 Years | 8.99% | 20.61% |
| 5 Years | 7.47% | 13.41% |
| Since inception (5/20/20) | 11.03% | 18.07% |

Calendar-year returns show JEPI's defensive character clearly ([J.P. Morgan JEPI fact sheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/FS-JEPI.PDF)):

| Year | JEPI (NAV) | S&P 500 |
|---|---:|---:|
| 2021 | 21.61% | 28.71% |
| 2022 | −3.54% | −18.11% |
| 2023 | 9.88% | 26.29% |
| 2024 | 12.56% | 25.02% |
| 2025 | 8.07% | 17.88% |

JEPI beat the S&P 500 by roughly 14.6 percentage points in the 2022 bear market (−3.54% vs. −18.11%) but lagged by 14–17 points in every subsequent bull year. Morningstar's analyst note independently corroborates this pattern and adds important nuance: JEPI's defensive stock *sleeve itself* (separate from the option overlay) outpaced the S&P 500 by 10.9 percentage points in 2022, and the fund beat the index by 14.2 and 3.5 percentage points respectively during the 2022 meltdown and the Feb–Apr 2025 volatility spike — but its capped upside caused it to lag the index by over 14 percentage points during the May–September 2025 rebound ([Morningstar, "JEPI" analysis](https://www.morningstar.com/etfs/arcx/jepi/quote)). Morningstar rates the fund Gold-medalist largely on the basis of its low fee (0.35% vs. a 0.96% category median) rather than on raw return capture ([Morningstar JEPI fact sheet analysis](https://www.morningstar.com/etfs/arcx/jepi/quote)).

Independent commentary is consistent: over five years, JEPI's cumulative total return trails SPY's by a wide margin (roughly 43% vs. 73% in one contemporaneous comparison) ([Yahoo Finance, "JEPI Is Falling While the S&P 500 Soars"](https://finance.yahoo.com/markets/stocks/articles/jepi-falling-while-p-500-190551045.html)).

### 4.3 Category-wide pattern

The Derivative Income ETF category (QYLD, JEPI, XYLD, JEPQ, and peers) as a group shows the identical structural pattern relative to their respective total-return benchmarks: materially higher current income, materially lower volatility and beta, materially shallower bear-market drawdowns, and materially lower compounded total return over any multi-year bull-market-inclusive window ([YCharts JEPI comparison](https://ycharts.com/companies/JEPI); [Schwab QYLD performance sheet](https://www.schwab.wallst.com/Prospect/Research/mutualfunds/performance.asp?symbol=qyld)).

---

## 5. LEAPS and long-dated call replacement as a leveraged, defined-risk alternative to shares

Long-Term Equity AnticiPation Securities (LEAPS) are simply listed options with more than roughly nine months to a year until expiration. The "stock replacement" strategy buys a deep in-the-money LEAPS call (delta ≈ 0.70–0.85) instead of the underlying shares, to gain most of the directional exposure for a fraction of the capital outlay ([Option Agent, "Using Long-Dated Calls as Stock Replacement"](https://theoptionagent.com/learn/lessons/leaps-options-explained?series=reading-the-market); [Ainvest, "LEAPS Options: The Long-Term Options Strategy Guide"](https://optionpilot.ainvest.com/blog/leaps-long-term-options-guide)).

**Mechanics and trade-offs:**
- A deep ITM LEAPS call with delta 0.80 moves roughly $0.80 for every $1 the underlying moves — capturing about 80% of directional exposure for typically 30–40% of the capital of outright share ownership ([GEX Levels, "LEAPS Options Strategy Explained"](https://gex-levels.com/blog/options-leaps-strategy)).
- Maximum loss is defined and limited to the premium paid — a genuine advantage over margined stock or futures leverage, which carries margin-call risk.
- LEAPS forgo dividends (a material drag for dividend-paying names) and are subject to time decay (theta), which is minimal with 18+ months remaining but accelerates sharply inside the final 90 days; practitioners generally roll positions once roughly 6–9 months remain to avoid the steep terminal decay ([Option Agent](https://theoptionagent.com/learn/lessons/leaps-options-explained?series=reading-the-market); [Ainvest](https://optionpilot.ainvest.com/blog/leaps-long-term-options-guide)).
- The embedded leverage cuts both ways: in a sustained downturn, LEAPS calls lose value faster (in percentage terms) than the underlying shares. An empirical study of 54 NYSE/NASDAQ stocks and their LEAPS calls over 2008–2010 explicitly found that "LEAPS calls are not a preferred financial instrument to replace common stocks for risk-averse traders," because in a progressive market downturn the LEAPS portfolios produced significantly higher losses, volatility, and worse risk-adjusted performance than the equivalent stock portfolios ([SSRN, "Equity LEAPS Calls vs. Stocks: An Empirical Study for Long-Term Speculation"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1919066)). The same paper found risk-*seeking* traders willing to tolerate the added volatility could benefit in favorable market conditions, particularly with high book-to-market underlying stocks.
- LEAPS also form the long leg of a "poor man's covered call" (PMCC) — buying a deep ITM LEAPS as a stock substitute and selling short-dated OTM calls against it monthly, capturing most of the covered-call income yield at a fraction of the capital commitment ([GEX Levels](https://gex-levels.com/blog/options-leaps-strategy); [Option Agent](https://theoptionagent.com/learn/lessons/leaps-options-explained?series=reading-the-market)).

**Assessment:** LEAPS stock replacement is best understood as a **capital-efficiency and leverage tool**, not a risk-reduction tool. It frees capital for other deployment while retaining most of the upside exposure to a bullish thesis, but it is not defensive — the defined-risk feature caps the *dollar* loss at premium paid, but the *percentage* volatility of the position is typically higher than the underlying shares, and the strategy underperforms outright ownership in flat or declining markets due to time decay and forgone dividends.

---

## 6. Options as a risk-management guardrail within a systematic strategy

The evidence above supports a specific, disciplined role for options in a systematic equity strategy: **tactical, event-driven hedging rather than a permanent income overlay.**

Key principles that follow from the research:

1. **Insurance has a persistent negative expected value; use it sparingly and selectively.** Because OTM puts trade at a volatility risk premium (implied vol systematically exceeds subsequently realized vol), a static protective-put or collar program is a long-run drag ([AQR](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Risk-and-Return.pdf?sc_lang=en); [Israelov](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2934538_code437885.pdf?abstractid=2934538&mirid=1)). A systematic strategy should reserve hedge deployment for periods of elevated tail risk (e.g., stretched valuations, inverted yield curves, spiking credit spreads, or the strategy's own internal risk signals crossing a threshold) rather than running it continuously.
2. **Static equity-exposure reduction is often a more capital-efficient hedge than buying puts.** Multiple independent studies find that simply trimming position size achieves better drawdown outcomes per unit of expected-return sacrifice than buying options-based insurance ([Israelov, "Pathetic Protection"](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2934538_code437885.pdf?abstractid=2934538&mirid=1); [Baltussen, Martens & van der Linden, 2026](https://www.evidenceinvestor.com/post/portfolio-insurance-crash-protection)). Options should be reserved for scenarios where the strategy specifically needs convex, asymmetric payoff (e.g., protecting against a specific known binary event, or when leverage/margin constraints make outright deleveraging impractical).
3. **Collars are a cheaper insurance structure than naked puts, at the cost of upside.** Financing the put with a sold call (as in CLL) roughly halves the long-run cost relative to buying puts outright, and is the natural choice when the strategy is willing to cap some upside in exchange for a cheaper or free hedge — precisely the trade JEPI and CLL make systematically.
4. **Position options sizing as a small percentage of the book, not the whole book.** Given the consistent finding that a *fully* hedged/covered posture drags heavily on compounding, most institutional overlay programs hedge only a fraction (commonly 25–75%) of notional exposure, preserving most of the upside participation while blunting the worst-case drawdown.
5. **Use volatility level as a sizing signal.** Because premium income (for calls or puts sold) and hedge cost (for puts bought) both scale with implied volatility, a systematic overlay should condition its sizing on the VIX/VXN level: sell more premium when implied vol is rich relative to trailing realized vol; buy protection preferentially when it is cheap relative to the strategy's own risk assessment, not reactively after a selloff has already repriced options expensively.

This reframes options overlays correctly: not as a yield-enhancement gimmick, nor as a "free" insurance policy, but as a **convexity-shaping tool** to be deployed with the same rigor, backtesting discipline, and cost-awareness as any other systematic signal.

---

## 7. A concrete, codeable covered-call overlay rule-set

Below is a fully specified rule-set for a monthly covered-call overlay on core equity holdings, informed by the Cboe BXMD (30-Delta BuyWrite) methodology and industry-standard practitioner conventions.

### 7.1 Strategy specification

**Universe:** Core long equity holdings held for ≥30 days (avoids wash-sale/short-term-gain complications and ensures shares are available to cover the short call).

**Entry rule (monthly cycle):**
- On the trade date **T = third Friday of each month** (standard monthly options expiration, matching Cboe's BXM/BXMD roll convention — [Cboe BuyWrite Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_BuyWrite_Indices_Methodology.pdf)), for each eligible position:
  1. Pull the option chain for the nearest expiration with **28–35 calendar days to expiration (DTE)**.
  2. Select the **call strike whose delta is closest to 0.30** (the "30-delta" convention also used by Cboe's own BXMD index, which is designed specifically around 0.30-delta strike selection — [Cboe BuyWrite Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_BuyWrite_Indices_Methodology.pdf)). This typically corresponds to a strike 3–8% out-of-the-money depending on implied volatility.
  3. Sell **1 call contract per 100 shares held** (round down; do not oversell uncovered contracts).
  4. Record: strike, premium received, expiration date, delta at entry, and implied volatility at entry.

**Position management:**
  - **Profit-take rule:** if the short call's market value decays to ≤25% of the premium originally received (i.e., 75% of max profit already captured) with more than 5 trading days remaining, close the call early to reduce gamma/pin risk and free the position for the next cycle.
  - **Roll rule:** if, with 5–7 days to expiration, the option is in-the-money (underlying > strike) and the position holder wants to retain the shares (avoid assignment), roll the call out (to the next monthly cycle) and up (to a new 0.30-delta strike), collecting a net debit or credit depending on the roll economics. If assignment is acceptable (e.g., the position was oversized or a rebalance is desired anyway), let it expire and get called away.
  - **Skip-the-cycle rule:** if trailing 30-day realized volatility of the position is in the bottom quartile of its own 2-year range (i.e., implied volatility is unusually cheap and premium income would be minimal, e.g., <0.4% monthly), skip writing that cycle rather than accept an unfavorable premium-for-upside-cap trade.

**Coverage ratio:** write calls against no more than **50–75% of total equity notional** at any time, leaving the remainder uncapped — this single design choice is what separates a "guardrail" overlay from the fully-covered QYLD/BXM structure that gives up the vast majority of bull-market upside (§1, §4).

### 7.2 Realistic assumptions for backtesting / paper implementation

| Assumption | Value | Basis |
|---|---:|---|
| Target short-call delta | 0.30 | Standard practitioner convention; matches Cboe BXMD design ([Cboe BuyWrite Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_BuyWrite_Indices_Methodology.pdf)) |
| Days to expiration at entry | 28–35 (monthly) | Standard monthly cycle used by BXM/BXN/PUT ([Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)) |
| Average gross monthly premium, S&P-500-like large-cap portfolio, ATM | 2.0–3.0% | Cboe BXN reports ~2.3% average gross monthly premium at near-ATM strikes ([Cboe BXN QRG](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf)) |
| Average gross monthly premium at 0.30 delta (more OTM than ATM) | ~1.0–2.0% | Roughly half of ATM premium at 30-delta vs ~50-delta strikes; consistent with practitioner "0.30 delta, 30–45 DTE" guidance ([TradeAlgo covered-call guide](https://www.tradealgo.com/trading-guides/options/covered-calls-for-income-pro); [Equicurious options guide](https://equicurious.com/learn/derivatives/options-strategies-and-greeks/covered-calls-and-cash-secured-puts)) |
| Realistic sustainable annualized premium yield (net of assignment/rolling costs) | 5–12% for single stocks; lower (in the 6–9% range) for a diversified large-cap book at 50–75% coverage, consistent with BXM's historical ~2 percentage-point average annual return shortfall vs. S&P 500 at full coverage | [Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf); [Embark Funds covered-call yield table](https://www.embarkfunds.com/insights/covered-calls-concentrated-stock-income) |
| Upside cap per cycle | Strike price (≈3–8% above spot at entry, depending on IV) | Delta-implied OTM distance |
| Assignment probability per cycle at 0.30 delta | ≈25–35% | Delta ≈ probability of finishing ITM ([Schwab, "Covered Calls: Beyond the Basics"](https://www.schwab.com/learn/story/covered-calls-beyond-basics)) |
| Historical full-coverage drag vs. buy-and-hold in strong bull years | 10–20+ percentage points/year (e.g., QYLD vs Nasdaq-100 in 2023–2024) | [Global X QYLD presentation](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf) |
| Expected volatility reduction vs. underlying (at full coverage) | ~25–35% | [Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf); [Cboe BXN QRG](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf) |
| Expected max-drawdown reduction vs. underlying (at full coverage) | ~10–15 percentage points less severe | [Cboe BXM fact sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf) |
| Transaction costs | Bid-ask spread ≈ 0.5–2% of option premium (wider for less-liquid single names); model half the bid-ask spread as implicit cost per Whaley's BXM construction convention | [Whaley, "Return and Risk of CBOE Buy Write Monthly Index"](https://www.whaley.info/_files/ugd/1362e1_28ef09f741b04464b1ab570210992fbd.pdf) |

### 7.3 Pseudocode

```
FOR each monthly cycle (on 3rd Friday of month):
    FOR each eligible equity position P in portfolio:
        IF shares_held(P) >= 100 AND holding_period(P) >= 30 days:
            iv_rank = trailing_2yr_iv_percentile(P)
            IF iv_rank < 25th_percentile:
                SKIP  # premium too cheap to justify upside cap
                CONTINUE

            chain = get_option_chain(P, dte_range=[28, 35])
            target_option = select_strike_closest_to_delta(chain, delta=0.30, type='call')

            contracts_to_sell = floor(shares_held(P) * coverage_ratio / 100)
            # coverage_ratio in [0.50, 0.75] -- guardrail, not full coverage

            IF contracts_to_sell >= 1:
                sell_to_open(target_option, qty=contracts_to_sell)
                log_trade(P, target_option, premium_received, entry_date=today)

    FOR each open short_call position C:
        days_to_expiry = C.expiration - today
        current_value = mark_to_market(C)
        pct_of_max_profit_captured = 1 - (current_value / C.premium_received)

        IF pct_of_max_profit_captured >= 0.75 AND days_to_expiry > 5:
            buy_to_close(C)  # lock in early profit, reduce pin risk

        ELIF days_to_expiry <= 6:
            IF underlying_price(C.symbol) > C.strike:      # ITM, at risk of assignment
                IF retain_shares_flag(C.symbol):
                    roll_out_and_up(C, new_delta=0.30, new_dte=30)
                ELSE:
                    ALLOW_ASSIGNMENT  # let shares be called away
            ELSE:
                ALLOW_EXPIRE_WORTHLESS  # keep full premium, shares retained
```

### 7.4 Expected outcome profile (based on cited historical data, at 50–75% coverage)

- **Income:** approximately 3–6% net annualized premium yield on total portfolio value (roughly half of BXM's full-coverage ~2 percentage-point average annual premium capture, scaled down for partial coverage and OTM strike selection vs. ATM).
- **Upside participation:** roughly 60–85% of the underlying's upside in a strong bull year (vs. ~40–65% for a fully-covered ATM strategy like classic BXM, and even less for QYLD's ATM-on-Nasdaq-100 structure).
- **Downside/volatility mitigation:** a meaningful but partial reduction — expect roughly 10–20% lower realized volatility and a somewhat shallower max drawdown than a pure buy-and-hold position, well short of BXM's ~30% full-coverage reduction, precisely because only half-to-three-quarters of the notional is covered.
- **Regime sensitivity:** this strategy, like all covered-call overlays, will underperform buy-and-hold in strong, sustained bull markets (the empirical BXM/BXN/QYLD record is unambiguous on this point) and will outperform or roughly match buy-and-hold in flat, choppy, or declining markets.

---

## Sources cited

- [Cboe S&P 500 BuyWrite Index (BXM) Fact Sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_BXM-Index.pdf)
- [Cboe NASDAQ-100 BuyWrite Index (BXN) Quick Reference Guide](https://cdn.cboe.com/resources/indices/documents/bxn_qrg.pdf)
- [Cboe NASDAQ BuyWrite Indices Methodology](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_NASDAQ_BuyWrite_Indices_Methodology.pdf)
- [Cboe BuyWrite Indices Methodology (incl. BXMD 30-Delta BuyWrite)](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_BuyWrite_Indices_Methodology.pdf)
- [Cboe S&P 500 PutWrite Index (PUT) Fact Sheet](https://cdn.cboe.com/resources/indices/factsheet/CboeGlobalIndices_PUT-Index.pdf)
- [Bondarenko (Cboe), "Historical Performance of Put-Writing Strategies"](https://cdn.cboe.com/resources/education/research_publications/PutWriteCBOE19_v14_by_Prof_Oleg_Bondarenko_as_of_June_14.pdf)
- [Cboe Collar Indices Methodology (CLL, CLL1M, CLL3M)](https://cdn.cboe.com/api/global/us_indices/governance/Cboe_Collar_Indices_Methodology.pdf)
- [Asset Consulting Group / Cboe, collar and put-write historical study](https://cdn.cboe.com/resources/indices/documents/pap-assetconsultinggroup-cboe-feb2012.pdf)
- [Cboe Strategy Benchmark Indices overview](https://www.cboe.com/us/indices/benchmark_indices/)
- [Cboe Benchmark Indexes Fact Sheet](https://cdn.cboe.com/resources/indices/documents/benchmarks-fact-sheet.pdf)
- [AQR, "Risk and Return of Equity Index Collar Strategies"](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/Risk-and-Return.pdf?sc_lang=en)
- [CXO Advisory, "Equity Index Collar Performance"](https://www.cxoadvisory.com/equity-options/equity-index-collar-performance/)
- [Israelov, "Pathetic Protection: The Elusive Benefits of Protective Puts" (SSRN)](https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID2934538_code437885.pdf?abstractid=2934538&mirid=1)
- [Alpha Architect summary of "Pathetic Protection"](https://alphaarchitect.com/pathetic-protection/)
- [Evidence Investor, summary of Baltussen, Martens & van der Linden (2026), *Financial Analysts Journal*](https://www.evidenceinvestor.com/post/portfolio-insurance-crash-protection)
- [Whaley, "Return and Risk of CBOE Buy Write Monthly Index"](https://www.whaley.info/_files/ugd/1362e1_28ef09f741b04464b1ab570210992fbd.pdf)
- [Feldman & Roy, PM Research (BXM extended study)](https://www.pm-research.com/content/iijinvest:::14:::2:::66.full.pdf)
- [Schwab, "Covered Calls: Beyond the Basics"](https://www.schwab.com/learn/story/covered-calls-beyond-basics)
- [Global X, QYLD Q4 2024 Presentation/Fact Sheet](https://www.globalxetfs.com/content/files/2024.Q4-QYLD-Presentation-Final.pdf)
- [StockAnalysis.com, QYLD profile](https://stockanalysis.com/etf/qyld/)
- [Yahoo Finance, "QYLD's 12% Yield Looks Generous, But Its 10 Year Total Return Tells a Different Story"](https://finance.yahoo.com/markets/stocks/articles/qyld-12-yield-looks-generous-191821678.html)
- [Yahoo Finance, "QYLD's 12% Yield Masks a Decade of Underperformance Against QQQ"](https://finance.yahoo.com/markets/options/articles/qyld-12-yield-masks-decade-131200815.html)
- [Total Real Returns, QYLD total-return tracker](https://totalrealreturns.com/n/QYLD)
- [Seeking Alpha, "QYLD: Tech Exposure With A Double-Digit Yield"](https://seekingalpha.com/article/4749654-qyld-tech-exposure-with-a-double-digit-yield)
- [J.P. Morgan Asset Management, JEPI Fund Story](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fund-story/STO-JEPI.pdf)
- [J.P. Morgan Asset Management, JEPI Fact Sheet](https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/etfs/FS-JEPI.PDF)
- [Morningstar, JEPI analysis/quote page](https://www.morningstar.com/etfs/arcx/jepi/quote)
- [Morningstar, "Should You Own a Covered-Call ETF Like JEPI?"](https://www.morningstar.com/funds/should-you-own-covered-call-etf-like-jepi)
- [Yahoo Finance, "JEPI Is Falling While the S&P 500 Soars. Is That Fat 8% Yield Worth It?"](https://finance.yahoo.com/markets/stocks/articles/jepi-falling-while-p-500-190551045.html)
- [YCharts, JEPI company/comparison page](https://ycharts.com/companies/JEPI)
- [SSRN, "Equity LEAPS Calls vs. Stocks: An Empirical Study for Long-Term Speculation"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1919066)
- [Option Agent, "Using Long-Dated Calls as Stock Replacement"](https://theoptionagent.com/learn/lessons/leaps-options-explained?series=reading-the-market)
- [Ainvest, "LEAPS Options: The Long-Term Options Strategy Guide"](https://optionpilot.ainvest.com/blog/leaps-long-term-options-guide)
- [GEX Levels, "LEAPS Options Strategy Explained: Long-Term Options as Stock Replacement"](https://gex-levels.com/blog/options-leaps-strategy)
- [TradeAlgo, "Covered Calls for Income: The Professional's Guide"](https://www.tradealgo.com/trading-guides/options/covered-calls-for-income-pro)
- [Equicurious, "Covered Calls and Cash-Secured Puts"](https://equicurious.com/learn/derivatives/options-strategies-and-greeks/covered-calls-and-cash-secured-puts)
- [Embark Funds, "Covered Calls on Concentrated Stock: Income Strategy, Tax"](https://www.embarkfunds.com/insights/covered-calls-concentrated-stock-income)
- [Nasdaq Dorsey Wright, "Optimizing a Covered Call Strategy" (BXM vs BXMD)](https://dorseywright.nasdaq.com/research/bigwire/2025/05/27/05-27-2025/optimizing-a-covered-call-strategy)
- [Nasdaq, Cboe NASDAQ-100 BuyWrite Index (BXN) Overview](https://indexes.nasdaq.com/Index/Overview/BXN)

---

*Disclaimer: This report synthesizes publicly available index performance data, fund fact sheets, and academic research for informational purposes. Past performance of indices and funds cited is not indicative of future results. Backtested or hypothetical index performance (including pre-launch backfilled data disclosed by Cboe) does not reflect actual trading and may not be achievable in live implementation due to transaction costs, slippage, and liquidity constraints.*
