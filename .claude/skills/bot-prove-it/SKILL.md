---
name: bot-prove-it
description: Use whenever a change to any of the three trading bots is claimed to be tested, verified or working — after adding a test, before opening a PR, when the owner says "prove to me it works", and when a test suite passes and you are about to report success. Green tests are not proof; a test that would still pass with the code broken proves nothing.
---

# Prove it: the mutation protocol

The owner's standing demand. A passing test suite is evidence that nothing
crashed. It is not evidence that the test you just wrote is load-bearing. The
only thing that shows a test protects a behaviour is **breaking that behaviour
and watching that specific test go red.**

## The protocol

For each behaviour you claim is protected:

1. **Break it** — one minimal edit to the source, the smallest change that
   inverts or removes the behaviour. Not a syntax error; a plausible wrong
   implementation.
2. **Run the suite.** The **named** test must fail. Note *which* tests failed.
3. **Restore.**
4. **Verify the restore is exact** — `md5 -q <file>` before and after must
   match. Not "looks the same"; byte-identical.
5. **Re-run.** Green.

A mutation that survives means either the test does not cover it or the
behaviour does not matter. Both are findings. Report them.

## Use the Python driver, not a shell loop

`.claude/skills/bot-prove-it/scripts/mutate.py`. **The bash version misreported twice
in one session** — it scraped a line position out of pytest's tail and reported
CAUGHT mutations as SURVIVED, which is the worst possible direction for a tool
whose job is to tell you your tests are weak. Every result it produced had to be
re-verified by hand.

If you write your own harness, it must count outcomes explicitly and it must be
tested against a mutation you *know* is caught before you trust a SURVIVED.

## Two mutations that genuinely survived, and what they taught

Both were real gaps, both were found only because the protocol was run:

**`health.py` — the ET date check.** Reverting the *record* side of the date
comparison from ET to UTC survived, because every test stamped `cycle_complete`
at 15:47 ET, where UTC and ET agree on the date. The test suite could not tell
the two implementations apart. Fixed by adding
`test_a_cycle_that_COMPLETES_in_the_ET_evening_counts`. The underlying bug was
live: health reported DEGRADED every weekday 20:00 ET–midnight.

**`probe_delisted_coverage.py` — coverage scored on the wrong list.** Scoring
`len(rows) > 0` instead of `len(pre) > 0` left the overall verdict correct —
check 2 still fired — while the report printed "coverage: PASS" for a ticker
whose every bar postdates its own delisting. **The verdict was right for the
wrong reason**, which is precisely the kind of thing that survives a refactor.

The lesson in both: a test that passes because of an *incidental* property of
its fixture (a timestamp where two timezones agree, a second check that happens
to fire) is not protecting what you think it is.

## Pairing

Pin behaviours in **both directions**. `tests/test_edge_freeze.py` is the model:
the freeze binds on EDGE *and* does not bind on DIAGNOSTIC/METHOD/CAPACITY; the
override works *and* rejects a token reason; the probe refuses on missing
history *and* passes when pre-delisting history exists. A one-directional test
lets a rule that blocks everything look identical to a rule that works.

## Safety rules while drilling

- **`REPETE_ALERTS_OFF=1`** on every drill, or the drill pages the owner.
- **Never run `halt.py` drills against the live repo.** Clone to the scratchpad
  — a clone excludes `.env`, which is the point.
- Use `sys.executable` in subprocess tests, never a hardcoded `.venv/bin/python`.
  That is already the repo convention (`tests/test_alert_delivery.py`) and
  ignoring it made tests pass locally and fail in CI.

## Reporting

State what you broke, which test caught it, and that the restore was verified
byte-identical. If a mutation survived, say so before saying anything passed —
a report that leads with the green run and buries the survivor is the failure
this skill exists to prevent.
