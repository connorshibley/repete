# Backtester vs live: the divergence register

Every gate verdict in `knowledge/backtest_candidates.md` rests on the simulator
being a faithful model of the live bot. Where the two differ, a verdict measures
a bot that does not exist.

Eighteen such differences have been found. Until 2026-07-28 they existed **only as
prose scattered across forty sections of the gate ledger** — there was no list,
so "how many are open?" had no answer, and #8 could sit closed-on-paper and open
in fact for three days without anyone noticing. This file is the list.

**Open as of 2026-08-06: #13, #14, #15, #16 and #18.** All five are open *by
construction* rather than by defect — a sampling fact about the live record, two
judge inputs the simulator has no mechanism to represent, a cost the paper
broker does not charge, and a fill-session hazard that only materialises when
the cycle runs long. **#17 is a DEFECT, found and closed the same day**, and
it is the largest correction here: the simulator let every strategy enter every
symbol in the snapshot.

That count has been wrong before, in the direction that matters. This file said
**"Open as of 2026-07-29: none"** while #15 had been open since the initial
commit on 2026-07-16 — through the creation of this very register — and nobody
noticed for nineteen days. So read any "open as of" line as a statement about
what has been *found*, never as a claim that the list is complete. #10, #11 and
#12 were all discovered by reading code for an adjacent task within four days of
this register being created; #12 had been sitting in
`data/snapshots/MANIFEST.json` in plain English the whole time; and #15 was found
while writing `.claude/skills/`.

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
| 17 | **The simulator ignored per-strategy universes — 500 names traded in the sim, 38 live** | **closed 2026-08-05** | `tests/test_sim_honours_universes.py` |
| 18 | **An overrunning cycle fills at the NEXT OPEN, and nothing checks the clock** | **open** | open by construction — see below |

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
## #15 — the live judge reads curated principles; the backtest's judge model cannot

**Registered 2026-08-04. Deliberate, open by construction — and open since
2026-07-16, which is the part worth reading.**

`memory.knowledge_block()` reads `knowledge/principles.md` (config
`llm.knowledge_path`) and prefixes it *"KNOWLEDGE (external, unverified — weigh
below realized evidence)"*. It goes into `memory.context_for_llm()`, so every
live judge call has seen it since the first commit.

`backtest.py` never imports `llm` — stated in its own docstring — and drives
`judge_model` instead. That module is a **distribution** stand-in: it reproduces
how often the judge cuts and by how much, from a hash of
`(symbol, timestamp, salt)`. It has no prompt, no text, and no reasoning. It
cannot represent a principle at all, in either direction. This is not a gap that
better modelling closes; there is nothing in `judge_model` for a principle to
attach to.

### Why it took until now to appear here

It did not open today. It opened on 2026-07-16 with the initial commit, sat
through the creation of this register on 2026-07-28, and was not caught when
that register recorded **"Open as of 2026-07-29: none."** That statement was
wrong, and the register's own header already says why it might be — *"a
statement about the twelve found, not a claim that twelve is all there are."*
Three of the first twelve were found by reading code for an adjacent task; so
was this one, while writing `.claude/skills/`.

The entry is dated by when it was **registered**, not by when it opened, and
both dates are given because only one of them is flattering.

### What this invalidates, precisely

Every backtest number this project has ever produced describes a judge with
**strictly less context** than the live one — not from 2026-07-30 as #14 says of
news, but from the beginning.

The direction is the same as #14's, and for the same reason:

- the sim judge cannot veto on a principle, so the sim approves trades live may
  refuse — backtests are, if anything, **optimistic** relative to live;
- it is not symmetric. Under invariant #2 the judge may only VETO or DOWNSIZE
  and may never enlarge or create a trade, so a principle can subtract live
  trades and can never add one.

That asymmetry is the whole reason `knowledge/principles.md` may be edited
without a gate in front of it: the worst case is a live bot that trades less
than its backtest, which is the safe direction to be wrong in. **It is not a
licence to put anything there.** A principle that induces a veto the evidence
does not support still costs real trades; it just cannot cost them in a way a
backtest would flatter.

### The budget it shares

