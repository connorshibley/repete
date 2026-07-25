"""Relative-volume entry filter (§23) — base.rvol + risk.rvol_blocked.

Two independent professional sources treat volume expansion as core entry
confirmation, and until §23 no strategy in this repo read the volume field at
all. These tests pin the two things that make the filter safe:

  1. it FAILS OPEN — a missing or degenerate volume baseline must permit the
     entry, never silently halt all trading;
  2. it is ONE shared implementation, so live and both simulators cannot drift
     apart the way §13/§19a/§19b/§22 did.
"""
import pytest

import risk
from strategies.base import rvol


def bars_with(volumes):
    return [{"ts": f"2025-01-{i+1:02d}T21:00:00+00:00", "open": 100.0,
             "high": 101.0, "low": 99.0, "close": 100.0, "volume": v}
            for i, v in enumerate(volumes)]


# ---------------- the arithmetic ----------------

def test_rvol_is_last_bar_over_prior_mean():
    # 20 bars of 100, then one of 250 -> 2.5x
    b = bars_with([100] * 20 + [250])
    assert rvol(b, 20) == pytest.approx(2.5)


def test_current_bar_is_excluded_from_its_own_baseline():
    """Including today would drag the mean toward today and understate a real
    spike. With 20x100 then 300: excluded -> 3.0; included -> ~2.86."""
    b = bars_with([100] * 20 + [300])
    assert rvol(b, 20) == pytest.approx(3.0)


def test_flat_volume_is_one():
    assert rvol(bars_with([500] * 21), 20) == pytest.approx(1.0)


def test_quiet_bar_is_below_one():
    b = bars_with([100] * 20 + [40])
    assert rvol(b, 20) == pytest.approx(0.4)


# ---------------- the undefined cases: None, never a number ----------------

def test_insufficient_history_returns_none():
    assert rvol(bars_with([100] * 20), 20) is None      # need period+1
    assert rvol([], 20) is None


def test_zero_volume_baseline_returns_none_not_infinity():
    """A dead/absent feed must not produce an infinite rvol that waves every
    entry through, nor a ZeroDivisionError."""
    b = bars_with([0] * 20 + [1000])
    assert rvol(b, 20) is None


def test_missing_volume_field_is_treated_as_zero_not_a_crash():
    b = bars_with([100] * 21)
    for bar in b:
        del bar["volume"]
    assert rvol(b, 20) is None          # baseline 0 -> undefined


def test_none_volume_does_not_crash():
    b = bars_with([100] * 20 + [None])
    assert rvol(b, 20) == pytest.approx(0.0)


# ---------------- the rail: risk.rvol_blocked ----------------

def base_cfg(**risk_kw):
    return {"risk": {**risk_kw}, "strategies": {"meanrev": {}, "tsmom": {}}}


def test_disabled_by_default_blocks_nothing():
    """Shipping default must be off — §23 is a candidate, not an adoption."""
    b = bars_with([100] * 20 + [10])            # very weak volume
    assert risk.rvol_blocked(b, base_cfg()) is False


def test_blocks_when_below_threshold():
    b = bars_with([100] * 20 + [150])           # rvol 1.5
    assert risk.rvol_blocked(b, base_cfg(min_rvol=2.0)) is True


def test_allows_when_at_or_above_threshold():
    b = bars_with([100] * 20 + [200])           # rvol 2.0
    assert risk.rvol_blocked(b, base_cfg(min_rvol=2.0)) is False


def test_fails_open_on_insufficient_history():
    """THE safety property: a short series must not block every entry."""
    b = bars_with([100] * 5 + [10])
    assert risk.rvol_blocked(b, base_cfg(min_rvol=2.0)) is False


def test_fails_open_on_dead_volume_feed():
    b = bars_with([0] * 20 + [0])
    assert risk.rvol_blocked(b, base_cfg(min_rvol=2.0)) is False


def test_per_strategy_threshold_overrides_global():
    b = bars_with([100] * 20 + [150])           # rvol 1.5
    cfg = base_cfg(min_rvol=2.0)
    cfg["strategies"]["meanrev"]["min_rvol"] = 1.0
    assert risk.rvol_blocked(b, cfg, "meanrev") is False   # per-strategy wins
    assert risk.rvol_blocked(b, cfg, "tsmom") is True      # falls back global


def test_per_strategy_zero_disables_for_that_strategy_only():
    b = bars_with([100] * 20 + [10])
    cfg = base_cfg(min_rvol=2.0)
    cfg["strategies"]["meanrev"]["min_rvol"] = 0
    assert risk.rvol_blocked(b, cfg, "meanrev") is False
    assert risk.rvol_blocked(b, cfg, "tsmom") is True


def test_custom_period_is_honoured():
    b = bars_with([100] * 5 + [300])
    cfg = base_cfg(min_rvol=2.0, rvol_period=5)
    assert risk.rvol_blocked(b, cfg) is False    # rvol 3.0 clears 2.0
    cfg2 = base_cfg(min_rvol=4.0, rvol_period=5)
    assert risk.rvol_blocked(b, cfg2) is True


def test_shipped_config_leaves_the_filter_off():
    """§23 must not silently change live behaviour before it passes a gate."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    assert not (cfg.get("risk") or {}).get("min_rvol", 0)
    for name, params in (cfg.get("strategies") or {}).items():
        assert not (params or {}).get("min_rvol", 0), f"{name} has min_rvol set"
