# Drafted specs — written, deliberately NOT registered

`scripts/register_gate.py` resolves `research/specs/<id>.yaml`. Nothing in this
subdirectory is reachable by that path, which is the point: a spec here cannot
be registered by typing its name, only by a deliberate move into the parent
directory.

**Writing a spec costs nothing. Registering one spends Bonferroni budget.**
Those are different acts and this directory keeps them different. A draft can
be argued over, revised, and thrown away with no cost to the multiple-
comparisons count; the moment it moves up a level, `canonical_sha256()` freezes
it and K rises.

A draft is not a queue. Nothing here is promised a run.

**Drafts carry no section number.** A file here is named for what it tests, not
`sNN`. `register_gate.py` says why: *"Reserving a number for a claim nobody has
registered was the mistake; numbers are assigned at registration, and a
reservation is not a registration."* This draft was originally written as
`s50a`, and §50 was then taken by the incumbent re-test, which registered and
ran first — the same rule that renumbered §42→§43→§44.

| draft | what it would test | why it is not registered |
|---|---|---|
| `vol-targeting.yaml` | volatility targeting | §48's reading rule licensed one thing, and this is not it |

**Only one period file exists, not four.** The plan called for four, the
convention §41/§44/§48 use — but that convention exists so a *conjunction* can
be frozen together before any of it runs, and nothing here is being frozen.
Three further copies differing only in a snapshot hash would add no argument
and would look more like a queued claim than a draft. If it is ever registered,
the remaining three get written at that point, together, before any run.

It is also **unrunnable as written**: its candidate arm sets
`risk.vol_target.*`, and there is no such code in `src/risk.py`. That is
deliberate. A draft that looked runnable would invite someone to run it.