`knowledge_block()` caps at `learning.max_context_chars // 4`. It has no budget
of its own — it takes a quarter of learning's. Growing `principles.md` past that
cap silently truncates the tail rather than failing, so length is a real
constraint and not a style preference. (This is the same trap
`news.memory.max_context_chars` was given its own budget to avoid.)

### What would close this

Nothing available. Closing it requires the real judge in the simulator, which
`backtest.py` refuses by design — a model call per bar would be slow,
non-reproducible, and contaminated by hindsight absorbed in training. Both of
the honest exits are the same shape as #14's:

1. **A judge model that reads text**, which would reintroduce every problem
   `judge_model.py`'s docstring lists; or
2. **`llm.knowledge_path` unset**, which restores byte-identical judge context.
   That is asserted by
   `tests/test_principles_context.py::test_unset_knowledge_path_gives_byte_identical_context`,
   not merely intended, and it is the rollback path.

### The test that would fail if this silently closed

`test_unset_knowledge_path_gives_byte_identical_context` fails if the block
stops being suppressible, and
`test_principles_do_not_shrink_the_lesson_block` fails if it starts displacing
other context. Neither can detect a simulator that *claims* to model principles
without doing so — which is why closing this needs (1), not a code change.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged; `knowledge/backtest_candidates.md` owns that count. Unlike news
memory, this one does not even produce two populations a future gate could
compare — the principles apply to every judgment, so there is no
without-principles arm in the live record. It is ungateable by construction as
well as unmodelled, and it is recorded here so that is a stated fact rather than
an oversight.

---

## #16 — paper shorting is free; real shorting is not

**Registered 2026-08-05, when `xsmom` gained a short leg. Open by construction,
and the bias runs in the flattering direction.**

Alpaca's paper environment charges **no stock-loan fee**, maintains **no
hard-to-borrow list**, and **never issues a buy-in**. A real short pays borrow
of roughly 0.3–3%/yr on an easy-to-borrow large cap, can spike far higher on a
crowded name, and can be **recalled by the lender at the worst possible moment**
— which is to say, exactly when the position is going against you and everyone
else is trying to cover the same name.

None of that exists in `src/backtest.py` either. The simulator models fills,
slippage and the rails; it has no concept of a financing cost that accrues per
day held, and no concept of a position closed by someone other than the bot.

### What this invalidates, precisely

**Any measured short-leg return is overstated by a cost that was never
charged**, in both the simulator and the paper record. The size of the overstatement
is not constant — it scales with holding period and with how hard the name is to
borrow, so it is largest on exactly the positions a momentum short leg is most
likely to hold: names that have fallen a long way and are crowded on the short
side.

The direction is unambiguous and it is the unsafe one. Unlike #14 and #15, where
the sim's judge has *less* context and therefore approves trades live would
refuse — leaving backtests conservative relative to live — this makes the short
leg look **better** than it can be. A short-leg result that clears a bar by a
small margin has not cleared it.

It also breaks a symmetry the long leg never had to think about: a long position
has no financing cost in a cash account and cannot be taken away from you. Every
number this project has produced so far is a long-only number, so this is the
first divergence that applies to one leg and not the other, and any per-leg
attribution has to carry the caveat rather than compare the two as like for like.

### What would close this

Two honest exits, and neither is available today:

1. **A borrow-cost model** — a per-day financing charge in `backtest.py` and in
   the live P&L, driven by a real borrow-rate source. That needs a data feed
   this project does not have, and inventing a flat rate would replace an
   unmeasured cost with a fabricated one, which is worse: it would look
   modelled.
2. **A real margin account**, which charges the real fee and enforces the real
   recalls. That is a live-trading decision gated behind ≥30 closed trades, ≥60
   days and attorney review, so it cannot be the near-term answer.

Until one of those lands, the mitigation is to state the bias whenever a
short-leg number is quoted, which is what this entry exists for.

### The test that would fail if this silently closed

None, and that is a statement about the divergence rather than an omission. There
is no assertion that can detect "the broker did not charge something", because a
fee that is never charged leaves nothing in the record to assert against. That
makes this **structurally weaker** than #13–#15, each of which at least has a
test pinning the mechanism it describes.

