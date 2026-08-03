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

| draft | what it would test | why it is not registered |
|---|---|---|
| `s50a.yaml` | volatility targeting | §48's reading rule licensed one thing, and this is not it |

**Only `s50a` exists, not `s50a-d`.** The plan called for four period files, the
convention §41/§44/§48 use — but that convention exists so a *conjunction* can
be frozen together before any of it runs, and nothing here is being frozen.
Three further copies differing only in a snapshot hash would add no argument
and would look more like a queued claim than a draft. If it is ever registered,
the remaining three get written at that point, together, before any run.

`s50a` is also **unrunnable as written**: its candidate arm sets
`risk.vol_target.*`, and there is no such code in `src/risk.py`. That is
deliberate. A draft that looked runnable would invite someone to run it.
