# Glossary

Terms this repo uses in a specific way. Where the usage differs from the
common one, that is said explicitly — a term that quietly means something
narrower here is worse than one nobody recognises.

---

## The research vocabulary

**§N** — a numbered section of `knowledge/backtest_candidates.md`, the research
record. Sections are chronological and append-only: §43 was written after §42
and neither can be quietly reworded later. Citing "§43" means "the reasoning and
the numbers are in that section, go read them" — every claim in the README
carries one.

**Pre-registration** — writing down what a test must show *before* running it:
the arms, the pass mark, an honest prior, and the ways the result could fool
you. `scripts/register_gate.py` hashes the parsed spec and records the digest
first. Without it, a pass mark can be moved after seeing the data, and a result
that could not have been wrong is not evidence.

**Frozen by hash** — `src/gatespec.py` hashes the *parsed* spec, so reordering
keys or reflowing YAML does not count as tampering, but changing a threshold
does. `scripts/run_gate.py` refuses to score an unregistered or altered spec and
**names the field that moved** — `clauses.2.pp: 1.0 -> 3.0` is the finding;
"the spec changed" only starts a search.

**Claim type** — what a section is trying to establish, fixed at registration:

| type | asks |
|---|---|
| **EDGE** | does this make money beyond the benchmark? |
| **CAPACITY** | does it still work at size? |
| **METHOD** | is the way we are measuring sound? |
| **DIAGNOSTIC** | what is actually happening in here? |

Only EDGE claims spend the multiple-comparison budget below.

**Bonferroni K** — the number of independent EDGE attempts the project has
allowed itself. Testing many ideas and reporting the best one is how noise
gets published, so the significance threshold is divided by K. **K = 16 here,
and the tally is 2 passes in 16** — still a rate consistent with chance, which
is why neither pass is treated as a standing result on its own.

**Enablement gate** — `backtest.enablement_gate`, the function a strategy must
clear before it may trade live. It is the same function that rejected every
candidate, which is the only reason a pass from it means anything. The shipped
configuration currently **fails it in all four test periods** (§43).

**Exposure-matched bar** — the benchmark used throughout: buy-and-hold scaled to
the exposure the bot actually carried. A bot that sits in cash 60% of the time
looks brilliant against fully-invested buy-and-hold and awful against cash;
neither comparison is fair, so neither is used.

**Survivorship inflation** — the return a universe gains purely by being chosen
from today's survivors. Measured here at **+200.28pp** on the 38-name universe
(§51). It is the reason a backtest number in this repo is never quoted without
it. `probe_delisted_coverage.py` tests whether a data vendor can avoid it —
yfinance cannot: SBNY returned 475 rows of fabricated post-seizure history.

**Walk-forward** — training on one period and testing on the next, repeatedly.
Used here for a quarterly report only. **No gate uses it**, because §34 measured
the *selection procedure itself* — single-split, fold-majority, and an oracle
with the future in hand all scored about the same as random.

---

## The engineering vocabulary

**Divergence (register)** — a place where the simulator and the live bot were
found to behave differently. Each is numbered in `docs/divergences.md`. It
matters because every gate verdict assumes the simulator models the live bot; if
it does not, the verdict measures a bot that does not exist.

**Closed vs open (a divergence)** — **closed means a named test would fail if it
reopened.** "Fixed in code" is not closed; this repo has been wrong about that
distinction before. **Open by construction** means the gap cannot be closed by
any assertion — e.g. the paper broker charges no borrow cost, so no test can
detect a real one. Five of eighteen are open, all by construction.

**Fail-open vs fail-closed** — which way a guard errs when it cannot verify
something. **Fail-closed** refuses to trade (preflight, the freshness rail, the
risk rails). **Fail-open** proceeds and logs a `degradation` (the entry drift
guard when the quote feed is down). Getting one backwards is the most expensive
mistake available here, which is why the README's cycle diagram colours them
differently.

**Rail** — a deterministic pre-trade check that can refuse a trade, in
`src/risk.py`. The live bot and the backtester call the *same function*, which
is how five early divergences were pre-empted rather than fixed.

**Rail label** — which check refused an entry, recorded on the ledger decision:
`datacheck` (the two price vendors disagree), `universe` (too few symbols have
usable bars), `halt` (an operator halt in exits mode), `entry_drift`, and the
named risk rails. **Exits are never blocked by a data rail** — a stranded
position is worse than a missed entry.

**Degradation** — a ledger event meaning "a guard failed open and the cycle
continued". Counted against `ops.max_degradations_per_day` (3), which is a
budget, not a target. New signals are deliberately kept *out* of this event type
so they cannot silently spend it.

**Mutation proof / negative control** — deliberately breaking a guard to confirm
its test goes red, then restoring the file byte-exact (md5 before / mutated /
after). Every guard here has one, plus a **control** mutation that correctly
*survives* — otherwise "all my tests fail when I break things" might just mean
the tests are indiscriminate. A guard nothing can falsify is not a guard.

**Deploycheck vs preflight** — opposite polarities on purpose. `deploycheck`
asks "is the running code the reviewed code?" and is advisory (fail-open).
`preflight` asks "is this configuration safe?" and **stops the cycle**
(fail-closed).

**Heartbeat / deadman** — the bot writes a timestamp every cycle; a separate
watchdog job alerts if it goes stale. It exists because the worst failure of an
autonomous bot is not a losing trade, it is *silently not running* — which
produces no error anywhere you would look, because nothing ran to produce one.

**The double interlock** — going live requires `mode: live` in `config.yaml`
**and** `LIVE_TRADING_CONFIRMED=YES` in `.env`. Two independent switches, both
shipped off, so no single edit or mistaken merge can start trading real money.

---

## Things that sound like claims and are not

**"2 passes in 16"** — not "we found an edge". Sixteen pre-registered EDGE
attempts have produced two passes, and the two are not the same kind of thing.
§51's ran on the most survivor-selected universe in the project, carrying
**+200.28pp** of inflation — enough to explain the whole result. §72's ran on
the survivorship-certified `data/pit/` universe and is the first pass
survivorship cannot account for; it is also regime-dependent (it *lost* to SPY
in 2014–2019) and enabled nothing — the venue re-froze with that registration.
Two in sixteen is still a rate consistent with chance, and the asymmetry frozen
before every run is unchanged: **a FAIL is decisive, a PASS is not.**

**"n = 10"** — ten closed round-trips. Not a track record. Six of the ten won and
the account still lost money, which is exactly why a win rate is never reported
here on its own.

**"+107.77%"** (divergence #17) — a simulator correction, not a return anyone
could have earned. Survivor-selected universe, no benchmark, and §52's freeze
stands.