The nearest available guard is
`tests/test_xsmom_short_leg.py::test_the_shipped_config_still_ships_xsmom_disabled`
— which does not detect the divergence at all, only the fact that nothing is
currently exposed to it.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged and stays frozen under §52; `knowledge/backtest_candidates.md` owns
that count. This entry adds a required caveat to a future short-leg DIAGNOSTIC,
and asserts nothing about whether the leg works.

---

## #17 — the simulator ignored per-strategy universes

**Found and CLOSED 2026-08-05**, while reading `backtest.py` for the Phase 3
short-leg work. It is the largest correction this register has recorded.

`strategies.in_universe` has gated live entries since PR #90 introduced
per-strategy universes — `main.py:1685`, entries only, with a comment explaining
why it cannot live inside `generate()`. `simulate_ensemble` had **no
counterpart**. Every strategy could enter every symbol in the snapshot, so on a
500-name wide snapshot the incumbent traded 500 names in the simulator and 38
live.

### Why it hid

The only two universe-scoped strategies, `xsmom` and `reclaim`, are **both
cross-sectional**. `prepare_one` scopes their ranking, so `generate` answers
"insufficient history for ranking" for a symbol outside their universe and the
filter appeared to be working. That is a coincidence of those two being
cross-sectional, not a filter — a per-symbol strategy with a `universe:` key
would have traded the whole snapshot with nothing failing.

The three ENABLED strategies have no `universe:` key, which means the core 38.
They were the ones trading 500.

### What it cost, measured

Shipped configuration, `bars_wide_2022-01-01_2026-07-24.json.gz`, one arm, the
only change being this fix:

| | before | after |
|---|---|---|
| entry signals | 245,213 | 5,347 |
| **blocked by `drawdown`** | **243,814 (99.4%)** | **4** |
| executed | 29 | 969 |
| bars fully in cash | 92.39% | 4.55% |
| avg deployment | 4.09% | 75.18% |
| return | −5.26% | +107.77% |

**The mechanism is in the census, not in the return column.** With 500 candidate
names the signal flow tripped the 10% drawdown latch almost immediately, and
once latched it refused essentially every entry for the rest of the run — the
simulator spent 92% of its bars fully in cash. Confined to the 38 names it can
actually trade, the book never trips it: four drawdown blocks in the entire run.

### What this does and does not say about the record

§48 concluded that "the drawdown rail was masking measurement" and measured it
blocking 94.58% of buy signals. **That conclusion survives — but a large share of
the signal flow it was blocking came from names the live bot cannot trade.** The
rail was masking measurement of a bot that did not exist.

**The +107.77% is NOT evidence of an edge, and must not be read as one.** It is
a survivor-selected universe (§51 sized the same effect at **+200.28pp**), it is
not compared against SPY here, and §52's freeze stands unchanged. This entry
reports a simulator correction, not a result.

**No verdict is reopened or re-scored.** §43, §48, §50 and §51 stand as
measurements of the bot as simulated then. §41 set that precedent exactly: its
own simulator finding could have reopened ten verdicts and reopened none. The
consequence that IS recorded: **re-running any wide-snapshot gate today will not
reproduce its recorded numbers**, and the reason is this entry rather than
nondeterminism or a dependency bump.

### CLOSED by `tests/test_sim_honours_universes.py`

Six tests. Entries: a strategy scoped to the sector map cannot enter outside it,
an unrecognised `universe:` key enters nothing (inheriting `universe_for`'s
fail-to-nothing polarity), a symbol in no enabled universe is never entered, and
what the simulator entered is a subset of what `in_universe` — the same function
`main.py` calls, not a copy — allows.

Exits: `in_universe` starts answering False partway through a run, and positions
already open must still close on `strategy_sell` rather than being stranded to
`end_of_data`. That is the half that is easy to break while fixing the other,
and it is the property `in_universe`'s docstring exists to protect.

Mutation-proven: removing the filter and neutering it are both caught, and a
control mutation of an unrelated entry guard correctly SURVIVES — so the tests
are specific to this filter rather than to any change in the loop.

### Not a claim of value

**This is not an EDGE claim and nothing here is gated.** The EDGE tally is
unchanged at 1-in-15 and frozen; `knowledge/backtest_candidates.md` owns that
count.

