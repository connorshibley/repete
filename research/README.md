# research/ — pre-registrations as data

A hypothesis is written here as a spec, frozen by its hash, then executed. The
prose registration in `knowledge/backtest_candidates.md` remains the human
record; this directory is the machine-checkable half of the same discipline.

```
research/
  specs/<id>.yaml       the claim: arms, snapshot, pass mark, prior, failure modes
  registrations.jsonl   append-only freeze: {id, spec_sha256, registered_at, spec}
  verdicts.jsonl        append-only results
```

## The workflow

```bash
# 1. write research/specs/s37.yaml
# 2. freeze it — BEFORE looking at any data
python scripts/register_gate.py s37

# 3. check the freeze and the snapshot without scoring anything
python scripts/run_gate.py s37 --dry-run

# 4. run it
python scripts/run_gate.py s37 --workers 4
```

Step 2 is not a formality. Until it happens there is no frozen pass mark, and
`run_gate.py` refuses to score anything — a result that could not have been
wrong is not evidence.

## Why the runner refuses things

| refusal | meaning |
|---|---|
| not registered | no frozen pass mark exists |
| altered since registration | the claim changed after freezing — the message names the field, e.g. `clauses.1.pp: 1.0 -> 3.0` |
| snapshot drift | the data is not what the registration named |

Re-registering is **allowed** while a claim has no verdict — editing a spec
before any data is seen is authoring. It is **refused** once a verdict exists,
because at that point the result is known and editing the pass mark is moving
the goalpost. Every registration appends a row; nothing is rewritten, so the
history of what was promised stays readable.

The hash covers the parsed structure, not the file bytes. Reformat, reorder
keys, rewrap a comment — same hash. Change a threshold, an arm, a clause or a
snapshot — different hash. A freeze that cries wolf is one people learn to
bypass.

## Two fields the machine never reads

`prior` and `failure_modes` are mandatory and validation rejects a spec without
them. They exist so the author states what they expected, and how the result
could fool them, **before** the number lands. That is the only thing that makes
a surprise legible afterwards, and it is why §35's rejection is worth more than
the seven before it.

## What a verdict is not

A verdict is a recommendation. Nothing here enables a strategy or widens a risk
rail — that stays the owner's decision (CLAUDE.md invariant 2). A PASS means one
hypothesis survived one pre-registered test on one snapshot, which is a long way
from a bot that should trade it.

And throughput is not information. §33b and §34 showed that in-sample selection
carries no signal on this data — fold-majority scored 2/4, an oracle holding the
future scored 2/4, a coin scores 2/4. Running more hypotheses through an
uninformative referee produces false positives faster. **This tooling makes the
referee cheaper to run; it does not make it more informative.** That problem is
still open.

## Known gaps

- **Selection is not implemented.** Specs run every arm and score the named
  candidate. Gates that pick an in-sample winner (§32, §33) stay on their
  committed runners — which is no loss, since §33b is the reason to distrust
  that step in the first place.
- **Aux snapshots are passed whole.** §31 needs its credit series sliced with a
  lead-in equal to the longest SMA period so the first OOS bar has history.
  Until that is expressible, §31 stays on `gate_cross_asset.py`.
- **§27 is not reproducible at all**, and not because of this tooling:
  `gate_budget.py` already records `SUPERSEDED_BY = "§29 (2026-07-26)"`. Its
  verdict stands on a bot that no longer exists.
