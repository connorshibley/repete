---
name: bot-survivorship-audit
description: Use before making, reading or believing ANY performance claim in the three trading bots — whenever a backtest return is compared to a benchmark, whenever a snapshot under data/snapshots/ is used, whenever someone says a strategy "beat buy-and-hold", and whenever building or evaluating a new data source. The measured inflation runs up to +200pp and has already explained away one of the record's two EDGE passes.
---

# Survivorship: the number that eats every backtest here

Every snapshot in `data/snapshots/` is built by `build_snapshot.py` from
`index_constituents()`, which reads **current** Wikipedia index membership. The
companies that failed are missing by construction. This is not a caveat. On some
windows it is larger than the entire measured effect.

## The measured table — `scripts/survivorship_check.py`

Equal-weight-500 against SPY over **identical bars** (SPY is in the universe and
an ETF already reflects constituent changes, so the gap is the artifact):

| snapshot | SPY | equal-weight | inflation |
|---|---|---|---|
| 2000–2006 | +8.68% | +138.74% | **+130.06pp** |
| 2007–2013 | +51.34% | +96.80% | +45.46pp |
| 2014–2019 | +97.97% | +101.94% | +3.97pp |
| 2022–2026 | +64.39% | +54.80% | −9.59pp |

The gradient is exactly what survivorship predicts: worst in the oldest window,
near zero recently.

**And the worst case is not in that table.** The 38-symbol live universe
(`bars_2020-01-01_2026-07-10.json.gz`) carries **+200.28pp** — worse than
2000–2006 — because those names are in `config.yaml` *precisely because* they
are today's winners. §51's spec called that arm "the most independent"; that was
wrong. **Unmined is not unbiased.**

## What this already invalidated

- **§48**: with the drawdown rail removed the bot cleared every clause in all
  four periods. Against clean SPY it beats only in the two *poisoned* periods
  and loses in both clean ones.
- **§51**: the first of only 2 passes in 16 EDGE claims, and the one this audit
  broke — its own results section showed +200.28pp was large enough to explain
  it. (§72 is the other, and it ran on `data/pit/`, not on a snapshot; this
  finding does not reach it.)
- **§52**: EDGE claims on `data/snapshots/` are now mechanically frozen.

## Which rule to use

- ✅ **`beats_benchmark_symbol`** — return vs a real ticker's buy-and-hold.
  Survivorship-free on the benchmark side.
- ✅ **`beats_benchmark_risk_adjusted`** — the same, drawdown-matched. Read its
  four FAIL-not-skip guards in `run_gate.py` before using it.
- ❌ **`beats_exposure_matched`** — §48 showed any long-biased strategy passes
  it. It is not a bar.
- ⚠️ **`beats_buy_hold`** — survivorship-*matched*, not survivorship-free. The
  strategy and the benchmark share the same poisoned universe, so it answers
  "did stock-picking help?" and never "did this make money?"

**The asymmetry that survives all of this:** the strategy still trades a
survivor-selected universe even when the benchmark is clean. So a **FAIL is
decisive** (it lost with a tailwind) and a **PASS is not**.

## The ticker-reuse trap — worse than the bias it fixes

`scripts/probe_delisted_coverage.py` asked whether a survivorship-free universe
is buildable from the current vendor and **REFUSED**, for two separate reasons:

1. yfinance returns **zero rows** for SIVB, FRC, TWTR and ATVI. The losers are
   simply absent — that is the bias itself.
2. **SBNY returns 475 bars that all start 2024-08-15**, seventeen months after
   Signature Bank was seized, still labelled "Signature Bank", on exchange PNK.

A builder checking only "did we get bars?" would splice in that OTC successor
and produce a dataset that **never observes the collapse**. Check 1 alone cannot
see this. That is why the probe scores coverage on `len(pre) > 0` — bars that
predate the delisting — and not on `len(rows) > 0`. A surviving mutation found
exactly that: the verdict stayed right while the report printed "coverage: PASS"
for a name whose every bar postdates its own death.

## Evaluating a candidate data vendor

The probe **is** the acceptance test. Point `--source` at a candidate (Norgate,
Sharadar, Polygon, CRSP) and it answers. It needs a control ticker to pass, or
it returns `UNDETERMINED` and exits 2 — because "everything returned nothing"
may mean the network is down, not that the vendor lacks delisted data.

Buying the vendor is the owner's decision, same shape as the §46 FMP decision.
Do not make it.

## Two more things that are not survivorship but read like it

- **Breadth** (§49): 20 open positions are **3.24–6.19 independent bets**; one
  factor is 53% of book variance; the IR ceiling is ~0.57× an independent book.
  Under IR = IC × √BR that is a ceiling no signal work lifts.
- **Universe mismatch** (§49): the wide 500-stock snapshots contain **no sector
  ETFs**. Every wide-snapshot gate scored a universe the live bot does not
  trade. Use the 38-symbol snapshot when the question is about live.
