---
name: bot-research-recall
description: Use before proposing, registering or arguing for ANY change to the three trading bots, and whenever citing a past result — "have we tried this?", "what did §58 actually find?", "what has been rejected and why?". Six thousand lines of record and 55 frozen hypotheses are searchable in one command; guessing at them, or citing a section from memory, is how a decided question gets re-litigated as a new one.
---

# Recall: read the record before adding to it

This project has 64 sections of research record, 55 frozen pre-registrations
and 59 verdict rows. Until Phase 13 the only way to read any of it was `grep`
over a 6,193-line markdown file, and nothing in the repo had ever read the two
JSONL files at all.

`scripts/recall.py` joins them. Use it before writing a spec, before proposing
a candidate, and before quoting a number at the owner.

## The four commands that matter

```bash
.venv/bin/python scripts/recall.py search <terms…>   # have we tried this?
.venv/bin/python scripts/recall.py section §58       # what does it say + its numbers
.venv/bin/python scripts/recall.py spec s58a         # the prior, failure modes, clauses
.venv/bin/python scripts/recall.py verdicts --failed # everything rejected, and why
```

Also: `metrics <id> [--arm X]` for per-arm figures, `priors` for every frozen
prediction beside its outcome, `divergences --for <id>` for what was open when
a gate ran, `calibrate` for §60's scoring, and `audit` to check the three
layers still agree.

## Search finds words, not ideas

Ranking is BM25 over literal tokens. No stemming, no synonyms — a stemmer is a
small inference and it makes a hit unexplainable. **A query for "volatility
squeeze" will not surface §17's Donchian breakout.** Every search prints that
caveat and every hit prints which terms matched it.

So: search two or three ways before concluding an idea is new. Try the
mechanism, the instrument, and the author's name. `--corpus priors` searches
the 55 frozen hypotheses, which is where a near-miss most often hides.
`--regex` is the escape hatch and `grep` remains ground truth.

## `passed: true` DOES NOT MEAN GOOD NEWS

The trap this tool makes cheaper to fall into, so it is stated first:

- **§48 reads `4/4`** and is the finding that the drawdown rail was masking
  measurement — the section that made the survivorship problem visible.
- **§51 reads `3/3`** and is the section that sized the survivorship inflation
  at **+200.28pp** and broke the project's only EDGE pass with its own results.
- **§44 reads `3/4`** and the claim failed: several sections register a
  conjunction where one failure sinks the whole thing, and nothing
  machine-readable says so.

A ratio is a count of verdict rows. It is not a verdict. Open the section.

## The index's two columns disagree on purpose

`research/INDEX.md` carries `heading says` and `gate result` side by side.
§57, §58 and §59 all read `pre-registered` in the first and `0/4`, `0/8`, `0/8`
in the second. Neither is wrong: the first quotes the heading, the second
counts the rows. **Do not "fix" the disagreement** — `classify()` consulting
verdicts is exactly the helpful change that turns a view of the record into a
second source of truth, and `tests/test_research_index.py` goes red if anyone
tries it.

## What this does not tell you

- **Whether the idea is good.** It reports what was tried and what happened.
- **Whether a verdict is still valid.** A verdict measures a bot that does not
  exist while a divergence is open — run `divergences --for <id>` and see
  `bot-divergence-check`.
- **Anything about s35, s37 or s38's divergence context.** They ran one day
  before `docs/divergences.md` existed. The query refuses rather than
  answering with today's table.

## After you read

If the idea is already decided, the answer is one paragraph pointing at the
section. That is a complete answer — see `bot-candidate-intake`.

If it is genuinely new, `bot-pre-registration` governs what happens next. Note
that **both EDGE venues are closed**: §52 freezes `data/snapshots/` and §57's
own reading rule freezes `data/pit/`, both enforced in
`register_gate.freeze_violation`. K stays 15.

## New sections carry a trailer

From §60 onward, every new section of the record ends with

```html
<!-- recall: section=§60 specs=s60a,s60b -->
```

`recall audit` requires it and refuses if it contradicts the registrations.
**Never back-fill it into §1–§59** — that would edit the append-only record,
which is the one thing it must not permit. `research/anchors.json` pins a
sha256 per section for the same reason: it is the first mechanical enforcement
this repo has had that the record is not reworded after the fact.
