# Beat-SPY candidate dossier — 2026-08-12

Deep-research pass for the §64+ program. Owner-pinned scope: free PIT data
only (ALFRED + EDGAR + index-level ETF prices), long-only ≤1.5x leverage
with financing at FF+150bps, shorting only as a pre-registered banded arm,
benchmark **SPY total return** via `beats_benchmark_symbol`. Six families
researched in parallel against a fixed evidence bar (mechanism, published
OOS evidence, decay, PIT availability, implementability, cost realism).
Every number below is cited in the full agent dossiers; key sources inline.

**The honest headline: no surveyed family credibly beats SPY total return
under these constraints as a standalone strategy.** The published
absolute-return edges live at 2–3x leverage with 1% borrow assumptions the
bot cannot have, or in pre-publication samples that post-publication
records contradict. What survives: drawdown-truncation claims, cheap
calendar/leverage tilts with perfect PIT data, and combinations of the two.
This mirrors the record's own base rate (EDGE 1 pass in 15, and that one
explained by survivorship).

---

## Ranked: recommended for spec design (§64+)

### 1. Trend-timed leverage ("Leverage for the Long Run", Gayed-Bilello 2016)

200-DMA as a **volatility-regime classifier**: hold levered SPY above the
MA, cash/T-bills below. Mechanism still empirically true through 2022
(vol below the MA ≈ 2x vol above it). Paper (1928–2015): 1.25x variant
12.5% CAGR vs 9.1% B&H. **Post-publication replication (2016–2026, run
fresh this session on yfinance SPY TR, 5bps, FF+150 financing): 1.5x
switch 13.5% CAGR vs SPY 15.8% — trails by 2.2pp/yr; MaxDD −29.6% vs
−33.7%.** Parameter plateau is flat (10–250d all work in the paper's own
grid — no interior optimum; 100–250d survive costs). Financing honesty is
the crux: Gayed assumed 1%/yr borrow; the bot pays ~FF+150.
[Paper](https://docs.cmtassociation.org/dow-award/2016-gayed-bilello.pdf) ·
[CXO review](https://www.cxoadvisory.com/volatility-effects/leveraging-the-u-s-stock-market-based-on-sma-rules/)

**Register as**: primary levered-trend spec, arms {unleveraged switch,
1.25x, 1.5x}, grid 100/150/200/250d. Expect the total-return clause to be
close; the risk-adjusted clause is the likely pass. Needs the margin build.

### 2. Turn-of-the-month leverage tilt (McConnell & Xu 2008)

The entire 1926–2005 equity premium concentrates in the last trading day
through first three days of the month, in 31 of 35 countries; still
persistent in 2026 international data. **Perfect PIT — the signal is a
calendar.** Expressed as a tilt (1.5x during TOM window, 1.0x otherwise),
not in/out: ~2 adjustments/month on 0.5x notional ≈ 5bps/month drag.
[SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=917884) ·
[Quantpedia](https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes)

**Register as**: tilt spec, arms {no tilt, TOM 1.25x, TOM 1.5x}; also a
combined arm with the 200-DMA gate (tilt only above the MA). Calendar
effects are the classic mining graveyard — the pre-committed rule must say
so and the claim stays DIAGNOSTIC. Needs the margin build.

### 3. Vol-managed SPY, capped (Moreira-Muir 2017 / Barroso-Detzel 2021)

Scale exposure by c/RV² of prior-month daily returns, cap 1.5x, monthly
rebalance. Mechanism real; the **market factor is the only one whose
vol-managed returns survive costs** (Barroso-Detzel JFE 2021). But
Cederburg et al. (JFE 2020, 103 strategies) show the real-time Sharpe edge
mostly evaporates, and MM's own Table V shows the 1.5x cap cuts E[R] from
9.47% to 7.18% — **expected outcome: ≈ SPY TR with materially lower
drawdowns, not a total-return win.** Two mandatory PIT disciplines: c
estimated expanding-window (Liu-Tang-Zhou lookahead trap), stand-alone
scaled portfolio only (no ex-post combination weights).
[MM JF 2017](https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12513) ·
[Cederburg JFE 2020](https://www.sciencedirect.com/science/article/abs/pii/S0304405X2030132X) ·
[Barroso-Detzel](https://www.sciencedirect.com/science/article/abs/pii/S0304405X21000775)

**Register as**: DIAGNOSTIC with a reading rule honest that total-return
is expected ≈flat; the informative clauses are maxdd_within and the
risk-adjusted bar. Promotes the existing `drafts/vol-targeting.yaml`.
Needs the margin build.

### 4. Macro-regime gate on vintage UNRATE (+T10Y3M) — the genuinely new venue

Risk-off when UNRATE > its 12-mo MA, on **first-print ALFRED vintages**
(coverage to 1960; publication lag ~1mo; revisions small — the lag is the
issue, not revision). T10Y3M is a market rate, never revised, PIT by
construction. **NFCI is pseudo-PIT before 2011 (re-estimated factor model —
its GFC "history" is a modern reconstruction); restrict any NFCI arm to
2014+.** Post-publication record of the family is 0-for-2 (2022-24
inversion sat out +57%; Sahm false-triggered 2024 at 0.57). ~1-2 switches
per test period → ~5 independent bets total; any pass is episodes, not a
distribution — the reading rule must be pre-committed to that weakness.
[Phil. Economics UE-trend](https://www.philosophicaleconomics.com/2016/02/uetrend/) ·
[SAHMREALTIME](https://fred.stlouisfed.org/series/SAHMREALTIME) ·
[Chicago Fed on NFCI revisions](https://www.chicagofed.org/publications/blogs/chicago-fed-insights/2020/nfci-revisions)

**Register as**: bounded DIAGNOSTIC (UNRATE-trend gate over 1x/1.5x SPY;
T10Y3M gate as a falsification arm given 2022-24). **Blocked on a FRED API
key** (owner item) and on `probe_alfred_vintages.py` passing.

### 5. GEM / dual momentum — falsification candidate

Tracked post-publication: **8.4% CAGR vs SPY 13.6% (2014–2026)**; 2022 the
haven leg (AGG −13%) failed it; ReSolve's 1,226-spec study shows the
published 12-mo lookback is luck-indistinguishable from its siblings.
~1.7 trades/yr — nearly free to test. Register the exact book spec, forbid
lookback shopping, and let the record kill it cleanly. 2000-06 period is
PIT-untestable (EFA/AGG inceptions); cash filter from FRED DTB3, not BIL.
[Quant4Free replication](https://quant4free.com/analysis/dual-momentum/) ·
[ReSolve craftsman study](https://investresolve.com/global-equity-momentum-executive-summary/)

---

## Parked (reviewed, not advancing)

- **EDGAR aggregate insider signals — REFUSED twice over.** (a) This
  session's `probe_edgar_pit.py` run: survivorship FAIL (SIVB/FRC retain 0
  filings in the submissions window), timestamps UNDETERMINED —
  `research/edgar_pit_2026-08-12.txt`. (b) Literature: raw aggregate signal
  refuted in the modern sample (Huang-Lin-Zheng 2022 — only a
  heavily-parameterized "opportunistic" reclassification survives, textbook
  degrees-of-freedom risk); 10b5-1 plans (~61% of CEO sales) mechanically
  pollute the sell side; structured Form 4s exist only from mid-2003 so the
  2000-06 period is unbuildable. A full-index build is 5–10 days for a
  published-dead signal. Revisit only ever as a rare-event judge-context
  input (invariant #2 shape), never a gate.
- **Overnight-drift capture** — unimplementable in a next-open fill model,
  and costs exceed the anomaly by an order of magnitude regardless.
- **Quality ETFs levered** — QUAL trailed SPY over the live decade; levering
  a same-vol underperformer minus financing is strictly worse.
- **Sector rotation / relative strength** — duplicate of existing xsmom on
  a coarser universe; ETF-level momentum absent in the recent decade.
- **Faber GTAA / 60-40-with-trend** — risk-adjusted story only; lags SPY
  outright since 2009; increment over #1 + existing tsmom is marginal.
- **Utilities/SPY relative-strength gate (Gayed-Bilello 2014)** — headline
  (13.9% vs 9.8%, 1926-2013) rests on non-investable pre-1999 index data
  and gross-of-cost weekly switching. Possible future *confirm signal* on
  #1, not a standalone spec. Parked with that one narrow reopening.
- **Sell-in-May / Halloween** — globally robust, US-specifically shaky
  post-publication (Dichtl-Drobetz bootstrap). At most one cheap tilt arm
  if a spec has genuine slack; must not displace any of the five above.
- **Banded 130/30 short reopen** — owner authorized the space, but no new
  external evidence surfaced to spend on it; §53's table stands (short leg
  −38 to −58pp wherever it traded). Slot stays open per §53's closure terms
  (net-exposure band actually enabled) if the owner still wants it as one
  arm; research provides no affirmative support.

## Cross-cutting facts the specs must encode

- **Financing realism**: every levered arm charges FF+150bps on borrowed
  notional, fail-closed (divergence #16's lesson). This is what separates
  these tests from every published number above.
- **Fixed periods**: 2000-06, 2007-13, 2014-19, 2022-26 (§57 comparability;
  no 2020-21 file). GEM can't reach period 1; NFCI can't reach 1-2.
- **Claims**: all DIAGNOSTIC at registration (spend no K). Pre-committed
  promotion rules license at most 1–2 EDGE registrations via the audited
  `--override-freeze` supersession route, argued before any run.
- **The §48/§49 trap**: 4 periods × few switches = handfuls of independent
  bets. Reading rules must count episodes, not just return deltas.
