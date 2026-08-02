# trading-agent

A swing-trading agent that decides once per day on daily bars, writes down its
reasoning for every decision, and holds itself to pre-registered pass marks it
cannot move after seeing the data.

**It trades paper money only.** Going live needs two independent switches thrown
by hand — `mode: live` in `config.yaml` **and** `LIVE_TRADING_CONFIRMED=YES` in
`.env` (`src/broker.py:44`). Both ship off. No real money is managed, and there
is no product to buy.

---

## What makes this worth reading

Most trading-bot repos are a strategy. This one is mostly a **referee**, because
the strategies kept failing and the interesting problem turned out to be *how
you know*.

**Pre-registration, frozen by hash.** A claim is a YAML file — arms, clauses,
the pass mark, an honest prior, and the ways the result could fool you.
`scripts/register_gate.py` hashes the *parsed* spec (`src/gatespec.py`) and
records the digest **before** the run. `scripts/run_gate.py` then refuses to
score:

1. a spec that was never registered — no frozen pass mark means the result
   could not have been wrong;
2. a spec altered since registration — and the refusal **names the field that
   moved**, because `clauses.2.pp: 1.0 -> 3.0` is the finding while "the spec
   changed" only starts a search;
3. a snapshot whose sha256 is not the one the registration named.

Reformatting is deliberately *not* tampering: the hash tracks meaning, so a
freeze that cried wolf would be one people learned to bypass.

**A divergence register.** `docs/divergences.md` lists every place the simulator
and the live bot were found to differ — 12 so far, each closed by a **named
test**, because "fixed in code" has been wrong here before. Three were found in
one day by reading code for an unrelated task, which is the honest reason the
file does not claim to be complete.

**Negative controls on the guards.** Every guard in this repo has been mutated
to confirm the test protecting it goes red, then restored byte-exact. A guard
nothing can falsify is not a guard.

**1,160 tests**, offline by design — no credentials, ever, in CI.

**The record is append-only.** `memory/ledger.jsonl` is the source of truth;
the dashboard, blog and journal are views rendered from it.

---

## What this has and has not shown

This is the section most repos leave out.

**Every EDGE claim has been rejected.** Every pre-registered claim that some
edge exists has been rejected. Not quietly dropped — written up in
`knowledge/backtest_candidates.md` with the verdict, the frozen prior, and what
the failure did and did not license. That file carries the running count and is
the only place it is kept.

**The shipped configuration fails its own gate.** §43 pointed
`backtest.enablement_gate` — the function that rejected every candidate — at the
strategies actually running, across four periods spanning 26 years, on the first
simulator that models what the live bot does. It failed **all four**:

| period | return | exposure-matched bar | margin |
|---|---|---|---|
| 2000–2006 | +9.45% | +29.02% | −19.57pp |
| 2007–2013 | +2.42% | +10.10% | −7.68pp |
| 2014–2019 | +56.71% | +62.61% | −5.90pp |
| 2022–2026 | −7.71% | +2.13% | −9.84pp |

The bar is buy-and-hold scaled to the exposure the bot actually carried — the
fair comparison for a bot that spends most of its time in cash.

**The one historical pass was an artifact.** 2014–2019 was the only period this
bot ever cleared its own gate. It cleared it on a simulator that did not model
the LLM judge, which downsizes 58% of live buys. With the judge modelled the
same period fails by 5.90pp. That is §42 and §43, and it took the count from
one-in-four to zero-in-four.

**There is no validated way to pick a winner here.** §34 tested the *selection
procedure itself*: single-split, fold-majority, and an oracle with the future in
hand all scored about the same as random. So a strategy that looks best on this
data cannot be trusted to be best, and the programme is allowed to reject but
not yet to accept.

**Live record: 4 closed round-trips** since 2026-07-14 — +16.78%, +10.67%,
+6.40%, −8.59%. **n=4 decides nothing** in either direction, and no average of
four numbers belongs in a README. Going live is gated behind **≥30 closed
trades, ≥3 months, and attorney review**, and at the current rate that is
months away.

> **No performance number appears in this repo without its benchmark and its
> sample size.** That rule is why the table above has a bar column and why the
> live record has an `n`.

---

## Layout

| path | what it is |
|---|---|
| `GUIDE.md` | build-from-zero setup walkthrough |
| `knowledge/backtest_candidates.md` | the research record — every claim, prior, and verdict |
| `knowledge/principles.md` | the rules the project holds itself to |
| `research/specs/`, `registrations.jsonl`, `verdicts.jsonl` | frozen specs, their hashes, and what they returned |
| `docs/divergences.md` | simulator-vs-live differences and the test closing each |
| `docs/` | runbooks, SLOs, incident response, secret rotation, go-live checklist |
| `src/risk.py` | every rail; the live bot and the backtester call the same function |
| `src/backtest.py` | the simulator (`simulate_ensemble` is the one gates use) |
| `scripts/run_gate.py` | executes a frozen registration and records the verdict |

## Running it

Setup is in [GUIDE.md](GUIDE.md). Briefly:

```bash
python3.11 -m venv .venv && .venv/bin/python -m pip install -r requirements.lock
cp .env.example .env                  # add your Alpaca PAPER keys
.venv/bin/python -m pytest tests/ -q  # 1160, offline, no keys needed
.venv/bin/python -m src.deploycheck   # is the running code the reviewed code?
.venv/bin/python src/main.py          # one cycle
```

Preflight is not a separate command — `src/preflight.py` runs inside every
cycle (`src/main.py:557`) and **fails the cycle closed** if the config is
unsafe, which is the opposite polarity from `deploycheck` (fail-open, advisory).
A clean start prints nothing from it; a bad one prints `PREFLIGHT: <reason>` and
stops before any order.

`requirements.lock` pins all 58 packages including transitive ones. That matters
more than usual here: `numpy` and `pandas` arrive through the closure, nothing
names them, and a float changing in the last place is enough to move a profit
factor across an enablement threshold. **Bumping a pin is a change that must be
followed by re-running a frozen gate and confirming the recorded numbers still
reproduce.**

## Licence and disclaimer

Apache-2.0 — see [LICENSE](LICENSE).

**Nothing here is financial advice.** This is a research project that trades
simulated money and has not demonstrated an edge. Do not point it at an account
you care about.
