# A 130/30 long/short book, forward-validated

**Status:** design approved 2026-08-04. Not implemented.
**Goal:** beat the S&P 500 on total return.

## Why

The bot has never been shown to beat SPY, and two independent measurements say
so.

**Live**, over its whole record (2026-07-17 → 2026-08-04): Repete **+0.48%**,
SPY **+2.75%** — behind by **2.27pp**. Eight closed round-trips, five wins, mean
+2.30% per trade. At n=8 over 18 days this is not evidence in either direction,
but it is not support for "already winning" either.

**Backtest**: §50 pre-registered exactly this comparison and **REJECTED it in
all four periods**, losing to SPY on return in 2014–2019 and 2022–2026 — the two
windows where the benchmark is survivorship-clean. The single EDGE pass in
fifteen claims (§51) was explained by **+200.28pp** of survivorship in its own
results section.

So the target is to *make* it beat SPY. Chasing incremental signal improvements
would assume a working edge to improve.

## Constraints

**Validation is the live forward record only. No data purchase** (owner
decision, 2026-08-04). The §52 EDGE freeze stays.

The consequence has to be stated plainly, because it governs everything below:
**backtests become a negative filter and nothing more.** A FAIL on
survivorship-inflated data is decisive — the strategy lost *with* a tailwind. A
PASS means nothing, because the strategy still trades a survivor-selected
universe. All positive evidence now comes from live-forward trading, which is
survivorship-free by construction, free, and slow.

Two things follow.

**Trade rate becomes a design variable.** Separating a real edge from noise needs
a few hundred observations. At ~0.4 trades/day that is one to three years.
Anything that raises independent observations per month shortens it — and that is
the same lever as §49's breadth finding, since IR = IC × √BR.

**Every change restarts the evidence clock.** The forward record only produces
evidence if the configuration holds still. Iterating monthly means never
accumulating n on anything.

This work supersedes the 2026-08-02 hands-off decision by explicit owner
direction, recorded here so the change reads as a decision rather than drift.

## The tension, and why 130/30

Market-neutral removes beta — the thing that produces SPY-like returns. A
net-zero book in a market compounding ~13%/yr needs 13%+ of *pure alpha* just to
tie, which is top-decile hedge fund territory from a bot that is 1-for-15 on edge
claims.

**130/30** keeps net ≈100% market exposure and adds the short leg as an alpha
overlay. The goal stays reachable and the existing long book keeps its evidence.

## Architecture

`xsmom` is the home for the short leg and is already half-built: it ranks the
universe cross-sectionally, buys `buy_top_fraction: 0.25`, and exits below
`exit_below_fraction: 0.5`. The symmetric short half was never written. It is
also `enabled: false` today, so turning it on costs no live evidence.

| leg | source | target |
|---|---|---|
| long ~130% | existing ensemble (`ma_crossover`, `tsmom`, `meanrev`), **unchanged** | keeps today's behaviour and evidence |
| short ~30% | **`xsmom` bottom fraction only** | net ≈100%, gross ≈160% |

Confining shorts to one strategy makes the leg attributable and revertible:
`xsmom.enabled: false` restores today's bot exactly.

### ETFs leave the cross-section

The universe is 38 symbols, **8 of them ETFs** (SPY, QQQ, DIA, IWM, XLK, XLF,
XLE, XLV). Shorting XLK while the ensemble is long AAPL/MSFT/NVDA is not alpha —
it unwinds your own longs. §49 measured the overlap: **11 of 13 pairs at or above
the 0.85 correlation threshold involve an ETF, and only 2 of 435 stock-vs-stock
pairs reach it.**

Ranking a sector basket against its own constituents is a category error
regardless of shorting, so this is a fix to `xsmom` in its own right. Thirty
rankable names remain; the bottom 25% is roughly seven or eight shorts.

### Signal contract

`Signal.action` is `"buy" | "sell" | "hold"`, where `"sell"` means *close a
long*. Add `"short"` and `"cover"` rather than overloading `sell`, so no existing
call site changes meaning silently.

## Three hazards to fix before any short trades

Each is a live risk on the side where risk is unbounded. None is optional.

**1. Shorts would have no stop.** `broker.bracket_market_order` is hardcoded
`side=OrderSide.BUY` (`src/broker.py:153`). A short's loss is unbounded, so an
unbracketed short is the most dangerous thing in this design. Generalise the side
and invert the geometry — stop *above* entry.

**2. The exposure cap would stop binding.** `risk.pure_checks` sums
`market_value` for gross (`src/risk.py:1116`). Alpaca reports shorts as
**negative**, so a long and a short net toward zero: the rail reads "no exposure"
on a book carrying 160% gross. Use `abs(market_value)`.

**3. Invariant #2 inverts.** "The LLM may only VETO or DOWNSIZE, never enlarge"
is defined on positive quantities. On a short, downsize must mean *toward zero*.
Without an explicit sign rule the judge can enlarge risk while appearing to
shrink it.

### New rail: net exposure band

Nothing today constrains net exposure. Add `risk.net_exposure_pct: {min, max}`
(80–120) checked in `pure_checks` beside gross, refusing any order that would
push net outside the band. This is what makes "130/30" a fact rather than an
intention.

### Preflight

`broker.account()` captures only equity, cash, last_equity and buying_power. Add
`shorting_enabled` and `multiplier`, and have `preflight.py` refuse to start when
shorting is disabled at the broker while `xsmom` shorts are enabled — otherwise
every short silently fails and the book quietly reverts to long-only.

