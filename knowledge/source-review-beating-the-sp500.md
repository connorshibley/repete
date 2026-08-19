# Source review: "Beating the S&P 500" (two-part research report)

**Reviewed:** 2026-08-18
**Question asked:** two PDFs dropped with no framing — *Beating the S&P 500:
Equity Strategies and the Evidence on Whether Any of Them Work* and its
companion *Deep Dives by Strategy Family* — with the follow-up instruction to
run candidate intake on them.
**Verdict: nothing adopted, nothing registered. Two families were already
closed by this repo's own record; the rest stop on a data dependency we do not
have. The report is good — that is not why it parks.**

## What the documents actually are

A two-part survey of systematic equity strategies against the S&P 500. The
overview (~43k chars) walks benchmark mechanics → factor premia → stock
selection → quantitative implementation → cost/tax drag, and ends in an
explicit per-family verdict table. The deep dive (~197k chars) expands seven
families with mechanism, replication evidence, live track records, and the
strongest documented counterarguments.

Both are secondary literature — they synthesise published research (Fama-French,
Jegadeesh-Titman, Frazzini-Pedersen, Asness, Piotroski, Gu-Kelly-Xiu,
Harvey-Liu-Zhu, Hou-Xue-Zhang, Bessembinder, Kacperczyk) rather than presenting
a new backtest. **No code, no data, no reproducible spec ships with them.**

Their own headline is the thing this repo keeps re-deriving independently:

> In backtests, dozens of strategies beat the index. In live, post-publication,
> after-fee, after-tax data, almost none of them do reliably.

and, in the closing synthesis: *"For the large majority of approaches surveyed
here, the evidence does not support a durable edge over the S&P 500 net of
costs and taxes."*

## The families, against what they give this repo

| Family | The report's own verdict | What it gives this repo |
|---|---|---|
| Momentum (Mom/UMD) | 7.44% ann.; Sharpe 0.51; −57.8% DD; crowded 2026 (J.P. Morgan) — *"strongest premium, worst tail, worst tax profile"* | **nothing — already closed.** §67/§71 |
| Cross-sectional momentum, 130/30 | — | **nothing — already rejected.** §53 |
| Quality (QMJ/RMW) | RMW 2.87% ann.; Sharpe 0.37 — *"most implementable factor tilt; small expected edge"* | PARK — needs fundamentals we lack |
| Low beta / BAB | 0.71%/mo, t=6.76, 1926–2009; Sharpe 0.75 — *"not accessible unlevered"* | PARK — needs cheap leverage the desk does not run |
| Value (HML) | 3.58% ann. full sample; **−0.39% ann. since 2015** | PARK — needs fundamentals we lack |
| Size (SMB) | 2.24% ann., t=1.70 — *"not statistically supported"* | nothing |
| Smart-beta ETFs | ~3% pre-listing → −0.50% to −1% post-listing; edge *"virtually disappears"* once live | nothing — this is the survivorship lesson, again |
| Discretionary active | 85.6% underperform over 10 yrs; net alpha −0.93%/yr | nothing |
| Manager selection | 29% top-quartile persistence vs 25% random | nothing |
| Concentrated stock picking | +1.85%/yr top-5% funds, but 57.4% of stocks lifetime-underperform T-bills — *"raises variance far more reliably than mean"* | not a mechanizable rule |
| Buffett-style quality+leverage | 18.6% excess 1976–2017, but alpha insignificant after BAB+QMJ — *"explained, not magic"* | not a mechanizable rule |
| Accounting screens (F-Score) | 23%/yr 1976–1996; sample ends 1996; expect ~35% post-publication haircut | PARK — needs fundamentals we lack |
| ML cross-section | NN3 OOS R² 0.40%; 920 predictors — *"real but tiny"* | not portable — code the live bot does not run |
| Options income (covered calls) | BXM 8.3% vs S&P 10.9% — *"risk reduction, not outperformance"* | nothing |
| Published anomalies generally | only ~35% replicate value-weighted (Hou-Xue-Zhang) | nothing — it is the prior, not a candidate |

## Blockers — each sufficient on its own

**1. The momentum family is closed by this repo's own falsification, not by
opinion.** §67 registered GEM dual momentum as DIAGNOSTIC at K=15 and returned
**0 of 3**; §71 re-ran it unlatched and returned **0 of 3** again, titled *"THE
FAMILY IS PERMANENTLY CLOSED."* §53 separately rejected the xsmom 130/30
configuration **3 of 4**. The report's momentum section is the strongest thing
in it and is precisely the thing already decided here. Re-testing it on the
strength of a survey article is what pre-registration exists to prevent.

**`scripts/recall.py search` run against `--corpus all` and `--corpus priors`
for momentum, beta, quality, piotroski, fscore, "smart beta", "covered call"
and buywrite before writing this.** One lexical false positive is worth
naming rather than silently discarding: "quality" also matches §23's
relative-volume entry filter and §58's volatility-contraction precondition
(the latter re-running the former's shape, rejected 8 of 8) — both are
entry-timing filters on the existing strategies, unrelated to a
profitability/leverage/safety composite. Read both sections directly to
confirm, not just trusted the match. Piotroski, F-Score, "smart beta" and
covered-call/buywrite return zero hits anywhere in the corpus — genuinely
untouched, not merely unindexed under a different name.

**2. Every remaining factor needs data we do not have.** Quality (RMW), value
(HML/book-to-market), and F-Score are all **fundamentals-keyed and require
point-in-time index membership** to test without survivorship. Intake Step 1
rule 2 says record the dependency and stop. `data/pit/` is frozen for a spent
licence — not for bad data — and `bot-survivorship-audit` puts the measured
inflation from getting this wrong at up to +200pp. A quality tilt scored against
a survivorship-contaminated universe would produce a number, and the number
would be fiction.

**3. The §52 freeze, and the frozen EDGE budget.** `register_gate.py`
mechanically refuses `claim: EDGE` on `data/snapshots/`. `--override-freeze`
exists and is not for getting a result you want.

**4. Hands-off since 2026-08-02.** The decision was to stop building and wait
for the decay monitor to reach n=20. It has not. A parked candidate loses
nothing by waiting; a registered one spends K permanently.

**5. There is no spec to register even if the above cleared.** The report
supplies premia and Sharpes, not a rule: no rebalance frequency, no universe
construction, no cost model, no fill convention. Turning a table row into an
arm is original work, and the arm would be ours, not theirs — which means the
report's numbers would not transfer to it anyway.

## What was adopted

**Nothing.** No registration, no gate, no config change, no `principles.md`
edit.

The one genuinely portable idea — *assume no edge until replicated; expect
roughly a 35% post-publication haircut and near-total decay once a factor is
listed* — is Step-5 shaped, and is deliberately **not** written to
`knowledge/principles.md`. Two reasons: that file is a live-only channel whose
edits open a divergence the simulator cannot reproduce (see
`docs/divergences.md` #15, `bot-divergence-check`), and this repo already
enforces the principle in code — pre-registration, Bonferroni K, the
survivorship audit, and the §-record exist precisely to make it structural
rather than advisory. Writing it down as a prompt line would add a divergence
and subtract nothing.

## Where it is parked

`knowledge/backtest_candidates.md`, §74 — same shape as §52, a section number
with no registration and no K spent. See that entry for what it would cost to
test and what would have to be true first.
