"""§54's four arms must actually differ, and differ in exactly one thing each.

WHY THIS FILE EXISTS. `gatespec._assign` builds missing intermediate keys with
`setdefault(p, {})`, so a dotted path with a typo — `strategies.xmsom.enabled`,
or the singular `strategy.xsmom.enabled` — silently creates a brand-new inert
subtree. The arm then runs IDENTICALLY to baseline, nothing warns, and the
frozen hash records the typo faithfully because it cannot tell that it was one.
`gatespec.validate()` never inspects `set:` keys at all.

§53 has a latent instance of exactly this shape: on the 2000-2006 snapshot its
`xsmom_long_only` and `xsmom_130_30` arms produced byte-identical results. There
it was explained by the drawdown rail blocking ~99.9% of signals, not by a bad
path — but nothing in the repo could have told the two apart, and §54 has four
arms rather than three.

So: assert against the REAL spec files, through the REAL overlay function, and
require both halves —

  1. each arm sets what it claims to set, and
  2. each arm changes NOTHING ELSE.

Half 2 is the one that catches the typo, because a typo satisfies half 1
vacuously by leaving the intended flag at its shipped value.

This runs in milliseconds and is the gate before any wall time is spent.
"""
import yaml

import backtest as bt
import gatespec as gs


SPEC_IDS = ("s54a", "s54b", "s54c", "s54d")

#: arm name -> the dotted paths it is ALLOWED to move, and to what.
EXPECTED = {
    "baseline": {},
    "reclaim_only": {"strategies.reclaim.enabled": True},
    "xsmom_130_30": {"strategies.xsmom.enabled": True},
    "both": {"strategies.reclaim.enabled": True,
             "strategies.xsmom.enabled": True},
}


def _spec(spec_id):
    with open(f"research/specs/{spec_id}.yaml") as fh:
        return yaml.safe_load(fh)


def _flat(node, prefix=""):
    """Every leaf as a dotted path, so two configs can be compared as sets."""
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            out.update(_flat(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = repr(node)
    return out


def test_the_shipped_config_has_both_strategies_off():
    """`baseline` only means "the shipped configuration" if the shipped
    configuration is what this spec family assumes. If either flag ever ships
    `true`, every arm below is measuring something else and the whole family
    needs re-registering."""
    cfg = bt.load_config()
    assert cfg["strategies"]["reclaim"]["enabled"] is False
    assert cfg["strategies"]["xsmom"]["enabled"] is False
    assert cfg["mode"] == "paper"


def test_the_short_leg_is_actually_configured_on_the_arms_that_claim_it():
    """`xsmom_130_30` and `both` rely on `short_bottom_fraction` SHIPPING
    non-zero rather than setting it. That is deliberate — it keeps the overlay
    minimal — but it means a config change to 0 would turn both arms long-only
    while their names still said 130/30. Pin the assumption the names rest on."""
    cfg = bt.load_config()
    assert cfg["strategies"]["xsmom"]["short_bottom_fraction"] > 0


def test_every_arm_sets_exactly_what_it_claims_and_nothing_else():
    """The load-bearing test: real specs, real `apply_overlay`, both halves."""
    for spec_id in SPEC_IDS:
        spec = _spec(spec_id)
        base_cfg = bt.load_config()
        shipped = _flat(base_cfg)
        arms = {a["name"]: a for a in spec["arms"]}
        assert set(arms) == set(EXPECTED), (
            f"{spec_id}: arms are {sorted(arms)}, expected {sorted(EXPECTED)}")

        for name, expected in EXPECTED.items():
            overlaid = _flat(gs.apply_overlay(base_cfg, spec, arms[name]))
            moved = {k for k in set(shipped) | set(overlaid)
                     if shipped.get(k) != overlaid.get(k)}

            # Half 1: it set what it said.
            for path, value in expected.items():
                assert overlaid.get(path) == repr(value), (
                    f"{spec_id}/{name}: {path} is {overlaid.get(path)}, "
                    f"expected {value!r} — the dotted path is probably a typo, "
                    f"which `_assign` would have created as an inert subtree")

            # Half 2: it set NOTHING else. This is what a typo fails.
            assert moved == set(expected), (
                f"{spec_id}/{name}: moved {sorted(moved)}, "
                f"expected exactly {sorted(expected)}")


def test_no_arm_is_a_duplicate_of_another():
    """Four arms that produce three distinct configs is the failure mode this
    family is most exposed to, and it is invisible in the output — two identical
    arms simply print two identical rows, which reads as a reproducible result
    rather than as a bug."""
    for spec_id in SPEC_IDS:
        spec = _spec(spec_id)
        base_cfg = bt.load_config()
        rendered = {}
        for arm in spec["arms"]:
            key = gs.canonical(gs.apply_overlay(base_cfg, spec, arm))
            assert key not in rendered, (
                f"{spec_id}: arms {rendered[key]!r} and {arm['name']!r} render "
                f"to the SAME config")
            rendered[key] = arm["name"]


def test_the_candidate_is_named_and_is_the_both_arm():
    """`default_candidate` picks arms[1], which here is `reclaim_only`. Scoring
    that arm would answer a different question, and the mistake would only
    surface after the run had cost its wall time."""
    for spec_id in SPEC_IDS:
        spec = _spec(spec_id)
        assert spec.get("candidate") == "both", spec_id
        assert "family" not in spec, f"{spec_id}: candidate and family are XOR"


def test_the_four_specs_differ_only_in_id_snapshot_and_title():
    """The §53 convention: one diagnostic in four files, written and frozen
    together. If arms, clauses, prior or failure_modes drift between periods,
    the four are no longer one experiment and cannot be read under a single
    rule."""
    base = _spec("s54a")
    for spec_id in SPEC_IDS[1:]:
        spec = _spec(spec_id)
        differs = {k for k in set(base) | set(spec)
                   if base.get(k) != spec.get(k)}
        assert differs == {"id", "snapshot", "title"}, (
            f"{spec_id} differs from s54a in {sorted(differs)}")


def test_every_spec_validates_and_declares_what_registration_requires():
    """Fail here rather than at `register_gate.py`: `judge_model` is refused if
    undeclared, `claim: EDGE` is refused under `data/snapshots/` by §52's
    freeze, and `min_trades` needs an `int`."""
    for spec_id in SPEC_IDS:
        spec = _spec(spec_id)
        gs.validate(spec)
        assert spec["id"] == spec_id
        assert spec["claim"] == "DIAGNOSTIC"
        assert isinstance(spec["judge_model"], bool)
        assert spec["bonferroni_k"] == 15
        n = [c for c in spec["clauses"] if c["rule"] == "min_trades"][0]["n"]
        assert isinstance(n, int)