## Divergence #16 — paper shorting is free, real shorting is not

Alpaca paper charges no stock-loan fee, maintains no hard-to-borrow list, and
never issues a buy-in. Real shorts pay 0.3–3%/yr on easy names, can spike far
higher, and can be recalled at the worst moment.

So **any measured short-leg alpha is overstated by a borrow cost that was never
charged**, and the paper/live gap runs in the flattering direction. Register it
in `docs/divergences.md` with the direction of bias stated. It cannot be closed
without a borrow-cost model or a real margin account.

## Validation plan

**Negative filter first.** Register a **DIAGNOSTIC** gate — spends no Bonferroni
budget, respects the §52 freeze, same shape as §48/§49 — measuring whether the
short leg *behaves*: does it trade, what net and gross does it actually run, does
the new rail bind, how does deployment change. If the 130/30 configuration FAILS
`beats_benchmark_symbol` on survivorship-inflated data, it is dead before it
costs a day of live time.

**Attribution by leg, from the first fill.** Every fill and outcome records which
leg produced it. Without this the short leg cannot be evaluated at all — it just
blends into the book.

**Then freeze and accumulate.** The short leg roughly doubles expressible
decisions, from "own it or don't" to "long, flat, or short" — the breadth
increase §49 called for, and faster n. Target ~200 closed round-trips before
reading a result.

**K stays 15.** No EDGE claim is registered here.

One gap to name rather than gloss: **the gate harness cannot currently score a
live record.** `run_gate.py` scores a backtest over a snapshot; there is no rule
that reads `memory/ledger.jsonl`. So "an EDGE claim on the forward record" needs
machinery that does not exist — a `live_record` source and clauses that operate
on realised round-trips. That is its own piece of work, best done as a METHOD
claim once there is enough n to be worth scoring, and it is deliberately **out of
scope here**.

## Phasing

The scope spans the signal contract, the broker, the risk rails, the judge and
the simulator, so it does not belong in one undifferentiated change. Three
phases, each independently mergeable and each leaving the bot in a working state:

**Phase 1 — make shorts survivable, ship nothing that shorts.** The three
hazards, the net-exposure band, the preflight check, and the signed position
model through `risk` and `backtest`. No strategy emits a short yet, so live
behaviour is byte-identical. This is where the mutation testing concentrates.

**Phase 2 — the `xsmom` short leg.** Short/cover actions, ETF exclusion from the
cross-section, leg attribution on fills and outcomes. Still `enabled: false`, so
still no live change.

**Phase 3 — the DIAGNOSTIC, then enable.** Register and run it, write it up, and
only then flip `xsmom.enabled: true` and start the evidence clock.

If Phase 3's diagnostic says the long book cannot deploy near 130%, stop there:
the ratio was the premise, and Phase 1's safety fixes are worth keeping anyway.

## Files

| file | change |
|---|---|
| `src/strategies/xsmom.py` | short/cover leg; exclude ETFs from the cross-section |
| `src/strategies/base.py` | `Signal.action` gains `"short"` / `"cover"` |
| `src/broker.py` | generalise bracket side; capture `shorting_enabled`, `multiplier` |
| `src/risk.py` | `abs()` on gross; net-exposure band; signed sizing |
| `src/main.py` | route short/cover; leg attribution on fills and outcomes |
| `src/judge_model.py`, `src/llm.py` | invariant #2 sign rule |
| `src/preflight.py` | refuse to start when the broker has shorting off |
| `src/backtest.py` | signed positions, so the DIAGNOSTIC can score |
| `config.yaml` | `xsmom` short params; `risk.net_exposure_pct` |
| `docs/divergences.md` | #16 |
| `research/specs/<id>.yaml` | the DIAGNOSTIC — section number claimed at registration, not reserved here (§42→§43→§44: a number belongs to whoever registers first) |

## Verification

1. **Mutation-test all three hazard fixes** (`bot-prove-it`): break each, confirm
   the *named* test goes red, restore, verify byte-identical `md5 -q`. An
   unbracketed short must be impossible; a long+short book must report gross
   ≈160% and not ≈100%; the judge must not be able to enlarge a short.
2. **Paired tests in both directions**, as `tests/test_edge_freeze.py` does. The
   net band must bind when breached and not bind inside it.
3. **`xsmom.enabled: false` restores byte-identical behaviour** — the rollback
   property, in the style of `test_disabled_gives_byte_identical_context`.
4. **No ETF ever appears in a short signal**, asserted directly.
5. **A paper drill on one short**: entry, bracket present at the broker, cover,
   ledger and attribution correct, with `REPETE_ALERTS_OFF=1`.
6. **Full suite green**, then the DIAGNOSTIC run and written up.

## Risks, stated before the fact

- **The cross-section is thin.** Thirty rankable names is small for a strategy
  whose academic form uses hundreds. The bottom-decile short may be noise.
- **130/30 uses margin.** Gross 160% sits inside Reg T's 2×, but margin calls
  become possible and they arrive at drawdown troughs — the objection §51
  pre-registered about levering a long book.
- **Shorts gap and squeeze.** The loss distribution is not the mirror of the long
  side.
- **The long book may not reach 130%.** §48 measured deployment as low as 4.72%
  with the drawdown rail engaged. If the long leg cannot get near 130%, the ratio
  is aspirational and net will drift. Measured first, in the DIAGNOSTIC, before
  any short trades.
- **The base rate is 1 pass in 15.** Adding a leg does not change that priors
  should be low. The design is built so a negative answer is cheap to reach and
  trivial to revert.
