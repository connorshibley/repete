# Backtester vs live: the divergence register

Every gate verdict in `knowledge/backtest_candidates.md` rests on the simulator
being a faithful model of the live bot. Where the two differ, a verdict measures
a bot that does not exist.

Twelve such differences have been found. Until 2026-07-28 they existed **only as
prose scattered across forty sections of the gate ledger** — there was no list,
so "how many are open?" had no answer, and #8 could sit closed-on-paper and open
in fact for three days without anyone noticing. This file is the list.

**Open as of 2026-07-29: none.** That is a statement about the twelve found, not
a claim that twelve is all there are. #10, #11 and #12 were all discovered by
reading code for an adjacent task, within four days of this register being
created — and #12 had been sitting in `data/snapshots/MANIFEST.json` in plain
English the whole time.

**A divergence is CLOSED only when a test would fail if it reopened.** "Fixed in
code" is not closed; the repo has been wrong about that before.

| # | Divergence | Status | Closed by |
|---|---|---|---|
| 1 | Heat and correlation caps missing from the simulator (§13) | **closed** | `src/backtest.py:593` — `pure_checks` is the same function live calls |
| 2 | Fill timing: sim filled at signal-bar close, live at next open (§19a) | **closed** | `tests/test_backtest.py` fill-timing cases |
| 3 | Per-strategy vs ensemble counters — sim watched one strategy, live counts all four (§19b) | **closed** | `simulate_ensemble` (`src/backtest.py:590-594`), `tests/test_ensemble_sim.py` |
| 4 | Symbol scan order: sim alphabetical, live config-order. Moved OOS return 2.66pp (§22) | **closed** | `risk.scan_order` (`src/risk.py:501-514`), shared by both paths |
| 5 | Relative-volume filter re-implementation risk | **pre-empted** | Single implementation, `src/risk.py:325-327` |
| 6 | Credit-gate re-implementation risk | **pre-empted** | Single implementation, `src/risk.py:430-431` |
| 7 | Repository vs reality — the running checkout was 57 commits behind the reviewed code (§26) | **closed** | `src/deploycheck.py`, `tests/test_deploycheck.py` (22 tests) |
| 8 | **The LLM judge is absent from the simulator** | **closed 2026-07-29** | `tests/test_judge_is_declared_not_assumed.py` |
| 9 | Drawdown rail existed on the live path only | **pre-empted** | `risk.drawdown_pct` shared (`src/risk.py:158-169`, `backtest.py:348,679`) |
| 10 | **The simulator's equity peak advanced only on buy bars** | **closed 2026-07-28** | `tests/test_sim_peak_tracks_every_bar.py` |
| 11 | **Live sampled the equity peak only when an order was attempted** | **closed 2026-07-29** | `tests/test_quiet_cycle_still_ratchets_the_peak.py` |
| 12 | **Live read RAW bars while every snapshot is split/dividend adjusted** | **closed 2026-07-29** | `tests/test_bars_are_split_adjusted.py` |

---

## #8 — the judge is modelled and switched off

`src/judge_model.py` was built for §29 (2026-07-26) specifically to close this,
and `config.yaml` shipped `judge_model.enabled: false`. `judge_model` appeared
in **no** file under `research/specs/` and nowhere in `scripts/run_gate.py`.

So §35, §37, §38, §39, §40 and §41 — every gate run *after* the fix was
written — measured a bot with no judge, while live the judge downsizes **58.1%**
of buys and vetoes **2.4%** at a mean scale of **0.729** (164 judged buys in
`memory/ledger.jsonl`). The simulator was sizing entries roughly a third larger
than the live bot would on the majority of trades.

This one is instructive: the code landed, the divergence was declared closed,
and the switch was never flipped. That is why this register records the *test*
that closes each entry rather than the commit that fixed it.

### CLOSED 2026-07-29 (W2-1)

Closed in two independent places, because a flag alone would repeat the
original failure in a new costume:

1. **Declared, not defaulted.** `gatespec` accepts a top-level `judge_model`
   bool and `scripts/register_gate.py` REFUSES to freeze any spec without one.
   The setting enters the canonical hash, so a spec registered judge-off cannot
   be re-run judge-on and still pass the freeze check.
2. **Proven, not claimed.** `judge_model` counts what it did
   (`sized`/`cut`/`vetoed`/`zeroed`), and `run_gate.py` refuses to write a
   verdict when a spec says `judge_model: true` while those counters say the
   model never acted. A verdict labelled judge-on and measured judge-off is
   worse than no verdict, because it looks like this entry got closed.

**Absence is not `false`.** §35-§41 predate the field and keep their frozen
hashes, so they still re-execute byte-identically — the record stays
reproducible. `run_gate.py` banners those runs as judge-less rather than
defaulting them quietly. A spec that says `false` made a choice; a spec with no
field did not know there was one, and flattening the two would erase the whole
finding.

