# Beat-SPY research program — owner report (2026-08-12)

**Ask**: deep research on beating the S&P 500, backtests, new strategy
candidates. **Scope you pinned**: free PIT data only, long-only ≤1.5x with
honest financing, SPY total return as the bar.

## Bottom line

**No candidate beat SPY total return under honest conditions.** Sixteen
pre-registered specs across four families ran against frozen reading rules;
every family closed by its own rule. This matches both the external
evidence (nothing in the published literature survives its own
post-publication decade at your constraints) and this repo's base rate
(EDGE: 1 pass in 15, that one explained by survivorship).

| candidate | beat SPY TR | resolution |
|---|---|---|
| trend_hold (Gayed 200-DMA, 1.5x) | 0 of 4 periods | closed; grid also showed a fitted-looking interior optimum |
| tom_tilt (turn-of-month, 1.5x) | 0 of 4 | closed — but see the latch note |
| vol_lever (vol-managed, 1.5x) | 1 of 4 | closed (the pass: +68% vs +10% in 2000-06 — on TWO trades; rule pre-excluded it) |
| gem (dual momentum, book spec) | 0 of 3 | family closed — falsification landed (−8.8% vs +51.3% in its best-suited period) |

## The real discovery

**The bot's drawdown latch is a one-way trapdoor for single-symbol
strategies.** In 2000-06, 867 of 876 trend_hold signals were blocked by the
latch; deployment was 2.5% on a strategy meant to be ~100% invested. Once
equity drops ~10% from its high-water mark, entries lock; with one symbol
and a cash book, equity can never recover to unlock. The latch was designed
for the 38-name ensemble. **What this wave closed is the candidates
as-wired, not their mechanisms** — most were never allowed to trade. A
re-test with the latch declared per-arm is a legitimate new registration if
you want to spend it (your call; §53's precedent forbids claiming relief
retroactively).

## What you got either way

- A margin/financing model in the simulator (leverage now testable, borrow
  never free), four new strategies shipped disabled, a certified 20-ETF PIT
  universe + GEM snapshots + a T-bill rate series, ALFRED/EDGAR lookahead
  probes (§63), and 16 frozen specs with verdicts on the record.
- EDGAR insider signals: refused twice (probe + literature) — parked.
- **Still open, unspent: the macro-vintage venue (s68)** — needs your free
  FRED API key, then the vintage probe decides if it can be tested at all.

## Nothing is enabled

`mode: paper`; every new strategy ships `enabled: false`; live strategies
stay frozen for the decay monitor (n=20, ~early Sept). Enabling anything is
your action alone.
