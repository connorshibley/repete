---
name: bot-pre-registration
description: Use when registering, writing, scoring or reasoning about a gate claim in any of the three trading bots (repete, repete1, repete2) — writing a spec in research/specs/, running register_gate.py or run_gate.py, choosing a claim type, spending the Bonferroni budget, or being asked whether a result "counts". Also use when tempted to compare two backtest variants informally, which is the thing this protocol exists to prevent.
---

# Pre-registration: how a claim becomes evidence here

A backtest number is not evidence. A backtest number whose pass mark was fixed,
hashed and committed before the run is. Everything below is the machinery that
enforces the difference, and it exists because §33 RUN 1 printed `VALIDATED` and
was an artifact.

## The protocol, in order

0. **Read the record first** — `bot-research-recall`. `recall.py search` over
   64 sections and 55 frozen hypotheses, then `recall.py section §N` on
   anything close. Registering a question the record has already answered
   spends K on a result the repo already owns.

1. **Write the spec** — `research/specs/<id>.yaml`. Never a draft you intend to
   edit after seeing a number.
2. **`python scripts/register_gate.py <id>`** — appends
   `{id, spec_sha256, registered_at, spec}` to `research/registrations.jsonl`.
   That row is the goalpost.
3. **`python scripts/run_gate.py <id>`** — refuses to score a spec whose hash
   has moved.

`gatespec.canonical_sha256()` hashes the **parsed** spec, so reformatting the
YAML is free and changing a threshold is not.

Re-registering is allowed while a claim has no verdict — a spec edited before
any data is seen is just authoring. It is **refused once a verdict exists**,
because at that point the result is known and editing the pass mark is the exact
failure this apparatus prevents. Every re-registration appends; nothing is
rewritten.

## Claim types — `gatespec.CLAIM_TYPES`

| type | what it may do | Bonferroni budget |
|---|---|---|
| `EDGE` | claims the bot makes money it otherwise would not | **spends K** |
| `CAPACITY` | about deployment, not returns (§41: can the breaker re-close?) | no |
| `METHOD` | about the measurement apparatus itself | no |
| `DIAGNOSTIC` | **decides nothing**; measures so a later claim is possible | no |

A DIAGNOSTIC that ends up licensing a config change was mistyped. §48 measured
the drawdown rail and enabled nothing, on purpose.

## The Bonferroni budget

`bonferroni_k` in the spec. It is the count of EDGE claims this project has
made, and it only goes up. **K is 16. The EDGE record is 2 passes in 16.**

The two passes are not equivalent. §51's was broken by its own results section
(+200.28pp survivorship — see `bot-survivorship-audit`). §72 (trend_hold,
latch-off, 1.5x financed) was the **first pass on the survivorship-certified
`data/pit/` universe** — measured with the judge off. **§75 (METHOD, the
project's first) re-measured it with the judge on and one period of four
stands.** Neither pass is an edge; the tally is what the register says it is.

**Both bar venues are currently frozen, mechanically.** `register_gate.py`
refuses `data/snapshots/` (§52) and `data/pit/` (§57's reading rule, re-frozen
by §72 — "the licence was for one spend"). So a new EDGE claim cannot be
registered today without `--override-freeze`, which is a speed bump with an
audit trail, not a bypass. DIAGNOSTIC spends no K and is the honest route for
anything exploratory.

§33's argument governs: *continuing to hunt arms is simply buying more chances
for a false positive.* If you are reaching for a new arm because the last four
failed, that is the failure mode, not the remedy.

## `judge_model` is not optional to declare

`register_gate.py` refuses any spec without a top-level `judge_model:` key.
There is no default, because the default is what went wrong: §35–§41 were all
scored judge-less by accident while the live bot cut ~58% of its buys, and a
silent `false` would let that recur while the spec looked complete.

- `judge_model: true` — model the judge, as live does
- `judge_model: false` — deliberately without it, and say why in `prior`

## The §52 EDGE freeze

`register_gate.py` **refuses `claim: EDGE` on any snapshot under
`data/snapshots/`.** All of them are built from current Wikipedia index
membership, so the companies that failed are absent by construction.
DIAGNOSTIC / METHOD / CAPACITY still register normally.

`--override-freeze "<reason>"` works, needs ≥20 characters, and writes the
reason into `registrations.jsonl` next to the claim. It is a speed bump with an
audit trail, not a bypass. **Do not use it to get a result you want.** What
lifts the freeze properly is a data source that passes
`scripts/probe_delisted_coverage.py`.

## Numbering

Sections are numbered in `knowledge/backtest_candidates.md`. **A number belongs
to whoever registered first** — §50 was taken that way, and §42→§43→§44 were
renumbered rather than argued over. Numbers are permanent once a run exists;
superseded plan text gets a note, not an edit.

**Drafts carry no section number.** `research/specs/drafts/` is where an
unregistered idea lives, and `id:` there is prefixed `draft-`.

## Writing the `prior` and `failure_modes`

These are not decoration. They are how a reader a year from now tells a
discovery from a fluke.

- **`prior`** — state the expected outcome *and your confidence*, in the
  direction you actually believe. §51a's prior said "REJECTED, at maybe 60/40,
  on a base rate of EDGE 0 for 14" and then it passed; that admission is what
  made the pass interesting rather than suspicious.
- **`failure_modes`** — name, before the run, the ways a PASS could be wrong.
  §51's pre-registered asymmetry — **"a FAIL is decisive; a PASS is not"** — is
  the single line that kept its pass from being read as a discovery.

**§60 scored all 55 priors and found they carry no information.** Fifty-one
stated a direction and every one of them said *fail*; not one prior in this
project's history predicted its own hypothesis would pass. Hit rate 80.4%
against a base rate of 80.4% — the author's forecasts and a rubber stamp
reading "it fails" are, arithmetically, the same strategy.

That is not an argument for optimism. It means a prior earns its place only
when it says something a constant pessimist would not: **which clause** you
expect to sink it (§41, §44), a number computed **before** the run that the
result must match (§43, §50), or an honest "I do not know" (§51's `GENUINELY
UNCERTAIN`, which preceded the only three-of-three pass in the record). Run
`recall.py calibrate` after registering; if the hit rate and the base rate ever
separate, that is a result worth writing up.

If you cannot write a failure mode that would invalidate a pass, you do not
understand the claim well enough to register it.

## What a PASS licenses

Less than you think. §51 passed ×3 and the write-up records:
`PROVISIONAL PASS is "a recommendation, not an action."` A pass licenses no
config change, no enablement, and no further claim without its own
registration.

## Standing constraints right now

- **Both EDGE venues frozen.** `data/snapshots/` under §52 (survivorship, until
  a vendor with verified delisted history is bought) and `data/pit/` under §57's
  reading rule, re-frozen by §72 — §68's promotion rule licensed exactly one
  registration and §72 spent it. A new EDGE claim therefore needs a fresh
  pre-committed promotion or a certified vendor, not an override.
- **Hands-off was REVERSED on 2026-08-10.** The 2026-08-02 stop-building
  decision no longer applies; development resumed toward the news/24-7 agent
  thesis. **The reversal is narrow, and two constraints survive it:**
  - **Live strategies stay frozen** so the decay sample reaches n=20 closed
    trades clean (13 as of 2026-08-14, ~early Sept). Do not touch strategy
    enablement or live config.
  - **Do not develop in `~/bots/repete`** — that checkout IS the live
    deployment; 9+ launchd jobs read `src/main.py` from that working directory
    every run, so work there is live by definition. Use
    `~/bots/repete-tv-dev`.
- Parking a candidate is still usually righter than registering it. See
  `bot-candidate-intake`.