**What is NOT claimed:** the pre-W2-1 verdicts are not withdrawn or re-scored.
They remain valid measurements of a judge-less bot, and must be read as such.
Calibration also refreshed here: n=146 (through 2026-07-23, pre-§29 sizing) to
n=164 (through 2026-07-28). The judge cuts *more* than the stale file said.

## #10 — the peak that only moved on buy bars

Found 2026-07-28 while reading `src/backtest.py` for §41. Previously
undocumented anywhere.

`sim_peak = max(sim_peak, acct.equity(last_close))` sat inside the
`if action == "buy":` branch of both `simulate()` and `simulate_ensemble()`, so
a bar that only sold — or that transacted nothing — never advanced the peak.
Live ratchets inside `pre_trade_checks`, which runs for sells too, and
`src/risk.py:828-833` states the reason outright: *"the peak must keep tracking
even while entries are blocked, otherwise the breaker could never clear
itself."*

Live therefore sampled the peak on strictly more occasions than the simulator.
Because the drawdown rail measures distance *below* the peak, a higher peak
means a deeper measured drawdown means more entries blocked — so **the §40 latch
bit harder in production than in any backtest that measured it**, and the error
ran in the reassuring direction, which is how it survived.

Fixed by making both correct rather than by making one imitate the other's
artifact: the ratchet moved to the top of the bar loop and now runs
unconditionally. A high-water mark sampled only when the book happens to
transact is not a high-water mark.

**Note for §41:** live still samples the peak only when an order is *attempted*
(`pre_trade_checks` is not called on a cycle that places nothing). That is the
same class of error, one level up, and it is in scope for §41 rather than fixed
quietly here. — **Now #11 below, closed 2026-07-29.**

## #11 — live sampled the peak only when an order was attempted

Predicted by #10's own note and closed 2026-07-29 (W2-2).

