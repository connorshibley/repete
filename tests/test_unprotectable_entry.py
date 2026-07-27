"""Never take a position you cannot put a stop on.

Why this file exists
--------------------
`risk.brackets()` returns None when `entry - mult*ATR <= 0`, and every caller
degrades to a plain market order. The intent was graceful degradation. The
effect is inverted safety: the only names it fires on are the most volatile in
the universe, so the positions that most need a stop are precisely the ones that
get none.

Found by reading §32's run log, where it fired repeatedly on a 500-name
universe. Investigated before being "fixed" — the trigger is real market data,
not corruption:

    shipped 38-symbol universe    0 of  61,104 bars   (0.0000%)
    wide 500-symbol universe     25 of 803,787 bars   (0.0031%)
                                 APA, CAR, CZR, NCLH, PENN
                                 ATR/price 0.36–0.62

March 2020 and a 2026 squeeze in CAR. A daily range half the share price is not
a swing setup.

`test_the_shipped_universe_is_completely_unaffected` is the load-bearing one: it
turns "this is safe to ship without a gate" from an assertion into a measurement
against the real committed snapshot.
"""
import gzip
import json
import os

import pytest

import risk
import strategies

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cfg(enabled=True, mult=2.0, high=0):
    return {"risk": {"brackets": {"enabled": enabled, "stop_atr_mult": mult,
                                  "stop_atr_mult_high_vol": high}}}


# ---- the boundary ----

def test_a_stop_above_zero_is_allowed_and_one_at_or_below_is_not():
    """The pair. Same price, same multiplier — only ATR differs, and it differs
    across the exact point where the stop crosses zero."""
    cfg = _cfg(mult=2.0)
    assert risk.unprotectable_entry(100.0, 49.0, cfg) is False   # stop 2.00
    assert risk.unprotectable_entry(100.0, 50.0, cfg) is True    # stop 0.00
    assert risk.unprotectable_entry(100.0, 51.0, cfg) is True    # stop -2.00


def test_the_real_case_from_the_wide_universe_is_blocked():
    """The literal numbers §32 logged."""
    assert risk.unprotectable_entry(185.96, 101.74, _cfg()) is True
    assert risk.unprotectable_entry(190.64, 115.79, _cfg()) is True


def test_a_normal_megacap_entry_is_never_blocked():
    """The permissive half. Without it, a function that blocks everything would
    pass every test above."""
    for price, atr in ((673.44, 6.2), (196.51, 4.8), (56.88, 0.9),
                       (1197.53, 22.0)):
        assert risk.unprotectable_entry(price, atr, _cfg()) is False


# ---- it uses the widest multiplier, not the current one ----

def test_the_widest_configured_multiplier_decides():
    """Whether a name is protectable must not depend on which vol bucket
    happens to be current — otherwise the same entry is refused on Tuesday and
    accepted on Wednesday for reasons unrelated to the name."""
    cfg = _cfg(mult=2.0, high=4.0)
    # stop at 2x is +20, at 4x is -60. The wider multiplier governs.
    assert risk.unprotectable_entry(100.0, 40.0, cfg) is True
    assert risk.unprotectable_entry(100.0, 40.0, _cfg(mult=2.0, high=0)) is False


# ---- it fails open ----

def test_it_fails_open_when_there_is_no_bracket_to_be_missing():
    """Brackets off, or no ATR: there is no stop to lose, and the bracket path
    was never going to protect this trade. Blocking would remove trades for a
    protection that was not on offer."""
    assert risk.unprotectable_entry(100.0, 500.0, _cfg(enabled=False)) is False
    assert risk.unprotectable_entry(100.0, None, _cfg()) is False
    assert risk.unprotectable_entry(100.0, 0.0, _cfg()) is False
    assert risk.unprotectable_entry(100.0, 50.0, _cfg(mult=0, high=0)) is False
    assert risk.unprotectable_entry(100.0, 50.0, {}) is False


# ---- the claim that lets this ship without a gate ----

def test_the_shipped_universe_is_completely_unaffected(  ):
    """Measured against the real committed snapshot, not asserted.

    If this ever goes red, the change has stopped being a no-op and needs a
    pre-registered gate like any other trading-behaviour change.
    """
    import yaml
    with open(os.path.join(ROOT, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    snap = os.path.join(ROOT, "data", "snapshots",
                        "bars_2020-01-01_2026-07-10.json.gz")
    if not os.path.exists(snap):
        pytest.skip("shipped snapshot not present")
    bars = json.load(gzip.open(snap))
    hits = 0
    for sym, rows in bars.items():
        for i in range(30, len(rows)):
            a = strategies.atr(rows[max(0, i - 30):i], 14)
            if a and risk.unprotectable_entry(rows[i - 1]["close"], a, cfg):
                hits += 1
    assert hits == 0, (
        f"{hits} bars on the SHIPPED universe would now be blocked. This was a "
        f"no-op when written (0 of 61,104); it is no longer, so it needs a gate.")


def test_both_simulators_and_live_call_the_same_helper():
    """Five sim/live divergences have cost real rework. One implementation."""
    for rel in ("src/main.py", "src/backtest.py"):
        src = open(os.path.join(ROOT, rel)).read()
        assert "risk.unprotectable_entry(" in src, rel
    assert open(os.path.join(ROOT, "src", "risk.py")).read().count(
        "def unprotectable_entry") == 1
    # backtest wires BOTH simulate() and simulate_ensemble()
    assert open(os.path.join(ROOT, "src", "backtest.py")).read().count(
        "risk.unprotectable_entry(") == 2


def test_it_is_never_consulted_on_an_exit():
    """A position must always be able to leave. The guard appears only in entry
    paths — an exit blocked by a missing stop would trap the holding forever."""
    src = open(os.path.join(ROOT, "src", "main.py")).read()
    idx = src.index("risk.unprotectable_entry(")
    window = src[max(0, idx - 1500):idx]
    assert "sig.action == \"sell\"" not in window.split("hold_reasons")[-1]
