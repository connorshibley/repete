# Quantitative Factor Investing as a Path to Beating the S&P 500: A Rigorous Review

*Compiled for repete knowledge base use. All quantitative claims are sourced to primary/authoritative documents (Ken French Data Library, AQR Capital Management, peer-reviewed/NBER/SSRN papers, iShares fund fact sheets, Morningstar). Report date context: August 2026.*

---

## 1. Executive Summary

Decades of academic and practitioner research show that stock returns are not fully explained by market beta (CAPM) alone. A small set of systematic, replicable characteristics — **size, value, profitability/quality, investment, momentum, and low volatility/low beta** — have historically earned risk premia beyond what CAPM predicts ([Fama & French, "A Five-Factor Asset Pricing Model," *Journal of Financial Economics*, 2015](https://www.sciencedirect.com/science/article/pii/S0304405X14002323)). These "factors" underpin most systematic strategies designed to beat a cap-weighted benchmark like the S&P 500 over the long run. However, every individual factor has gone through multi-year, sometimes decade-long, drawdowns relative to the market (most notably value from 2007–2020, and size for most of the last 40 years), which is why the strongest, most defensible approach is **diversified multi-factor exposure** rather than a single-factor bet ([AQR, "Investing With Style"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)). Section 8 provides a concrete, codeable multi-factor rule set (value + momentum + quality) suitable for backtesting against S&P 500 constituents.

---

## 2. The Fama-French Factor Model and Extensions

### 2.1 Origins and the data source of record

The canonical source for factor return data is the **Kenneth R. French Data Library** at Dartmouth's Tuck School of Business, which has published free, monthly-updated factor returns since 1926 and is the standard reference used by academics and practitioners worldwide ([Kenneth R. French — Data Library](http://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/data_library_202412_archive.html); [Description of Fama/French Factors](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)). As of the most recent update, daily/monthly factor data run from **July 1926 through mid-2026**, and the five-factor (RMW/CMA) series runs from **July 1963** ([Ken French — Description of Fama/French Factors, f-f_5_factors_2x3.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html)).

### 2.2 Three-factor model (1993) and five-factor extension (2015)

The original Fama-French three-factor model (1993) added two factors to CAPM's market factor:

- **Mkt-RF (Market factor):** the value-weight return of all NYSE/AMEX/NASDAQ common stocks minus the one-month T-bill rate — i.e., the standard equity risk premium ([Ken French, Description of Fama/French Factors](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)).
- **SMB (Small Minus Big):** the return on small-cap portfolios minus large-cap portfolios, formally SMB = ⅓(Small Value + Small Neutral + Small Growth) − ⅓(Big Value + Big Neutral + Big Growth) — captures the size premium ([Ken French, f-f_factors.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)).
- **HML (High Minus Low):** the return on high book-to-market ("value") portfolios minus low book-to-market ("growth") portfolios, HML = ½(Small Value + Big Value) − ½(Small Growth + Big Growth) — captures the value premium ([Ken French, f-f_factors.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)).

In 2015, Fama and French extended the model to five factors by adding profitability and investment, motivated by the dividend-discount model ([Fama & French, "A Five-Factor Asset Pricing Model," *JFE* 116(1), 2015](https://www.sciencedirect.com/science/article/pii/S0304405X14002323)):

- **RMW (Robust Minus Weak):** the return on high (robust) operating-profitability portfolios minus low (weak) operating-profitability portfolios — RMW = ½(Small Robust + Big Robust) − ½(Small Weak + Big Weak); this is the academic "quality/profitability" factor ([Ken French, f-f_5_factors_2x3.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html)).
- **CMA (Conservative Minus Aggressive):** the return on low-asset-growth ("conservative") portfolios minus high-asset-growth ("aggressive") portfolios — CMA = ½(Small Conservative + Big Conservative) − ½(Small Aggressive + Big Aggressive); captures the "investment" factor — firms that invest conservatively tend to outperform firms that invest aggressively ([Ken French, f-f_5_factors_2x3.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html)).

A companion summary states the RMW factor shows profitable firms outperform unprofitable firms by roughly **4.7% per year on average**, and the CMA factor shows conservative-investment firms outperform aggressive-investment firms by roughly **3% per year** ([Fama & French (2015): The Five-Factor Asset Pricing Model Explained](https://blankcapitalresearch.com/learn/fama-french-five-factor-model)) — note these summary figures are secondary restatements of the FF(2015) results and should be cross-checked against the underlying French Data Library series for exact vintages.

Fama and French found that once profitability and investment are included, the value factor (HML) becomes statistically *redundant* for explaining average returns in their U.S. sample — i.e., a large part of the historical value premium can be explained as compensation correlated with the profitability and investment characteristics ([Fama & French, *JFE* 2015 abstract, EconPapers](https://econpapers.repec.org/article/eeejfinec/v_3a116_3ay_3a2015_3ai_3a1_3ap_3a1-22.htm)). A companion paper, "Dissecting Anomalies with a Five-Factor Model," shows that positive RMW/CMA exposure (profitable, conservatively investing firms) captures the high average returns of low-beta stocks, share-repurchasing firms, and low-volatility stocks — directly linking the quality and low-volatility phenomena discussed in Sections 4 and 5 below to Fama-French factor structure ([Fama & French, "Dissecting Anomalies with a Five-Factor Model," *Review of Financial Studies* 29(1), 2016](https://academic.oup.com/rfs/article/29/1/69/1843682)).

### 2.3 Momentum (Carhart/UMD) extension

Momentum (labeled "Mom" or "UMD"/"WML") is provided by the French Data Library as a separate factor, not part of the core FF3/FF5 set. It is constructed from six value-weighted portfolios formed monthly on size (median NYSE market equity breakpoint) and prior (2–12 month) returns (30th/70th NYSE percentile breakpoints); Mom = ½(Small High + Big High) − ½(Small Low + Big Low), i.e., long high-prior-return stocks, short low-prior-return stocks ([Ken French, "Detail for Monthly Momentum Factor"](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor.html)). Monthly data run from January 1927 to the present.

### 2.4 AQR's extensions: momentum, quality, and defensive factors

AQR Capital Management maintains public datasets that extend or replicate several of these factors, including "AQR Momentum Indices," "Betting Against Beta" (BAB) factors, "Quality Minus Junk" (QMJ) factors, and "Value and Momentum Everywhere" (VME) factors spanning multiple asset classes ([AQR Capital Management, Data Sets](https://www.aqr.com/Insights/Datasets)). AQR's own framework organizes factors into four families it considers most pervasive in the literature and practice: **value, momentum, carry, and defensive/quality** ([AQR, "Fact, Fiction, and Factor Investing"](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/AQRJPMQuant23FactFictionandFactorInvesting.pdf?sc_lang=en)).

### 2.5 Long-run historical premium evidence (summary table)

| Factor | Description | Approx. long-run premium (annualized) | Source |
|---|---|---|---|
| Market (Mkt-RF) | Equity risk premium | ~6-9% (varies by period) | [Ken French Data Library](http://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/data_library_202412_archive.html) |
| SMB (size) | Small minus big | ~2.5%/yr, 1926–2017 (statistically weak; CAPM alpha insignificant) | [AQR, "Fact, Fiction, and the Size Effect," *J. Portfolio Mgmt* 45(1)](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf) |
| HML (value) | High minus low book-to-market | 3.56%/yr full sample (1926-2007 average ~5.20%/yr; post-2007 collapse, see §3) | [FinObservatory Equity Risk Factors](https://finobservatory.org/factors); [Cambridge Core, "Is the Value Premium Dead?"](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/is-the-value-premium-dead-forecasting-valuegrowth-cycles-with-the-implied-value-premium/435A9DD112FA371FD268C05757B9E1E3) |
| RMW (profitability/quality) | Robust minus weak profitability | ~4.7%/yr (secondary restatement) | [Blank Capital Research summary of FF 2015](https://blankcapitalresearch.com/learn/fama-french-five-factor-model) |
| CMA (investment) | Conservative minus aggressive | ~3%/yr (secondary restatement) | [Blank Capital Research summary of FF 2015](https://blankcapitalresearch.com/learn/fama-french-five-factor-model) |
| UMD/Mom (momentum) | Winners minus losers | 9.0%/yr (1927–2019); 5.0%/yr (1992-2019) | [Alpha Architect, citing Hasler (2021)](https://alphaarchitect.com/is-the-value-premium-smaller-than-we-thought/) |
| BAB (defensive/low-beta) | Betting against beta | Sharpe ratio 0.73 (full sample, higher than HML/UMD/SMB) | [AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf) |

Full-sample Sharpe ratio comparison across factors (same AQR source, 1926–2017 sample): **SMB 0.22, HML 0.38, UMD (momentum) 0.48, BAB 0.73** — showing momentum and defensive/low-beta factors have historically had markedly higher risk-adjusted returns than size or even value ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).

---

## 3. Value Investing Evidence and the 2007–2020 "Value Drought"

### 3.1 Long-run outperformance

Using the HML factor, U.S. value stocks outperformed growth stocks by an average of **4.1% per year from 1926–2023** ([Quantopia, "Understanding HML"](https://www.quantopia.net/blog/understanding-hml-the-value-premium-in-the-fama-french-model/)). Over the full sample of 1,199 months from July 1926, HML compounded at **3.56% a year** with a **t-statistic of 3.43**, i.e., statistically significant over the long run ([FinObservatory, "Equity Risk Factors"](https://finobservatory.org/factors)). Even after severe post-2007 underperformance, a long-short HML value investor was still **4.3 times as wealthy** as the growth investor over the full July 1963–June 2020 period ([Blitz & colleagues, "Reports of Value's Death May Be Greatly Exaggerated," *Financial Analysts Journal*, 2021](https://www.tandfonline.com/doi/full/10.1080/0015198X.2020.1842704)).

### 3.2 The 2007–2020 value drought

This is one of the most important cautionary data points for any systematic strategy:

- The HML value premium averaged **5.20% per annum between July 1926 and December 2007**, but only **−0.86% per annum between January 2008 and March 2025** — a stark regime break ([Cambridge Core, "Is the Value Premium Dead? Forecasting Value–Growth Cycles"](https://www.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/is-the-value-premium-dead-forecasting-valuegrowth-cycles-with-the-implied-value-premium/435A9DD112FA371FD268C05757B9E1E3)).
- The HML long-short portfolio's cumulative index peaked in **December 2006** and had not made a new high 233 months later, having compounded at **−2.0% a year** over that span ([FinObservatory, "Equity Risk Factors"](https://finobservatory.org/factors)).
- The HML book-to-market portfolio experienced a **−55% drawdown** from 2007 to mid-2020 — the largest drawdown observed since June 1963 ([Blitz et al., "Reports of Value's Death May Be Greatly Exaggerated," *FAJ* 2021](https://www.tandfonline.com/doi/full/10.1080/0015198X.2020.1842704)).
- The CFA Institute notes that a long-short HML portfolio generated over **4000% cumulative returns from 1926–2007**, but "since 2007, the results have completely flipped," with the portfolio losing about **half its value** following the Great Recession as growth (especially tech) stocks took off ([CFA Institute, "Fama and French: The Five-Factor Model Revisited"](https://rpc.cfainstitute.org/blogs/enterprising-investor/2022/fama-and-french-the-five-factor-model-revisited)).

### 3.3 AQR's rebuttal: "Is (Systematic) Value Investing Dead?"

AQR's flagship response to the value-drought narrative, "Is (Systematic) Value Investing Dead?" (Israel, Laursen & Richardson, 2020), tests and rejects several common explanations for value's decade of underperformance — including that fundamentals worsened for cheap stocks, that low interest rates structurally hurt value, and that intangible assets/tech-sector business-model shifts invalidated book-to-price — finding little empirical support for any of them ([AQR, "Is (Systematic) Value Investing Dead?"](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/AQR-JPMQuant21IsValueInvestingDead.pdf?sc_lang=en); [SSRN abstract](https://ssrn.com/abstract=3554267)). AQR attributes the underperformance primarily to a **widening valuation gap** (a "revaluation" event) between cheap and expensive stocks, not to a structural collapse of the value premium itself, and documents that value spreads had reached near-historical extremes, historically associated with stronger subsequent value returns ([AQR, "Is (Systematic) Value Investing Dead?" perspectives piece](https://www.aqr.com/Insights/Perspectives/Is-Systematic-Value-Investing-Dead)). Morningstar's independent analysis reaches a similar conclusion: valuation spreads were well above the historical median as of its 2023 analysis, and "there doesn't seem to be any strong evidence that the value premium is dead" ([Morningstar, "It's Too Soon to Say the Value Premium Is Dead"](https://www.morningstar.com/portfolios/its-too-soon-say-value-premium-is-dead)).

**Caution for the trading agent:** the value drought demonstrates that even a factor with a ~90-year positive track record can underperform the market for 13+ years. Any backtest of a value-only strategy must be stress-tested against the 2007–2020 window specifically.

---

## 4. Quality Factor: Profitability, Low Leverage, Stable Earnings

### 4.1 AQR's Quality Minus Junk (QMJ)

The primary academic/practitioner reference is Asness, Frazzini & Pedersen, **"Quality Minus Junk"** (working paper 2013–2014; published *Review of Accounting Studies* 2019) ([SSRN paper](https://ssrn.com/abstract=2312432); [original draft PDF](http://www.efalken.com/LowVolClassics/Asness_Frazzini_Pedersen_QMJ.pdf)). QMJ defines quality using four sub-components — **profitability, growth, safety (low leverage/low earnings volatility), and payout** — and constructs a long/short factor: long high-quality ("quality") stocks, short low-quality ("junk") stocks, at the intersection of six value-weighted portfolios formed on size and quality ([AQR, "Quality Minus Junk: Factors, Monthly" dataset page](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)).

Key findings from AQR's dataset documentation and paper:
- Quality stocks — profitable, growing, well-managed companies — command higher prices on average than "junk" (unprofitable, stagnant, poorly managed) stocks, but the premium investors pay for quality is "puzzlingly modest" relative to the return difference quality delivers, implying quality is not fully priced by the market ([AQR, QMJ dataset page](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)).
- QMJ factors are provided for the **U.S. and 23 international equity markets**, updated monthly, with the QMJ strategy shown to earn significant historical risk-adjusted returns across this global sample ([AQR, QMJ dataset page](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)).
- AQR also provides 10 quality-sorted, long-only portfolios starting **1956** (U.S. long sample) and **1986** (global broad sample) for backtesting ([AQR, "Quality Minus Junk: 10 Quality-Sorted Portfolios, Monthly," dataset documentation](https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets/Quality-Minus-Junk-10-QualitySorted-Portfolios-Monthly.xlsx)).

### 4.2 Quality's link to risk-adjusted (not necessarily raw) outperformance

Fama and French's own five-factor research shows that positive RMW (profitability) and CMA (conservative investment) exposure — i.e., a "quality" tilt — captures the high average returns of low-beta stocks, firms with low volatility, and firms that repurchase shares, while negative RMW/CMA slopes explain the low returns of high-beta, high-volatility, aggressively-investing/issuing firms ([Fama & French, "Dissecting Anomalies with a Five-Factor Model," *RFS* 2016](https://academic.oup.com/rfs/article/29/1/69/1843682)). This is the academic bridge between "quality" and "low volatility" investing (see §5): both are, in part, manifestations of the same underlying profitability/investment factor structure.

### 4.3 Quality and size interact

AQR's size-effect research (§5 below) found that controlling for quality via QMJ dramatically *strengthens* the statistical significance of the size effect (SMB's alpha t-statistic rises from 0.91 to 4.84 once QMJ is added as a control), because small-cap indices are implicitly "long junk" — small firms tend to be lower quality than large firms — which suppresses the observed size premium ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)). This is direct evidence for why quality should be combined with, rather than viewed in isolation from, other factors (see §7-8).

---

## 5. Low-Volatility Anomaly

### 5.1 Original literature

The foundational papers are Ang, Hodrick, Xing & Zhang, **"The Cross-Section of Volatility and Expected Returns"** (*Journal of Finance* 61(1), 2006) and its follow-up, **"High Idiosyncratic Volatility and Low Returns: International and Further U.S. Evidence"** (*Journal of Financial Economics* 91(1), 2009) ([Wiley/JF abstract](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2006.00836.x); [Columbia preprint of the 2009 paper](https://business.columbia.edu/sites/default/files-efs/pubfiles/3361/ang_high_idiosyncratic_volatility.pdf)). The core finding: stocks with high sensitivity to innovations in aggregate volatility have low average returns, and stocks with high idiosyncratic volatility (relative to the Fama-French model) have "abysmally low" average returns — the opposite of the CAPM prediction that higher risk should earn higher expected return ([Ang, Hodrick, Xing & Zhang, *JF* 2006 abstract](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2006.00836.x)).

Reported magnitude: sorting stocks into idiosyncratic-volatility quintiles, the lowest-volatility quintile earned about **1.06% average monthly return** versus **0.02%** for the highest-volatility quintile — a **~1.04 percentage-point per month** low-minus-high spread ([summary of Ang et al. 2006 methodology, Blank Capital Research](https://blankcapitalresearch.com/learn/ang-volatility-expected-returns)). The international follow-up found that across 23 developed markets, the difference in average returns between the extreme quintiles sorted on idiosyncratic volatility was **−1.31% per month**, after controlling for world market, size, and value factors, and the effect was individually significant in each G7 country ([Ang, Hodrick, Xing & Zhang, "High Idiosyncratic Volatility and Low Returns," 2009, preprint](https://business.columbia.edu/sites/default/files-efs/pubfiles/3361/ang_high_idiosyncratic_volatility.pdf)).

### 5.2 The related "Betting Against Beta" literature

Frazzini & Pedersen's **"Betting Against Beta"** (NBER Working Paper 16601, 2010; published *Journal of Financial Economics* 111, 2014) provides a theoretical and empirical foundation closely related to the low-volatility anomaly: leverage-constrained investors bid up high-beta assets, so a portfolio that is long leveraged low-beta assets and short high-beta assets (BAB) produces statistically significant positive risk-adjusted returns across U.S. equities, 20 international equity markets, Treasury bonds, corporate bonds, and futures ([NBER Working Paper 16601](https://www.nber.org/system/files/working_papers/w16601/w16601.pdf)).

### 5.3 Caveats and limits-to-arbitrage evidence

AQR's own research, "The Limits to Arbitrage and the Low-Volatility Anomaly," cautions that the anomaly's existence and tradeable efficacy are "more limited than widely believed" over 1963–2010: anomalous returns are not found within equal-weighted long-short portfolios, and within value-weighted portfolios the alpha is largely eliminated once sub-$5 (low-priced) stocks are excluded ([AQR, "The Limits to Arbitrage and the Low-Volatility Anomaly"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/The-Limits-to-Arbitrage-and-the-Low-Volatility-Anomaly.pdf); [SSRN version](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1738316)). A complementary explanation, "Benchmarks as Limits to Arbitrage" (Baker, Bradley & Wurgler), argues the anomaly persists partly because institutional investors are mandated to maximize information ratio relative to a fixed benchmark without leverage, which discourages arbitrage in both low-beta/high-alpha and high-beta/low-alpha stocks ([Baker, Bradley & Wurgler, SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1585031); [NYU Stern PDF](https://pages.stern.nyu.edu/~jwurgler/papers/faj-benchmarks.pdf)).

**Practical implication:** low-volatility/low-beta strategies work best when implemented as value-weighted, liquid-stock-only portfolios (excluding micro-caps and penny stocks) — precisely how MSCI's Minimum Volatility indices (underlying USMV) and AQR's BAB factor are constructed.

---

## 6. Size Factor: Historical Evidence and Its Weak Modern Record

### 6.1 Original discovery

The size effect was first documented by Banz (1981), Keim (1983), and Roll (1983): pre-1980 U.S. data showed small-cap stocks substantially outperformed large caps even after risk adjustment — an early and influential challenge to the CAPM ([AQR, "Fact, Fiction, and the Size Effect," *Journal of Portfolio Management* 45(1)](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).

### 6.2 Weakening and near-disappearance after discovery

This is one of the clearest cases of a **published anomaly weakening after discovery** in the factor-investing literature:

- Over the original 1936–1975 sample, SMB returned **1.9%/year** annualized with a t-statistic of only **1.21** (below conventional significance) and essentially zero/slightly negative CAPM alpha; the smallest-decile-minus-largest-decile spread returned **7.1%/year** with a t-stat of 1.78 (barely meeting the 10% significance threshold) ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).
- During **1976–1986**, immediately after the original papers were published, SMB had an unusually strong run (Sharpe ratio 0.86 — almost 4x its 1936–1975 Sharpe ratio) — but this reversed: **size returns were negative for the following decade**, then only slightly positive and essentially flat over the next two decades. There has been **no statistically significant positive size premium** in the post-discovery period ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).
- Over the full 1926–2017 sample, SMB's raw premium is statistically significant, but its **CAPM alpha is statistically insignificant**, and among BAB, HML, UMD, RMW, and SMB, **SMB had the weakest performance on every metric examined** (mean return, Sharpe ratio, t-stat, alpha, information ratio) — full-sample Sharpe ratio of just **0.22** vs. 0.38 (HML), 0.48 (UMD), and 0.73 (BAB) ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).
- The size premium is also overwhelmingly concentrated in **January** (2.1%/month in January vs. ~0.0% in other months, 1926–2017) — a seasonal anomaly (year-end tax-loss selling, rebalancing, window dressing) that has itself weakened since 1976, contributing to the size effect's broader decline ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)).
- A widely-cited independent review, van Dijk, **"Is size dead? A review of the size effect in equity returns"** (*Journal of Banking & Finance*, 2011), and Schwert's earlier review both conclude the small-firm anomaly has effectively disappeared since the papers that discovered it were published, consistent with the "anomaly gets arbitraged away once known" hypothesis ([van Dijk, SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=879282); citing Schwert (2003): "it seems that the small-firm anomaly has disappeared since the initial publication of the papers that discovered it").

### 6.3 Why size still matters — conditionally

AQR's analysis shows the size effect is **not robust to alternative (non-price) measures of firm size**, is concentrated in illiquid microcap stocks (with estimated transaction costs of 88–240 bps/year depending on strategy size, enough to erase most of the premium), and is largely a proxy for **liquidity risk** rather than a distinct size premium ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)). Critically, however, once returns are controlled for **quality (QMJ)**, the size effect's alpha becomes highly significant (t-stat rises from 0.91 to 4.84), a more linear size-return relationship reappears, the January concentration diminishes, and the effect becomes robust internationally ([AQR, "Fact, Fiction, and the Size Effect"](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)). AQR's practical conclusion: don't make a generic small-cap bet; instead overweight **small value, small momentum, or small quality** stocks, where size is combined with other factors rather than used alone.

---

## 7. Live Fund Track Records: iShares MSCI USA Factor ETFs vs. S&P 500

All five iShares MSCI USA single-factor ETFs launched around **April–July 2013**, giving a ~12-13 year live (not backtested) track record as of mid-2026. Figures below are from each fund's official iShares/BlackRock fact sheet (NAV total return, since inception, annualized) or third-party aggregators as noted.

| ETF | Factor | Inception | Since-inception annualized return (NAV) | Benchmark (index) annualized return | Source |
|---|---|---|---:|---:|---|
| **MTUM** | Momentum | 4/16/2013 | **16.79%** | 17.00% (MSCI USA Momentum SR Variant) | [iShares MTUM Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/mtum-ishares-msci-usa-momentum-factor-etf-fund-fact-sheet-en-us.pdf) |
| **VLUE** | Value | 4/16/2013 | **11.08%** (NAV) | 11.25% (MSCI USA Enhanced Value) | [iShares VLUE Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/vlue-ishares-msci-usa-value-factor-etf-fund-fact-sheet-en-us.pdf) |
| **QUAL** | Quality | 7/16/2013 | **13.81%** | 14.00% (MSCI USA Sector Neutral Quality) | [iShares QUAL Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/qual-ishares-msci-usa-quality-factor-etf-fund-fact-sheet-en-us.pdf) |
| **USMV** | Low volatility (min-vol) | 10/18/2011 | **11.60%** | 11.73% (MSCI USA Minimum Volatility) | [iShares USMV Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/usmv-ishares-msci-usa-min-vol-factor-etf-fund-fact-sheet-en-us.pdf) |
| **SIZE** | Size (low-size tilt) | 4/16/2013 | **11.47%** | 11.63% (MSCI USA Low Size) | [BlackRock SIZE Fact Sheet](https://www.blackrock.com/us/individual/literature/fact-sheet/size-ishares-msci-usa-size-factor-etf-fund-fact-sheet-en-us.pdf) |
| **S&P 500 (SPY)** | Market benchmark | 1/22/1993 | 10.80–10.93% (since 1993 inception; ~13-15% over trailing 5-10yr windows through mid-2026) | S&P 500 Index | [SSGA SPY Fact Sheet](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy) |

Notes and context:
- **Momentum (MTUM)** has been the standout single-factor performer, compounding at roughly **16.8%/year NAV** since April 2013 — well ahead of typical broad-market returns over the same stretch — though it also carries the highest volatility and drawdown risk among the five (e.g., −18.23% in 2022 vs. the broad market's −18.11%) ([iShares MTUM Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/mtum-ishares-msci-usa-momentum-factor-etf-fund-fact-sheet-en-us.pdf); [S&P 500 2022 return, Slickcharts](https://www.slickcharts.com/sp500/returns)). An independent total-return tracker corroborates MTUM's since-inception CAGR at **+15.84%/year** (April 18, 2013 – August 6, 2026), turning a $10,000 initial investment into **$70,691** ([totalrealreturns.com, MTUM](https://totalrealreturns.com/n/MTUM)).
- **Quality (QUAL)** and **Value (VLUE)** have both delivered double-digit annualized NAV returns since inception (13.81% and 11.08% respectively), broadly in line with or modestly behind their MSCI benchmark indices, and demonstrating that quality has been a consistently strong single-factor performer over this window ([iShares QUAL Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/qual-ishares-msci-usa-quality-factor-etf-fund-fact-sheet-en-us.pdf); [iShares VLUE Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/vlue-ishares-msci-usa-value-factor-etf-fund-fact-sheet-en-us.pdf)).
- **Low volatility (USMV)** and **Size (SIZE)** have lagged a cap-weighted S&P 500 tracker over most of this bull-market-heavy 2013–2026 window (both ~11.5% annualized NAV vs. SPY's low-to-mid teens over comparable trailing windows), consistent with the academic evidence that low-vol and size factors underperform in strong, low-volatility bull markets and add value mainly in drawdowns/crises ([iShares USMV Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/usmv-ishares-msci-usa-min-vol-factor-etf-fund-fact-sheet-en-us.pdf); [BlackRock SIZE Fact Sheet](https://www.blackrock.com/us/individual/literature/fact-sheet/size-ishares-msci-usa-size-factor-etf-fund-fact-sheet-en-us.pdf)). For example, USMV returned only **10.34%** in 2023 and **15.75%** in 2024 versus the S&P 500's **26.29%** and **25.02%** in those same years — a large single-factor bull-market shortfall typical of defensive strategies ([iShares USMV Fact Sheet](https://www.ishares.com/us/literature/fact-sheet/usmv-ishares-msci-usa-min-vol-factor-etf-fund-fact-sheet-en-us.pdf); [S&P 500 annual returns, Slickcharts](https://www.slickcharts.com/sp500/returns)).
- For calibration, the S&P 500 (via the SPDR SPY ETF, inception January 1993) has compounded at roughly **10.8–10.9%/year annualized since inception** through mid-2026, with 10-year annualized returns in the **14.9–15.4%** range depending on measurement date ([SSGA SPY Fact Sheet](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy); [StockAnalysis SPY comparison tool](https://stockanalysis.com/etf/compare/spy/)).

**Takeaway:** Over their ~13-year live histories, momentum and quality factor ETFs have modestly outpaced or kept pace with the S&P 500 with meaningfully different risk/return paths, while low-volatility and size factor ETFs have lagged in absolute terms during this unusually strong bull-market period — exactly the pattern the academic literature (§5, §6) predicts.

---

## 8. Multi-Factor Combination and Diversification Evidence

### 8.1 Why combine factors: negative correlation between value and momentum

AQR's "Value and Momentum Everywhere" (Asness, Moskowitz & Pedersen, published in the *Journal of Finance*, 2013) is the seminal paper: value and momentum premia are pervasive and statistically significant across eight diverse markets and asset classes (individual stocks in the U.S., U.K., continental Europe, Japan; equity index futures; government bonds; currencies; commodity futures), and — critically — **value and momentum are negatively correlated with each other**, both within and across asset classes, even though each correlates positively with itself across markets ([AQR, "Value and Momentum Everywhere"](https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere)). AQR's updated dataset covers VME factor returns from **January 1972** to the present ([AQR, "Value and Momentum Everywhere: Factors, Monthly" dataset](https://www.aqr.com/Insights/Datasets/Value-and-Momentum-Everywhere-Factors-Monthly)).

### 8.2 Quantified diversification benefit

AQR's "Investing With Style" (*Journal of Investment Management*) quantifies this directly using four style factors (value, momentum, carry, defensive), each scaled to 10% annualized volatility, across seven asset-class contexts ([AQR, "Investing With Style"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)):

| Factor | Annual excess return | Sharpe ratio | Correlation to equities |
|---|---:|---:|---:|
| Value | 2.9% | 0.29 | 0.00 |
| Momentum | 8.3% | 0.83 | −0.03 |
| Carry | 8.7% | 0.87 | 0.20 |
| Defensive | 5.8% | 0.58 | −0.31 |
| **Composite (all four combined)** | **17.4%** | **1.74** | **−0.12** |

The correlation between value and momentum specifically is **−0.64** (main sample) to **−0.65** (robustness check with a rolling 36-month risk model) — a powerful diversification relationship ([AQR, "Investing With Style"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)). The combined multi-style composite's Sharpe ratio of **1.74** is roughly **double** the best individual factor's Sharpe ratio (0.87 for carry), and the composite's maximum drawdown of **−15.0%** is far smaller than any single style's max drawdown (value −42.1%, momentum −29.6%, carry −25.7%, defensive −37.8%) ([AQR, "Investing With Style"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)). AQR also shows that adding a modest style-factor allocation to a traditional 60/40 portfolio meaningfully improves the Sharpe ratio: a 100% global-60/40 portfolio has a Sharpe ratio of 0.31; adding a 10% style allocation raises it to 0.52; a 20% allocation raises it to 0.76; and a 30% allocation raises it to 1.04 ([AQR, "Investing With Style"](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)).

### 8.3 Factor timing and factor momentum

AQR's "Factor Momentum Everywhere" documents robust momentum behavior *in factors themselves* (not just individual stocks) across 65 widely-studied characteristic-based equity factors, showing that a time-series "factor momentum" strategy that combines timing signals across all factors earns an annualized Sharpe ratio of **0.84**, and that this factor-momentum effect adds significant incremental performance on top of traditional stock-level momentum, value, and other commonly studied factors ([AQR, "Factor Momentum Everywhere"](https://www.aqr.com/Insights/Research/Working-Paper/Factor-Momentum-Everywhere)). This supports periodically re-weighting factor exposures based on their own recent relative performance, rather than using purely static equal weights.

### 8.4 Practical implication

The consistent theme across §3 (value drought), §5 (low-vol limits-to-arbitrage), and §6 (size decay) is that **every individual factor experiences long, painful periods of underperformance**. The AQR evidence in this section is the direct empirical justification for combining negatively- or lowly-correlated factors (especially value + momentum, and quality as a stabilizer/size-effect enhancer) into a single multi-factor score, rather than betting on any one factor in isolation.

---

## 9. A Concrete, Codeable Multi-Factor Rule-Set for Backtesting

Below is a specific, implementable rule-set combining **value, momentum, and quality** — chosen because (a) value and momentum are empirically negatively correlated ([AQR, "Value and Momentum Everywhere"](https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere)), and (b) quality both delivers its own premium (§4) and materially strengthens factor robustness when combined with other signals (§4.3, §6.3).

### 9.1 Universe

- **Universe:** current constituents of the S&P 500 (or S&P 1500 for a broader/more liquid mid-cap extension). Use point-in-time constituent membership to avoid survivorship bias.
- **Exclusions:** exclude stocks with less than 12 months of trading history (for momentum calculation), ADRs/foreign private issuers if data quality is inconsistent, and (optionally) the bottom decile of the universe by 3-month average daily dollar volume, to control transaction costs — mirroring AQR's finding that size/microcap effects are hollowed out by trading costs (§6.2).

### 9.2 Factor signal definitions (standard financial ratios)

1. **Value score:** For each stock, compute
   - Earnings yield = trailing 12-month (TTM) Net Income / Market Capitalization (equivalently 1 / trailing P/E), **or** Book-to-Market = Total Book Equity / Market Capitalization (the original Fama-French HML sort variable — [Ken French, f-f_factors.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)).
   - Recommended composite (to mitigate the P/B-only weaknesses AQR documents for large caps, §3.3): average the cross-sectional z-scores of (a) Book-to-Market, (b) trailing Earnings Yield (E/P), and (c) trailing FCF Yield (Free Cash Flow / Enterprise Value).
   - Rank all eligible stocks by this composite Value Score in descending order (higher = cheaper = more "value").

2. **Momentum score:**
   - Compute trailing 12-month total return **excluding the most recent 1 month** (the standard 12-1 momentum construction used in academic literature and matching French's Mom-factor formation logic of using returns from month t-12 to t-2 — [Ken French, "Detail for Monthly Momentum Factor"](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor.html)).
   - Rank all eligible stocks by this 12-1 momentum return in descending order (higher = stronger positive momentum).

3. **Quality score:**
   - Compute a composite of: (a) Return on Equity (ROE) or Gross Profitability = (Revenue − COGS) / Total Assets (per Fama-French RMW construction — [Ken French, f-f_5_factors_2x3.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html)); (b) Debt-to-Equity or Total Debt / Total Assets (lower = higher quality, per AQR QMJ "safety" pillar — [AQR QMJ dataset page](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)); (c) Earnings volatility (standard deviation of quarterly EPS over trailing 5 years, lower = higher quality); (d) year-over-year Total Asset Growth (lower = more "conservative," per Fama-French CMA — [Ken French, f-f_5_factors_2x3.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html)).
   - Rank all eligible stocks by this composite Quality Score in descending order.

### 9.3 Combined score and portfolio construction

```
For each rebalance date t:
  1. Compute cross-sectional percentile ranks (0-100) for:
       ValueRank_i, MomentumRank_i, QualityRank_i   for each stock i in universe
  2. CombinedScore_i = 0.4 * ValueRank_i + 0.4 * MomentumRank_i + 0.2 * QualityRank_i
     (weights are a starting point; equal-weighting 1/3 each is a defensible simpler
      alternative given no single factor dominates historically — see Section 2.5)
  3. Rank all stocks by CombinedScore_i descending.
  4. Select top quintile (top 20%) of the universe by CombinedScore -> long-only book
     (for a long/short variant: also short the bottom quintile, dollar-neutral).
  5. Within the selected quintile, weight positions:
       - Equal-weight (simplest, avoids mega-cap concentration), OR
       - Score-weight proportional to CombinedScore_i (tilts more toward extremes).
  6. Apply position cap (e.g., max 3% per name) to control concentration risk.
```

### 9.4 Rebalancing and turnover controls

- **Rebalance frequency:** quarterly (aligned with 10-Q fundamental data refresh cadence and consistent with standard academic factor-portfolio formation conventions, e.g., Fama-French's annual June refresh of book-to-market combined with monthly momentum updates — [Ken French, f-f_factors.html](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html)).
- **Momentum signal refresh:** update the momentum component **monthly** even if full portfolio rebalancing occurs quarterly, since momentum decays faster than value/quality — many practitioner implementations (including MSCI's momentum indices underlying MTUM) use semi-annual rebalancing specifically to control momentum-related turnover and transaction costs ([iShares MTUM summary prospectus](https://www.ishares.com/us/literature/summary-prospectus/sp-ishares-edge-msci-usa-momentum-factor-etf-7-31.pdf)).
- **Turnover buffer / banding:** only trade a name out of the portfolio if its CombinedScore rank falls below, e.g., the 35th percentile (a "buy at top quintile, sell below median" band) to reduce unnecessary round-trip turnover from marginal rank changes — mirroring standard institutional smart-beta index methodology used by MSCI factor indices.

### 9.5 Risk controls

- **Sector neutrality (optional but recommended):** compute factor ranks *within* GICS sector before combining, or cap sector over/underweights at ±5% relative to the S&P 500 sector weights, to avoid the combined score simply becoming a sector bet (e.g., value tilting heavily to financials/energy, momentum tilting heavily to tech).
- **Beta/volatility check:** monitor portfolio beta to the S&P 500; if using a long-only implementation, expect beta modestly below or near 1.0; if adding a low-volatility overlay (per §5), an explicit fourth screen — exclude the highest-quintile trailing-12-month realized-volatility stocks before ranking — can be layered in to capture the defensive premium alongside value/momentum/quality.
- **Backtest validation checkpoints:** explicitly test performance in (a) 2007-2009 (financial crisis), (b) 2007–2020 (value drought window, §3.2), (c) 2020-2021 (COVID + momentum crash/value snapback), and (d) 2022 (rate-hike, growth selloff) sub-periods, since these are the regimes where single factors historically diverged most sharply from blended performance.

### 9.6 Expected properties, grounded in the evidence above

- The value+momentum combination should benefit from their empirically negative correlation (−0.64 to −0.65, §8.2), smoothing the return path relative to either factor alone.
- Adding quality should help mitigate both the value factor's tendency to load on distressed/low-quality "value traps" and the size-effect-style liquidity/junk exposure documented in §6.3, consistent with AQR's finding that quality controls materially strengthen other factor signals.
- This is a **long-only, quintile-based, quarterly-rebalanced** design suitable for a first backtest; a long/short dollar-neutral variant (long top quintile, short bottom quintile) more closely replicates the academic factor construction in Ken French's data library and AQR's factor datasets and is the appropriate design if isolating pure factor alpha versus market beta is the goal.

---

## 10. Key Source List (Primary/Authoritative)

- Kenneth R. French Data Library (Dartmouth): [Data Library home](http://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/data_library_202412_archive.html) | [Factor definitions (Mkt-RF, SMB, HML)](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_factors.html) | [RMW/CMA definitions](https://mba.tuck.dartmouth.edu/pages/faculty/Ken.french/Data_Library/f-f_5_factors_2x3.html) | [Momentum factor detail](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/Data_Library/det_mom_factor.html)
- Fama, E. & French, K. (2015). "A Five-Factor Asset Pricing Model." *Journal of Financial Economics* 116(1): [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0304405X14002323)
- Fama, E. & French, K. (2016). "Dissecting Anomalies with a Five-Factor Model." *Review of Financial Studies* 29(1): [Oxford Academic](https://academic.oup.com/rfs/article/29/1/69/1843682)
- AQR Capital Management — Data Sets hub: [aqr.com/Insights/Datasets](https://www.aqr.com/Insights/Datasets)
- AQR — "Is (Systematic) Value Investing Dead?" (Israel, Laursen, Richardson, 2020): [PDF](https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/AQR-JPMQuant21IsValueInvestingDead.pdf?sc_lang=en) | [Perspectives summary](https://www.aqr.com/Insights/Perspectives/Is-Systematic-Value-Investing-Dead)
- Asness, Frazzini & Pedersen — "Quality Minus Junk": [SSRN](https://ssrn.com/abstract=2312432) | [AQR dataset page](https://www.aqr.com/Insights/Datasets/Quality-Minus-Junk-Factors-Monthly)
- Ang, Hodrick, Xing & Zhang (2006). "The Cross-Section of Volatility and Expected Returns." *Journal of Finance* 61(1): [Wiley](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.2006.00836.x)
- Ang, Hodrick, Xing & Zhang (2009). "High Idiosyncratic Volatility and Low Returns." *Journal of Financial Economics* 91(1): [Columbia preprint](https://business.columbia.edu/sites/default/files-efs/pubfiles/3361/ang_high_idiosyncratic_volatility.pdf)
- Frazzini & Pedersen — "Betting Against Beta": [NBER Working Paper 16601](https://www.nber.org/system/files/working_papers/w16601/w16601.pdf)
- AQR — "The Limits to Arbitrage and the Low-Volatility Anomaly": [PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/The-Limits-to-Arbitrage-and-the-Low-Volatility-Anomaly.pdf)
- AQR — "Fact, Fiction, and the Size Effect" (*Journal of Portfolio Management* 45(1)): [PDF](https://spinup-000d1a-wp-offload-media.s3.amazonaws.com/faculty/wp-content/uploads/sites/3/2021/08/Fact-Fiction-and-the-Size-Effect.pdf)
- Asness, Moskowitz & Pedersen (2013). "Value and Momentum Everywhere." *Journal of Finance*: [AQR summary](https://www.aqr.com/Insights/Research/Journal-Article/Value-and-Momentum-Everywhere) | [Dataset](https://www.aqr.com/Insights/Datasets/Value-and-Momentum-Everywhere-Factors-Monthly)
- AQR — "Investing With Style" (*Journal of Investment Management*): [PDF](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/JOIM-Investing-With-Style.pdf)
- AQR — "Factor Momentum Everywhere": [aqr.com](https://www.aqr.com/Insights/Research/Working-Paper/Factor-Momentum-Everywhere)
- iShares/BlackRock fund fact sheets: [MTUM](https://www.ishares.com/us/literature/fact-sheet/mtum-ishares-msci-usa-momentum-factor-etf-fund-fact-sheet-en-us.pdf) | [VLUE](https://www.ishares.com/us/literature/fact-sheet/vlue-ishares-msci-usa-value-factor-etf-fund-fact-sheet-en-us.pdf) | [QUAL](https://www.ishares.com/us/literature/fact-sheet/qual-ishares-msci-usa-quality-factor-etf-fund-fact-sheet-en-us.pdf) | [USMV](https://www.ishares.com/us/literature/fact-sheet/usmv-ishares-msci-usa-min-vol-factor-etf-fund-fact-sheet-en-us.pdf) | [SIZE](https://www.blackrock.com/us/individual/literature/fact-sheet/size-ishares-msci-usa-size-factor-etf-fund-fact-sheet-en-us.pdf)
- SPDR S&P 500 ETF Trust (SPY) fact sheet, State Street: [ssga.com](https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy)
- Morningstar — "It's Too Soon to Say the Value Premium Is Dead": [morningstar.com](https://www.morningstar.com/portfolios/its-too-soon-say-value-premium-is-dead)
- van Dijk, M. (2011). "Is size dead? A review of the size effect in equity returns." *Journal of Banking & Finance*: [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=879282)

---

## 11. Bottom Line for the Trading Agent

1. Systematic factor premia (value, momentum, quality, low-vol/defensive) have real, statistically-grounded long-run evidence behind them from the primary sources cited above — this is not folklore.
2. Every single factor, in isolation, has had multi-year (value: 13+ years) periods of underperformance versus the S&P 500 — do not deploy capital against a single-factor thesis without explicit drawdown/regime risk controls.
3. Size alone is the weakest, least robust factor and should not be used as a standalone signal; it works best combined with quality or value.
4. Momentum has had the strongest live ETF track record among the five iShares single-factor funds examined (2013–2026), but also the highest volatility.
5. The strongest, most defensible approach — both empirically (AQR's 1.74 Sharpe-ratio composite vs. 0.29–0.87 for single factors) and practically (smoother drawdowns) — is a **diversified multi-factor blend**, such as the value+momentum+quality rule-set specified in Section 9, which should be the starting point for any backtest intended to beat the S&P 500 over a full market cycle.
