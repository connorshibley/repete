# Source review: oria.one, "Institutional-Grade Claude Skills for Finance"

**Reviewed:** 2026-08-03
**Source:** `https://www.oria.one/resources/institutional-grade-claude-skills-for-finance`
**Verdict: nothing adopted. §46 had already written the rule that decides this.**

## What the page is

18 copy-paste markdown prompts for Claude Projects, arranged in six phases, plus
a pitch for Oria (a PowerPoint add-in). No code, no datasets, no downloads.

| Phase | Skills |
|---|---|
| Frame | Investment Question Framer · Assumption Auditor · Signal Separator |
| Map | Sector Mapper · Competitive Position Analyst · Demand & Margin Pool Scanner |
| Value | DCF Builder · Comparable Company Analyst · Precedent Transaction Screener |
| Model | Three-Statement Model Builder · Working Capital Analyst · Returns & Debt Schedule Builder |
| Test | Covenant Tester · Stress Scenario Builder · Exposure Mapper |
| Report | Investment Memo Writer · Board Paper Drafter · IC Narrative Builder |

These are **deal and fundamental-analysis workflows** — private equity, M&A,
credit, investment-committee papers. This bot is a systematic swing strategy on
~38 liquid tickers, daily bars, 13–21 day holds. Different job, different
horizon, different instrument.

## Why it was already decided

§46 (2026-07-30) came from the same class of source — a "use Claude for finance"
resource — and committed its rule *before* seeing any result:

> - **all three pass** → a §46 may be pre-registered and backtested on fundamentals;
> - **any one fails** → fundamentals are **live judge context only** — judge-only,
>   so under invariant #2 they can veto or shrink and can never create a trade.

`scripts/probe_fmp_lookahead.py` ran that test. All three checks returned
**UNDETERMINED** — the free FMP tier answers the deciding endpoints with HTTP
402 — and the probe treats undetermined as failure, because an unanswerable
question is not a passed one.

**The pre-registered rule has therefore already fired, and it selected the
judge-context-only branch.** Ten of these eighteen skills (all of Value and
Model, most of Map) produce exactly the fundamental outputs that branch
constrains. Under it, a perfect DCF could still only ever cause the judge to
decline a trade — it cannot create one, so it cannot create an edge.

§46 also rejected, by name, the two things that reappear on this page:

- a **screener that ranks names for you** — "unregistered discretionary
  selection, the exact thing §32/§33 existed to test";
- **"Claude adjusts your strategy over time"** — a strategy change adopted by
  enabling it and watching.

## Skill by skill

- **DCF / comps / precedent / three-statement / working capital / debt schedule** —
  need fundamentals this repo does not have, and are constrained to judge context
  even once it does.
- **Covenant Tester** — a credit instrument. There are no covenants on SPY.
- **Stress Scenario Builder** — the version here is §37: a five-clause pass mark
  frozen at a spec hash *before the 2007–2013 crisis snapshot finished building*,
  then run against it. A prompt-written scenario is strictly weaker, and
  unfalsifiable.
- **Assumption Auditor / Signal Separator** — methodology, and the
  pre-registration machinery (frozen spec hashes, a Bonferroni budget,
  exposure-matched baselines, a rejection record it refuses to launder) is a much
  stronger version of the same instinct.
- **Investment Memo / Board Paper / IC Narrative** — human-facing documents; the
  repo already writes for a non-technical reader throughout.

## The one thread with something behind it

**Exposure Mapper.** Correlation is capped only *pairwise* here —
`config.yaml` `correlation_cap`: `max_correlated: 2` at `threshold: 0.85` over a
60-day lookback — and nothing maps sector or factor concentration across the
whole book. §22 recorded that index ETFs get structural first refusal on slots,
so SPY/QQQ/DIA/IWM are over-represented by construction rather than by choice.

**Recorded as an unregistered candidate, not adopted.** The page contributes a
prompt, not the work: the work would be a pre-registered DIAGNOSTIC against the
existing snapshots, in the shape of §40 and §45.

## Standing

Nothing enabled. No threshold moved. `mode: paper` unchanged. Fundamentals
remain blocked on the owner's call about a paid FMP plan; if bought, their
licensed use is already fixed by §46's rule and is judge context only.
