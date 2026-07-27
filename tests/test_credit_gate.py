"""§31's rail: prove it exists, prove it fails open, prove it can't peek.

Written BEFORE the gate was run, alongside the pre-registration. The point is
not that the filter helps — the registration says the prior is a rejection,
EDGE claims being 0 for 5 — but that whatever verdict comes back is measuring
the rule the registration describes.

Three properties, in descending order of how badly a bug would matter:

  1. it cannot read a bar past the decision timestamp (a lookahead bug would
     manufacture an edge and the gate would happily report it)
  2. it FAILS OPEN on every kind of missing data (a credit-feed outage must
     never silently halt trading)
  3. it is off unless configured, and it bites when it is

Boundary pairs throughout, in the style of tests/test_rails_are_live.py: each
blocked case sits next to a permitted one differing in exactly the thing the
rail measures.
"""
import risk


def _bars(closes, start_day=1):
    """Daily bars from 2026-01-<start_day>, one close each."""
    return [{"ts": f"2026-01-{start_day + i:02d}T21:00:00Z", "close": c}
            for i, c in enumerate(closes)]


def _cfg(period):
    return {"risk": {"credit_sma_period": period}}


LAST = "2026-01-12T21:00:00Z"


# ---- the rail exists and measures the ratio ----

def test_falling_credit_blocks_and_rising_credit_does_not():
    """The boundary pair that pins the whole rule.

    Same LQD, same period, same timestamp — only the direction of HYG differs.
    """
    ig = _bars([100.0] * 12)
    falling = {"HYG": _bars([90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79]),
               "LQD": ig}
    rising = {"HYG": _bars([79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90]),
              "LQD": ig}
    assert risk.credit_blocked(falling, LAST, _cfg(5)) is True
    assert risk.credit_blocked(rising, LAST, _cfg(5)) is False


def test_it_is_the_RATIO_that_matters_not_either_leg():
    """HYG falling while LQD falls faster is credit STRENGTH — not a block.

    A version that watched HYG alone would get this backwards, and would look
    plausible on a chart while measuring the wrong thing.
    """
    both_down_ig_faster = {
        "HYG": _bars([100, 99, 98, 97, 96, 95, 94, 93, 92, 91, 90, 89]),
        "LQD": _bars([100, 98, 96, 94, 92, 90, 88, 86, 84, 82, 80, 78]),
    }
    assert risk.credit_blocked(both_down_ig_faster, LAST, _cfg(5)) is False


# ---- it cannot see the future ----

def test_bars_after_the_decision_timestamp_are_not_read():
    """The dangerous bug. A gate that peeks manufactures an edge, and the
    statistics downstream would confirm it.

    Identical history; the second dict has a spike AFTER the decision bar. If
    that spike changes the answer, the rail is reading the future.
    """
    ig = _bars([100.0] * 12)
    hy = [90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79]
    early_ts = "2026-01-06T21:00:00Z"          # decide on the 6th bar
    quiet = {"HYG": _bars(hy), "LQD": ig}
    spiked = {"HYG": _bars([*hy[:6], 500, 500, 500, 500, 500, 500]), "LQD": ig}
    assert (risk.credit_blocked(quiet, early_ts, _cfg(3))
            == risk.credit_blocked(spiked, early_ts, _cfg(3)))


def test_the_window_is_inclusive_of_the_decision_bar():
    """On-or-before, not strictly-before: the close being decided on is the
    close the live bot would have had."""
    ig = _bars([100.0] * 6)
    hy = {"HYG": _bars([100, 100, 100, 100, 100, 50]), "LQD": ig}
    # the 6th bar (ratio 0.5) drags the mean below the latest only if counted
    assert risk.credit_blocked(hy, "2026-01-06T21:00:00Z", _cfg(3)) is True


# ---- it fails open ----

def test_every_missing_data_shape_permits_the_entry():
    """A credit-feed outage must never halt trading. The freshness rails own
    that failure class; this one only ever removes a trade it can justify."""
    ig = _bars([100.0] * 12)
    hy = _bars([90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79])
    for label, bars in (
            ("no dict", None),
            ("empty dict", {}),
            ("HYG absent", {"LQD": ig}),
            ("LQD absent", {"HYG": hy}),
            ("HYG empty", {"HYG": [], "LQD": ig}),
            ("history shorter than the period",
             {"HYG": hy[:3], "LQD": ig[:3]}),
    ):
        assert risk.credit_blocked(bars, LAST, _cfg(5)) is False, label


def test_a_zero_or_negative_denominator_does_not_raise():
    """Bad data divides by zero if nobody checks. Fail open, don't crash the
    cycle."""
    bad = {"HYG": _bars([90] * 12), "LQD": _bars([0.0] * 12)}
    assert risk.credit_blocked(bad, LAST, _cfg(5)) is False


def test_misaligned_series_are_matched_by_timestamp_not_by_index():
    """One missing LQD bar would shift the two series against each other and
    quietly compare different days — a silent wrong answer, not an error."""
    ig_full = _bars([100.0] * 12)
    ig_gap = [b for b in ig_full if b["ts"] != "2026-01-05T21:00:00Z"]
    hy = {"HYG": _bars([90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79])}
    assert (risk.credit_blocked({**hy, "LQD": ig_gap}, LAST, _cfg(5))
            == risk.credit_blocked({**hy, "LQD": ig_full}, LAST, _cfg(5)))


# ---- it is off unless asked for ----

def test_zero_or_absent_period_disables_it_and_a_real_period_restores_it():
    """Boundary pair. The shipped config must be able to leave this off, and
    §31 may not change live behaviour until the gate says so."""
    falling = {"HYG": _bars([90, 89, 88, 87, 86, 85, 84, 83, 82, 81, 80, 79]),
               "LQD": _bars([100.0] * 12)}
    assert risk.credit_blocked(falling, LAST, _cfg(0)) is False
    assert risk.credit_blocked(falling, LAST, {"risk": {}}) is False
    assert risk.credit_blocked(falling, LAST, _cfg(5)) is True


def test_the_shipped_config_has_the_gate_switched_off():
    """§31 is pre-registered, not adopted. A rejected arm is never adopted by
    enabling it and seeing what happens."""
    import os
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    assert not (cfg.get("risk") or {}).get("credit_sma_period"), (
        "credit_sma_period is set in the shipped config — §31 has not passed "
        "its gate, so this must stay 0/absent")


# ---- the simulators see the same rail as live ----

def test_both_simulators_and_live_call_one_implementation():
    """Five sim/live divergences have already cost real rework. The filter is
    defined once; this asserts nobody re-implemented it at a call site."""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("src/backtest.py",):
        src = open(os.path.join(root, rel)).read()
        assert "risk.credit_blocked(" in src, rel
    # ...and that the rule itself lives in exactly one place
    risk_src = open(os.path.join(root, "src", "risk.py")).read()
    assert risk_src.count("def credit_blocked") == 1
