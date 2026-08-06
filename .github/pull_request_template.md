<!--
This template exists so the repo's own standards are visible to a reader who
never opens `.claude/skills/`. Delete any section that genuinely does not apply
— but delete it deliberately, and say why in the PR body rather than silently.
-->

## What changed, and why

<!-- One paragraph. Name the behaviour, not the diff. -->

## Proof

<!--
`.claude/skills/bot-prove-it`: green tests are not proof. A test that would
still pass with the code broken proves nothing.
-->

- [ ] Every new guard **mutation-proven** — md5 before / while-mutated / after
- [ ] At least one **control mutation** that correctly SURVIVES, showing the
      tests are specific rather than merely numerous
- [ ] Any mutation that survived unexpectedly is **reported here**, not quietly
      fixed — a surviving mutation is a finding about the tests

Mutations run, and their outcomes:

<!-- e.g. "M1 delete the abs() at main.py:1322 -> caught by test_x. C1 rename a
     local -> survived, as intended." -->

## Divergence check

<!--
`.claude/skills/bot-divergence-check`. A verdict measures a bot that does not
exist if live and the simulator disagree.
-->

- [ ] Could this make `src/main.py` and `src/backtest.py` behave differently?
- [ ] If yes: registered in `docs/divergences.md` with a **named test that
      would fail if it reopened** — "fixed in code" is not closed
- [ ] Does it invalidate any recorded gate number? If so, say which, and do
      **not** silently re-run them

## Research claims

- [ ] No EDGE claim registered on a `data/snapshots/` path (§52 freeze)
- [ ] Any new claim pre-registered **before** the data was seen, per
      `.claude/skills/bot-pre-registration`
- [ ] Any performance number quoted here carries **its benchmark and its sample
      size** (README standing rule)

## Invariants

- [ ] **No strategy enabled and no risk rail widened** — CLAUDE.md invariant 2
      makes that the owner's decision alone
- [ ] `mode:` unchanged
- [ ] `ruff check src/ scripts/ tests/` clean
- [ ] Full suite green; any test count in docs **regenerated**, never retyped

## Live-deployment safety

<!-- This checkout IS the deployment; launchd reads src/main.py from the tree. -->

- [ ] Work done in `.worktrees/`, not the live checkout
- [ ] Merging outside the 15:45 ET cycle window
- [ ] After merge: on `main`, pulled, `deploycheck` reports **NO DRIFT**