### Note, 2026-08-06 — the same gap re-enacted itself INSIDE live, and is now floored

The entry above is about the simulator trading a wider universe than the bot.
The mirror image also existed and nobody had looked for it: **the bot silently
trading a NARROWER universe than the one every gate scored.**

`_fetch_and_validate_bars` caught a per-symbol bar failure, logged `data_error`,
and carried on with whatever was left. There was no floor and no record that the
cross-section had shrunk. It has fired once — the ledger's only `data_error` in
its entire history:

```json
{"event": "data_error",
 "detail": "QQQ: ('Connection aborted.', RemoteDisconnected(...))",
 "ts": "2026-08-05T19:47:25.555162+00:00"}
```

QQQ is one of the core 38. That cycle then ran on 37 with nothing anywhere
saying so.

**This does NOT reopen #17**, and it is deliberately a note rather than #19: it
is the same gap between the scored universe and the traded one, measured from
the other side, and no new sim/live divergence was created by finding it. What
changed is that the gap is now bounded and visible — `risk.min_universe_fraction`
blocks entries below 80% of the requested cross-section (exits always run), and
a `universe_truncated` event records every loss down to a single symbol.
Pinned by `tests/test_universe_floor.py`.

**The floor takes no credit for the total-outage case.** On 2026-07-31 the
laptop lost DNS and every symbol failed inside 40ms; SPY was therefore missing
and the stale-SPY rail aborted the cycle, as it was always going to.
`test_a_total_outage_still_aborts_on_spy_rather_than_the_floor` holds that
boundary, with the freshness rail deliberately left armed so the test cannot
pass for the wrong reason.

---

## #18 — an overrunning cycle fills at the next open, and nothing checks the clock

**Registered 2026-08-06. OPEN by construction.**

Found while instrumenting cycle duration, not while looking for it.

### The mechanism

The cycle fires at **15:45 ET** against a **16:00 close**, so the entire budget
is fifteen minutes. **No code anywhere compares the clock to the close before
submitting an order.** Grep for a cutoff and there is none: not in
`_process_signal`, not in `broker.market_order`, not in preflight.

Exits go out as `TimeInForce.DAY` (`src/broker.py:181`). Alpaca does not reject
a DAY order placed after the bell — it **queues it for the next session's
open**. So a cycle that runs long does not fail loudly. It silently converts a
**same-close fill into a next-open fill**, across an overnight gap.

Every gate assumes the same-close fill. §19 states it directly
(`knowledge/backtest_candidates.md:665-681`): *"The live scheduler runs one
cycle at 15:45 ET and fills immediately, at today's close."* That assumption is
load-bearing for every recorded number, and it is true only while the cycle
finishes in time.

### Why it is open rather than closed

The same reason as #16. A test can prove the *alarm* fires — and one does, in
`tests/test_cycle_timing.py`. No test can detect **"the broker filled this at
the wrong session"**: the fill is legitimate, the order is legitimate, and
nothing in the response distinguishes a 15:59 fill from an 09:30 one except a
timestamp nobody compares. That makes this **structurally weaker than #13-#15**
and exactly as weak as #16.

Closing it would require either a hard cutoff that refuses to submit inside N
minutes of the bell — a behaviour change, and a real one, since refusing an
exit is not obviously safer than filling it late — or fill-session attribution
in `record_fill_quality`. Both are decisions, not cleanups. Neither is in scope
here.

### What was actually shipped

Visibility, not a fix:

- `cycle_timing` on **every** cycle (`src/main.py`, in `run_cycle`'s `finally:`,
  so a crashed cycle records too) carrying `duration_s`, `finished_at_et` and
  `margin_min`.
- One alert per day when `margin_min < ops.min_close_margin_min` (default 5).

### The measurement that prompted it

Seven launchd cycles, the only ones that exist: min **1.63 min**, median
**~2.6**, max **7.72** on 2026-08-05 — which spent a 54 s stall and two
~90-100 s broker socket hangs, and finished at 15:52:44 with **7m16s of
margin**. Nothing had ever recorded any of it.

**No verdict is reopened.** No gate has been shown to be affected, because no
cycle has yet been shown to cross the bell. This entry records a hazard that is
now instrumented rather than a correction to a past number.
