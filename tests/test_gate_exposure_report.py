"""The gate report has to show GROSS and WHICH RAIL BOUND, or the DIAGNOSTIC
cannot answer its own questions.

Two gaps this closes, both of which had the data already sitting in the Result:

  * `deploy` in the per-arm line is NET (`(equity - cash) / equity`). Shorting
    RAISES cash, so a 130/30 arm and a flat 100%-long arm print the SAME
    number. A reader comparing them would conclude the short leg changed
    nothing about the size of the book.
  * `simulate_ensemble` has collected a per-rail block census since §40, and
    `run_gate` never printed any of it. "Does the net-exposure band bind?" had
    no answer in the gate report even though the answer was in the object.
    §48's headline — the drawdown rail blocking 94.58% of signals — was reached
    by reading a census by hand.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import run_gate  # noqa: E402


LONG_ONLY = {
    "total_return_pct": 1.0, "profit_factor": 1.2, "max_drawdown_pct": 3.0,
    "n_trades": 10, "n_symbols_traded": 4, "avg_deployment_pct": 100.0,
    "gross_exposure_avg_pct": 100.0, "gross_exposure_max_pct": 100.0,
    "net_exposure_avg_pct": 100.0, "net_exposure_min_pct": 100.0,
    "n_short_trades": 0, "n_short_symbols": 0,
    "census": {"signals": 50, "executed": 10, "blocked": {"heat": 40}},
}

SHORTING = {**LONG_ONLY, "avg_deployment_pct": 100.0,
            "gross_exposure_avg_pct": 160.0, "gross_exposure_max_pct": 172.5,
            "net_exposure_avg_pct": 100.0, "net_exposure_min_pct": 88.0,
            "n_short_trades": 7, "n_short_symbols": 5}


# ---------------------------------------------------------------------------
# Exposure.
# ---------------------------------------------------------------------------

def test_a_shorting_arm_reports_gross_beside_net():
    line = run_gate.fmt_exposure("xsmom_130_30", SHORTING)
    assert line is not None
    assert "160.00%" in line and "172.50%" in line
    assert "shorts    7" in line and "5 names" in line


def test_the_two_arms_are_indistinguishable_on_the_deploy_COLUMN_alone():
    """The reason the extra line exists, asserted rather than asserted-about:
    both arms print the same `deploy`, so that column cannot tell a 130/30 book
    from a flat long one. If this ever stops being true the extra line may be
    redundant — but it will be a deliberate finding, not a silent one."""
    a = run_gate.fmt("baseline", LONG_ONLY, 1.0)
    b = run_gate.fmt("xsmom_130_30", SHORTING, 1.0)
    assert "deploy 100.00%" in a and "deploy 100.00%" in b


def test_a_long_only_arm_prints_no_exposure_line():
    """Suppressed on purpose: for a long-only arm gross IS net, and a second
    identical column is noise that trains people to skim the row."""
    assert run_gate.fmt_exposure("baseline", LONG_ONLY) is None


def test_an_arm_from_an_older_result_does_not_crash_the_report():
    """A summary predating these fields — a resumed run, an older pickle — must
    print, not raise. The report is not the place to discover a schema change."""
    old = {k: v for k, v in LONG_ONLY.items()
           if not k.startswith(("gross_", "net_", "n_short"))}
    old["n_short_trades"] = 3
    line = run_gate.fmt_exposure("x", old)
    assert line is not None and "0.00%" in line


# ---------------------------------------------------------------------------
# Which rail bound.
# ---------------------------------------------------------------------------

def test_the_rail_census_is_printed_with_its_denominator():
    """The count is meaningless without `signals`: "40 blocked" is a different
    finding at 50 signals than at 50,000."""
    line = run_gate.fmt_rails("baseline", LONG_ONLY)
    assert line is not None
    assert "signals     50" in line
    assert "blocked     40" in line
    assert "heat 40" in line


def test_the_net_exposure_rail_is_visible_when_it_fires():
    """The specific question the 130/30 DIAGNOSTIC has to answer. `net_exposure`
    is just another key in the census, which is the point — the report did not
    need a new measurement, only to stop discarding one."""
    s = {**SHORTING, "census": {"signals": 900, "executed": 100,
                                "blocked": {"net_exposure": 300, "heat": 12}}}
    line = run_gate.fmt_rails("xsmom_130_30", s)
    assert "net_exposure 300" in line


def test_the_busiest_rails_come_first():
    """Ordered by count, so the binding constraint is the first thing read
    rather than whichever key happened to be inserted first."""
    s = {**LONG_ONLY, "census": {"signals": 100, "executed": 1,
                                 "blocked": {"heat": 2, "drawdown": 90,
                                             "cooldown": 7}}}
    line = run_gate.fmt_rails("x", s)
    assert line.index("drawdown 90") < line.index("cooldown 7") < line.index("heat 2")


def test_an_arm_that_blocked_nothing_prints_no_rail_line():
    s = {**LONG_ONLY, "census": {"signals": 10, "executed": 10, "blocked": {}}}
    assert run_gate.fmt_rails("x", s) is None


def test_a_result_with_no_census_at_all_does_not_crash():
    assert run_gate.fmt_rails("x", {**LONG_ONLY, "census": {}}) is None
    assert run_gate.fmt_rails("x", {}) is None
