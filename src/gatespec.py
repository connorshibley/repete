"""A pre-registration expressed as data, and frozen by its own hash.

Why this exists (2026-07-28)
----------------------------
Every gate in `scripts/` is a hand-written runner — seven of them, 1,464 lines,
each re-implementing the same five steps around `backtest.simulate_ensemble`,
`backtest.enablement_gate` and `significance.compare`. The compute is cheap
(§32 scored 500 symbols in 13m35s); the *writing* is what caps how many
hypotheses this project can test. That is the bottleneck being removed.

What must NOT be lost
---------------------
Those rejections mean something only because the claim, the arms, the pass mark
and the honest prior were committed to `knowledge/backtest_candidates.md`
**before the runner existed**. §33 RUN 1 is the standing reminder of what
happens otherwise: it printed VALIDATED and was an artifact.

Today that discipline is a habit backed by a git commit. Here it becomes
mechanical: `canonical_sha256()` hashes the parsed spec, `register_gate.py`
records that hash before the run, and `run_gate.py` refuses to score a spec
whose hash no longer matches. **You cannot move a goalpost after seeing the
data** — not by editing a threshold, not by adding a clause, not by swapping a
snapshot.

The hash is taken over the PARSED structure, not the file bytes. Reformatting
the YAML, reordering keys or rewrapping a comment must not read as tampering —
a freeze that cries wolf is one people learn to bypass.

Two fields are mandatory and carry no machine meaning at all: `prior` and
`failure_modes`. A registration that does not state what the author expected,
and how the result could fool them, is not a pre-registration. Validation
rejects a spec without them for the same reason it rejects one without arms.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os

# DIAGNOSTIC is not a claim. It runs the same machinery and produces the same
# numbers, but its verdict decides NOTHING — the shape §34 used for its oracle
# variant, which was computed precisely so the gap between it and the causal
# result could be read, and was never allowed to license anything.
#
# It exists as a first-class type rather than a note in prose because the
# distinction has to survive being skimmed. A future session reading
# verdicts.jsonl must be able to tell "this was measured on data already mined
# by §32" from "this was a test", without trusting anyone to have read the
# title.
CLAIM_TYPES = ("EDGE", "CAPACITY", "METHOD", "DIAGNOSTIC")

# Clause rules the runner can execute. The pass mark is EXECUTED, never
# paraphrased — prose in the registration and a different threshold in the
# runner is exactly the drift this format exists to prevent.

# COMPARATIVE rules measure an arm against the `baseline` ARM, so a spec using
# any of them needs at least two arms.
COMPARATIVE_RULES = (
    "pf_gt_baseline",       # profit factor strictly greater than the baseline arm
    "maxdd_within",         # maxDD <= baseline maxDD + `pp` percentage points
    "significantly_better",  # significance.compare(...).significant   [EDGE]
    "not_worse",            # significance.compare(...).not_worse      [CAPACITY]
)

# SELF-REFERENTIAL rules measure an arm against benchmarks derived from its OWN
# run — the market it traded, the exposure it actually carried. They need no
# second arm, which is what lets §39 put the incumbent on trial with nothing to
# compare it to except the market.
SELF_RULES = (
    "enablement_gate",         # clears backtest.enablement_gate() in full
    "min_trades",              # n_trades >= `n`
    "beats_buy_hold",          # return >= buy_hold_return_pct
    "beats_exposure_matched",  # return >= buy_hold_return_pct * deployment
    "deployment_at_least",     # avg_deployment_pct >= `pct`          [§41]
)

CLAUSE_RULES = COMPARATIVE_RULES + SELF_RULES

REQUIRED = ("id", "claim", "title", "snapshot", "arms", "clauses",
            "prior", "failure_modes")


class SpecError(ValueError):
    """A spec that cannot be trusted to mean one thing. Always fatal."""


def _need(cond, msg):
    if not cond:
        raise SpecError(msg)


def load(path: str) -> dict:
    """Parse and validate one spec file. Raises SpecError, never returns junk."""
    import yaml
    with open(path) as f:
        spec = yaml.safe_load(f)
    _need(isinstance(spec, dict), f"{path}: top level must be a mapping")
    validate(spec)
    return spec


def validate(spec: dict) -> None:
    for key in REQUIRED:
        _need(spec.get(key) not in (None, "", [], {}),
              f"spec is missing required field `{key}`")

    _need(spec["claim"] in CLAIM_TYPES,
          f"claim must be one of {CLAIM_TYPES}, got {spec['claim']!r}")

    snap = spec["snapshot"]
    _need(isinstance(snap, dict) and snap.get("path") and snap.get("sha256"),
          "snapshot needs both `path` and `sha256` — an unpinned snapshot means "
          "the verdict cannot be re-executed, which is the point of pinning it")
    _need(len(str(snap["sha256"])) == 64,
          "snapshot.sha256 must be the full 64-char digest, not a prefix")

    for name, aux in (spec.get("aux") or {}).items():
        _need(isinstance(aux, dict) and aux.get("path") and aux.get("sha256"),
              f"aux.{name} needs both `path` and `sha256`")

    arms = spec["arms"]
    _need(isinstance(arms, list) and arms, "a gate needs at least one arm")
    names = [a.get("name") for a in arms]
    _need(all(names), "every arm needs a `name`")
    _need(len(set(names)) == len(names), f"duplicate arm names: {names}")
    _need(names[0] == "baseline",
          "the first arm must be named `baseline` — every comparative clause is "
          "expressed relative to it")

    # Two arms are required only when something is actually being compared to
    # the baseline arm. The original blanket rule read "a candidate with nothing
    # to beat is a measurement, not a claim", which is true of `pf_gt_baseline`
    # and false of `beats_buy_hold` — there the thing to beat is the market.
    # §39 puts the INCUMBENT on trial against benchmarks derived from its own
    # run, and has no second arm by design.
    used = {c.get("rule") for c in spec["clauses"]}
    comparative = sorted(used & set(COMPARATIVE_RULES))
    if comparative:
        _need(len(arms) >= 2,
              f"clauses {comparative} compare against the `baseline` arm, so "
              f"this spec needs at least two arms — a candidate with nothing "
              f"to beat is a measurement, not a claim")

    for clause in spec["clauses"]:
        _need(isinstance(clause, dict) and clause.get("id") and clause.get("rule"),
              f"each clause needs `id` and `rule`: {clause!r}")
        _need(clause["rule"] in CLAUSE_RULES,
              f"unknown clause rule {clause['rule']!r}; known: {CLAUSE_RULES}")
        if clause["rule"] == "maxdd_within":
            _need(isinstance(clause.get("pp"), (int, float)),
                  "maxdd_within needs a numeric `pp` (percentage points)")
        if clause["rule"] == "min_trades":
            _need(isinstance(clause.get("n"), int),
                  "min_trades needs an integer `n`")
        if clause["rule"] == "deployment_at_least":
            _need(isinstance(clause.get("pct"), (int, float)),
                  "deployment_at_least needs a numeric `pct`")
            _need(0 < clause["pct"] <= 100,
                  "deployment_at_least `pct` must be in (0, 100] — a threshold "
                  "of 0 would pass a bot that never invested, which is the "
                  "exact degenerate case §41 exists to rule out")

    ids = [c["id"] for c in spec["clauses"]]
    _need(len(set(ids)) == len(ids), f"duplicate clause ids: {ids}")

    # `candidate` (one named arm) or `family` (every listed arm must clear).
    # Never both: a spec that names one arm AND a family is ambiguous about
    # what passing means, and ambiguity is how a verdict gets read as stronger
    # than it is.
    _need(not (spec.get("candidate") and spec.get("family")),
          "a spec has `candidate` or `family`, not both")
    fam = spec.get("family")
    if fam is not None:
        names = {a["name"] for a in arms}
        _need(isinstance(fam, list) and len(fam) >= 2,
              "`family` needs at least two arms — a family of one is just a "
              "candidate, and §34 prescribes family coherence precisely "
              "because a lone arm cannot distinguish an effect from selection "
              "noise")
        _need("baseline" not in fam, "baseline is the comparison, not a member")
        missing = [n for n in fam if n not in names]
        _need(not missing, f"family names arms that do not exist: {missing}")

    split = spec.get("split")
    if split is not None:
        _need(isinstance(split, dict), "split must be a mapping")
        frac = split.get("fraction")
        _need(isinstance(frac, (int, float)) and 0 < frac < 1,
              "split.fraction must be strictly between 0 and 1")
        _need(split.get("use") == "oos",
              "split.use must be `oos`. Scoring in-sample is not a gate, and "
              "spelling it out stops a silent default from deciding which half "
              "a verdict came from.")

    k = spec.get("bonferroni_k", 1)
    _need(isinstance(k, int) and k >= 1,
          "bonferroni_k must be a positive integer — family-wise error "
          "accumulates across a research programme even when hypotheses differ")

    # `judge_model` — whether the simulated judge runs (W2-1, 2026-07-29).
    #
    # Optional HERE and mandatory in register_gate.py, which is not an
    # inconsistency: specs frozen before this field existed (§35-§41) must still
    # load and re-execute byte-identically, or the record they produced stops
    # being reproducible. Making it required at REGISTRATION means no NEW claim
    # can be frozen without declaring it, while every old hash stands.
    #
    # Absence therefore means "predates the field", not "false" — and
    # run_gate.py banners that case loudly rather than defaulting quietly.
    # Empty is not the same as absent.
    if "judge_model" in spec:
        _need(isinstance(spec["judge_model"], bool),
              "judge_model must be true or false, not a string or a number — "
              "it decides whether the run models the judge that cuts 58% of "
              "live buys, and a truthy string would silently read as `on`")

    _need(isinstance(spec["failure_modes"], list) and spec["failure_modes"],
          "`failure_modes` must name at least one way this result could fool "
          "you. A registration that cannot fail honestly is not one.")
    _need(isinstance(spec["prior"], str) and len(spec["prior"].strip()) >= 20,
          "`prior` must state what you actually expect, in a sentence — "
          "'it might work' is not a prior")


def canonical(spec: dict) -> str:
    """The exact bytes that get hashed.

    Sorted keys and no insignificant whitespace, so the digest tracks MEANING.
    Two specs that parse to the same structure freeze to the same hash however
    their YAML was laid out; changing any threshold, arm, clause or snapshot
    changes it.
    """
    return json.dumps(spec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def canonical_sha256(spec: dict) -> str:
    return hashlib.sha256(canonical(spec).encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def diff_fields(old: dict, new: dict) -> list[str]:
    """Dotted paths whose value differs. Used to SAY WHAT CHANGED when a spec
    fails its freeze check.

    "This spec was altered" sends someone hunting; "clauses.3.pp: 1.0 -> 3.0"
    tells them the drawdown allowance was widened after the data was seen. The
    second one is the finding.
    """
    out = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k, "<absent>"), b.get(k, "<absent>"),
                     f"{path}.{k}" if path else str(k))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            for i, (x, y) in enumerate(zip(a, b)):
                walk(x, y, f"{path}.{i}")
        elif a != b:
            out.append(f"{path}: {a!r} -> {b!r}")

    walk(old, new, "")
    return out


# ---- applying an arm to a config -------------------------------------------

def _assign(cfg: dict, dotted: str, value) -> None:
    node = cfg
    parts = dotted.split(".")
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def apply_overlay(base_cfg: dict, spec: dict, arm: dict) -> dict:
    """Build the config one arm runs under.

    Order is fixed and matters: the spec-level `shared` block first (so every
    arm is sized identically and a difference between arms can never be a
    sizing difference — §35's SHARED_RISK made that explicit), then the arm's
    own `replace`, then its `set`.

    `replace` swaps a whole top-level subtree (§35 replaces `strategies`
    outright so the baseline's enabled strategies cannot leak into the
    candidate); `set` assigns one dotted path (§31 moves a single risk knob).
    Both are needed and conflating them hides which is happening.
    """
    cfg = copy.deepcopy(base_cfg)
    for dotted, value in ((spec.get("shared") or {}).get("set") or {}).items():
        _assign(cfg, dotted, value)
    for key, value in (arm.get("replace") or {}).items():
        cfg[key] = copy.deepcopy(value)
    for dotted, value in (arm.get("set") or {}).items():
        _assign(cfg, dotted, value)
    return cfg


# ---- the freeze ------------------------------------------------------------

REGISTRATIONS = "research/registrations.jsonl"


def registrations(path: str = REGISTRATIONS) -> dict:
    """id -> the most recent registration record.

    Append-only on disk; last write wins on read, so a legitimate
    re-registration is possible and is visible in the file as an extra row
    rather than an edit. Nothing here silently rewrites history.
    """
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                out[rec["id"]] = rec
    return out