§31 put `risk.update_high_water` inside `risk.pre_trade_checks`, and reasoned
explicitly about running it for sells as well as buys — *"the peak must keep
tracking even while entries are blocked"* (`src/risk.py:899-902`). That
reasoning is correct and insufficient: `pre_trade_checks` has exactly one caller
(`main.py`'s order loop) and fires **only when an order is actually attempted**.
A cycle producing no buy and no sell never touched the mark at all.

**Direction of the error.** The peak only ratchets up, so equity earned on a
quiet day was invisible to it. A book drifting up over a week of holds keeps
last week's peak, and the first 10% drawdown is then measured from a stale LOW
peak — the breaker fires **late**. This runs OPPOSITE to #10, which made the
sim's latch bite *less* than live. The two errors were not cancelling: one was
in the simulator and one is in production.

**Where it bites.** Invisibly on the 500-symbol snapshots, where something
trades on virtually every bar — which is exactly why #10's census came back
byte-identical. It bites on the book this bot actually runs: **38 symbols, with
whole days where nothing is entered.** Same shape as #10: the error hid in the
place nobody measures.

Closed by ratcheting once per cycle in `src/main.py`, immediately after the
account is read fresh from the broker (invariant #4) and before the first early
return — the daily-loss kill switch and the HALT check both return before the
order loop, so any later placement would leave the same hole. `pre_trade_checks`
still ratchets per order; this is the floor under it, and `update_high_water` is
max-based so running both is a no-op the second time.

## #12 — live read raw bars, every snapshot is adjusted

Found 2026-07-29 (W2-3).

`alpaca-py`'s `StockBarsRequest.adjustment` defaults to `None`, which the API
treats as **raw**. Neither `broker.bars()` (the live path) nor
`backtest.fetch_bars()` passed it. Every frozen snapshot, meanwhile, is split-
AND dividend-adjusted: `scripts/build_snapshot.py` uses yfinance
`auto_adjust=True`, and `scripts/build_intraday_snapshot.py` already used
`adjustment=Adjustment.ALL`.

So **the live bot has been reading a different price series from the one every
gate verdict was scored on.**

**Direction of the error, and why it is not small.** A 2-for-1 split in a raw
series is a 50% overnight gap that never happened. It collapses an SMA, spikes
ATR, and can fire an entry signal or trip a stop on an event that did not change
the value of a single share held. The bot cannot distinguish that gap from a
crash. The effect is not a consistent bias in one direction — it is noise
injected at unpredictable moments, which is worse than a bias because it cannot
be corrected for.

**This was already written down.** `data/snapshots/MANIFEST.json` records the
first intraday build going out RAW and carrying **11 unadjusted splits** "that
the simulator read as overnight collapses — it voided a gate run." The fix was
applied to that one builder and never propagated to the live path. Same failure
shape as #8: the lesson was learned, recorded, and applied in exactly one place.

Closed by passing `Adjustment.ALL` in both request builders.
`tests/test_bars_are_split_adjusted.py` asserts both, refuses `SPLIT`-only
(which would leave a dividend mismatch), and asserts the snapshot builder still
auto-adjusts — so the alignment cannot be re-broken from either side.

**This changes live inputs.** It is a data-correctness fix, not a strategy or
threshold change, so it is not gated — but it is the one item in W2 that alters
what the production bot sees, and it is recorded here rather than mentioned in
a commit message.

---

## How to add an entry

1. Number it. Numbers are permanent; a superseded entry stays with its status
   changed, never deleted.
2. State which side was wrong and **in which direction** the error ran. A
   divergence that made the bot look worse is a different risk from one that
   flattered it.
3. Name the test that closes it. If there is no test, the status is OPEN
   regardless of what the code does today.

## #13 — live is trading in a state the backtest says is rare

**Found** 2026-07-29 (§45). **OPEN.**

The `max_drawdown_pct: 10.0` rail latches trading off on a peak-to-trough
drawdown. In the 2022-2026 snapshot — the period closest to the current regime —
that rail blocked **96.86% of every buy signal the strategies emitted** (237,522
of 245,212). It is the single dominant constraint on the backtest, and it is
similarly dominant in 2000-2006 (74.5%) and 2007-2013 (93.8%).

Live has been running since 2026-07-14. Its equity drawdown reached **1.05%
today** and had never exceeded **0.10%** before that. The latch has never been
close to engaging.

So live's 14 open positions and 4 closed round-trips were all generated in the
~3% of conditions where the backtest's dominant rail is quiet. **The live record
and the backtest are not samples from the same distribution**, and a comparison
between them right now is comparing the calmest stretch of one against the
average of the other.

This is not a code defect — nothing is wired wrongly, and W2-2 already closed the
peak-sampling divergence (#11) that would have been. It is a *sampling* fact
about the live record, and it is recorded here because the natural next step
after ≥30 closed trades is exactly the comparison it invalidates.

### What would close this

A live record that spans at least one period in which the drawdown rail actually
engages, so that the live sample includes the state the backtest spends most of
its time in. Concretely: a `risk_rejection` with rail `drawdown` in the live
ledger, and the closed-trade count measured separately either side of it.

Until then, any statement of the form "live is tracking / beating / lagging the
backtest" is comparing unlike things, and **§45's table plus this entry are the
citation that says so.**


## #14 — the live judge reads news memory; the backtest's judge model cannot

**Opened 2026-07-30 (W7). Deliberate, not a defect — and open by construction.**

`memory.context_for_llm()` now includes a NEWS MEMORY block: a symbol's
accumulated news mentions over `news.memory.retention_days`, plus the realised
P&L of any prior news-linked closed trades. Every live judge call sees it.

`backtest.py` drives `judge_model`, which cannot. There is no archive of what
the newswire and eleven RSS feeds said on an arbitrary day in 2007, and the
snapshots hold prices, not headlines. Reconstructing one from a modern LLM's
knowledge of how those years turned out would not be a news feed — it would be
hindsight wearing a timestamp, and it would inflate every backtest it touched.

So the two judges see different inputs, on purpose.

### What this invalidates, precisely

Every backtest number produced from 2026-07-30 onward describes a judge with
**strictly less context** than the live one. That direction matters:

- the sim judge cannot veto on news, so the sim will approve trades live may
  refuse — backtests are, if anything, **optimistic** relative to live;
- it is not symmetric. Under invariant #2 the judge may only veto or shrink, so
  news memory can subtract live trades and can never add one. There is no
  mechanism here by which live outperforms sim *because of* this block.

That asymmetry is the reason this ships without a gate: the worst case is a
live bot that trades less than its backtest, which is the safe direction to be
wrong in.

### What would close this

Either of:

1. **A real point-in-time news archive** covering a snapshot period, replayed
   into `judge_model` so both judges read the same thing. Nothing short of
   genuinely contemporaneous text qualifies — see the hindsight problem above.
2. **`news.memory.enabled: false`**, which restores byte-identical judge
   context. That is asserted by
   `tests/test_news_memory.py::test_disabled_gives_byte_identical_context`, not
   merely intended, and it is the rollback path if a future §46 rejects this.

### The test that would fail if this silently closed

`test_disabled_gives_byte_identical_context` fails if the block stops being
suppressible, and `test_the_lesson_block_does_not_shrink_when_news_is_present`
fails if it starts displacing other context. Neither can detect a sim that
*claims* to model news memory without doing so — which is why closing this
requires the archive in (1), not a code change.

### Not a claim of value

**This is not an EDGE claim.** Nothing here has been gated, and the EDGE tally
is unchanged by it — `knowledge/backtest_candidates.md` owns that count, and it
is not restated here. The outcome half exists
to produce the two populations a §46 would compare — trades entered with news
present versus without — and there are currently **4 closed trades**. A gate
scored on 4 trades is theatre, so no gate is registered.
