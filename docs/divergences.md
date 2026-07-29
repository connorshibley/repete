# Backtester vs live: the divergence register

Every gate verdict in `knowledge/backtest_candidates.md` rests on the simulator
being a faithful model of the live bot. Where the two differ, a verdict measures
a bot that does not exist.

Ten such differences have been found. Until 2026-07-28 they existed **only as
prose scattered across forty sections of the gate ledger** — there was no list,
so "how many are open?" had no answer, and #8 could sit closed-on-paper and open
in fact for two days without anyone noticing. This file is the list.

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
| 8 | **The LLM judge is absent from the simulator** | **OPEN** | see below |
| 9 | Drawdown rail existed on the live path only | **pre-empted** | `risk.drawdown_pct` shared (`src/risk.py:158-169`, `backtest.py:348,679`) |
| 10 | **The simulator's equity peak advanced only on buy bars** | **closed 2026-07-28** | `tests/test_sim_peak_tracks_every_bar.py` |

---

## #8 — the judge is modelled and switched off

`src/judge_model.py` was built for §29 (2026-07-26) specifically to close this,
and `config.yaml:546` ships `judge_model.enabled: false`. `judge_model` appears
in **no** file under `research/specs/` and nowhere in `scripts/run_gate.py`.

So §35, §37, §38 and §39 — every gate run *after* the fix was written — measured
a bot with no judge, while the live judge downsizes **56.7%** of buys at a mean
scale of 0.752 (164 judged buys in `memory/ledger.jsonl`). The simulator has
therefore been sizing entries roughly 33% larger than the live bot would on the
majority of trades.

This one is instructive: the code landed, the divergence was declared closed,
and the switch was never flipped. That is why this register records the *test*
that closes each entry rather than the commit that fixed it.

**Closes when:** `judge_model.enabled` is true in the gate path, the calibration
is refreshed from the current ledger, and a test asserts a gate run with the
judge on differs from one with it off.

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
quietly here.

---

## How to add an entry

1. Number it. Numbers are permanent; a superseded entry stays with its status
   changed, never deleted.
2. State which side was wrong and **in which direction** the error ran. A
   divergence that made the bot look worse is a different risk from one that
   flattered it.
3. Name the test that closes it. If there is no test, the status is OPEN
   regardless of what the code does today.
