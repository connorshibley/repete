"""The net exposure band has no preflight validation — and an inverted one is
a two-sided outage, not a mistuned number (Phase 1 review, changes requested).

`risk.py:1150-1169` reads `net_exposure_pct.max` as a ceiling that refuses a
BUY moving net above it, and `.min` as a floor that refuses a SHORT moving net
below it — deliberately directional, so a floor can never block every buy the
way a two-sided band would (see the comment there, and §48). That protection
assumes `min < max`. With `min: 120, max: 80` (inverted), `projected > hi`
refuses every buy toward 80% and `projected < lo` refuses every short toward
120%, which between them cover the entire number line either side of the
band's nonsensical interior — both directions refused, permanently.

The key ships commented out in config.yaml, so nothing today can trip this,
but nothing stops someone from uncommenting a doubled or swapped value later
either. Same shape as `tests/test_heat_inversion_trap.py`, whose structure
this file follows.
"""
import pytest

import preflight
import risk


def _cfg(base, **over):
    c = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    c["risk"] = dict(base["risk"])
    c["risk"].update(over)
    return c


ACCOUNT = {"equity": 100_000.0, "buying_power": 100_000.0,
          "last_equity": 100_000.0}


def test_the_arithmetic_of_the_inversion_is_real():
    """Not hypothetical: starting from a comfortably-mid-band 100% net book,
    an inverted band refuses a small BUY toward 110% (blocked by `hi=80`,
    swapped in from `min`) and refuses a small SHORT toward 90% (blocked by
    `lo=120`, swapped in from `max`) — the same two moves a correctly-ordered
    [80, 120] band would both permit."""
    cfg = {"risk": {"max_position_pct": 100.0, "max_open_positions": 0,
                    "net_exposure_pct": {"min": 120, "max": 80}}}
    positions = {"LONG": {"market_value": 100_000.0}}   # net = 100% of equity

    with pytest.raises(risk.RiskRejection) as buy_err:
        risk.pure_checks("buy", "ZZZ", 100, 100.0, ACCOUNT, positions, cfg)
    assert buy_err.value.rail == "net_exposure"

    with pytest.raises(risk.RiskRejection) as short_err:
        risk.pure_checks("short", "ZZZ", 100, 100.0, ACCOUNT, positions, cfg)
    assert short_err.value.rail == "net_exposure"


def test_preflight_refuses_an_inverted_band(cfg):
    c = _cfg(cfg, net_exposure_pct={"min": 120, "max": 80})
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert len(fails) == 1
    assert "two-sided outage" in fails[0]


def test_preflight_permits_a_correctly_ordered_band(cfg):
    """The paired half, varying only the dimension the rail measures: same
    band shape, min below max instead of above it."""
    c = _cfg(cfg, net_exposure_pct={"min": 80, "max": 120})
    assert not [f for f in preflight.run(c) if "net_exposure_pct" in f]


def test_preflight_refuses_an_equal_band(cfg):
    """min == max is still inverted in effect: the interior is empty, so the
    same two-sided refusal applies. `<` must be strict."""
    c = _cfg(cfg, net_exposure_pct={"min": 100, "max": 100})
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert len(fails) == 1


def test_preflight_permits_the_boundary_one_unit_apart(cfg):
    """The boundary pair for the equal-band refusal above: the narrowest band
    that is still NOT inverted."""
    c = _cfg(cfg, net_exposure_pct={"min": 99, "max": 100})
    assert not [f for f in preflight.run(c) if "net_exposure_pct" in f]


def test_preflight_does_not_require_the_key_at_all(cfg):
    """This is an optional rail — absent is fine, matching how it ships
    (commented out in config.yaml)."""
    c = _cfg(cfg)
    assert "net_exposure_pct" not in c["risk"]
    assert not [f for f in preflight.run(c) if "net_exposure_pct" in f]


def test_preflight_refuses_a_band_missing_min(cfg):
    c = _cfg(cfg, net_exposure_pct={"max": 120})
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert any("min" in f for f in fails)


def test_preflight_refuses_a_band_missing_max(cfg):
    c = _cfg(cfg, net_exposure_pct={"min": 80})
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert any("max" in f for f in fails)


def test_preflight_refuses_a_non_numeric_bound(cfg):
    c = _cfg(cfg, net_exposure_pct={"min": "eighty", "max": 120})
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert any("min" in f for f in fails)


def test_preflight_refuses_a_band_that_is_not_a_block(cfg):
    c = _cfg(cfg, net_exposure_pct=130)
    fails = [f for f in preflight.run(c) if "net_exposure_pct" in f]
    assert len(fails) == 1
    assert "must be a block" in fails[0]


def test_a_gutted_guard_would_fail_this_file(cfg):
    """Meta-assertion. If the preflight clause were deleted, the refusal test
    above would pass its setup and assert nothing. Prove the clause fires."""
    c = _cfg(cfg, net_exposure_pct={"min": 120, "max": 80})
    assert any("net_exposure_pct" in f for f in preflight.run(c)), (
        "preflight raised nothing on an inverted band — the guard is inert "
        "and every refusal test in this file is vacuous")
