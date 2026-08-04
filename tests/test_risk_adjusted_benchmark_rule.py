"""Risk-adjusted scoring against a benchmark instrument (§51, 2026-08-03).

Why this file exists
--------------------
§50 rejected the incumbent on absolute return against SPY, and recorded one
thing its prior had not anticipated: the bot drew down LESS than SPY in all
four periods, and had a better return-per-unit-of-drawdown in all four —
including 2014-2019, where it lost on return.

That is a different question from the one §50 asked, so it needs a different
rule. `beats_benchmark_symbol` asks "did it make more money". This asks "did it
make more money PER UNIT OF DRAWDOWN". The two genuinely disagree on real data,
and `test_the_two_benchmark_rules_can_disagree` pins that with §50's own
2014-2019 numbers — because if they could not disagree, this rule would be an
expensive alias and §51 would just be §50 again.

Scored risk-MATCHED rather than as a bare ratio:

    strategy_return x (benchmark_maxDD / strategy_maxDD) >= benchmark_return

Algebraically the ratio comparison, but stated as "levered to the index's own
drawdown, does it beat the index?". That phrasing is deliberate: it keeps the
leverage assumption visible, and the assumption is load-bearing and false in
general — a long equity book cannot be levered for free, and levering
multiplies the tail beyond what maxDD measures. That is a failure mode recorded
in the §51 specs, not something these tests can fix.

Every guard FAILS rather than skips, on the §33 RUN 1 precedent: that run
printed VALIDATED out of checks that quietly did not run, so "could not be
measured" must never read as "passed". Each guard is pinned in both directions
— a rule that failed everything would be just as useless as one that passed
everything.
"""
import sys

import pytest

import gatespec as gs

sys.path.insert(0, "scripts")
import run_gate as rg                                        # noqa: E402

RULE = "beats_benchmark_risk_adjusted"


def _spec(symbol="SPY", rule=RULE):
    return {"bonferroni_k": 15,
            "clauses": [{"id": "c", "rule": rule, "symbol": symbol}]}


def _arms(total, strat_dd, bench, bench_dd, symbol="SPY"):
    s = {"total_return_pct": total, "max_drawdown_pct": strat_dd,
         "benchmark_return_pct": bench, "benchmark_max_drawdown_pct": bench_dd,
         "benchmark_symbol": symbol, "n_trades": 100}
    return {"baseline": (s, [1.0, -1.0])}


def _one(spec, arms):
    return rg.evaluate(spec, arms, "baseline", resamples=20)["clauses"][0]


# ------------------------------------------------------------ the comparison

def test_lower_drawdown_can_win_despite_a_LOWER_return():
    """THE WHOLE POINT, using §50's real 2014-2019 numbers: the incumbent
    returned +85.01% at 11.74% maxDD against SPY's +97.39% at 19.35%. It LOST
    on return and still clears a risk-matched bar."""
    out = _one(_spec(), _arms(85.01, 11.74, 97.39, 19.35))
    assert out["pass"] is True
    assert "levered" in out["detail"]


def test_a_higher_return_bought_with_more_drawdown_LOSES():
    """Its paired half. Without this the rule would reward any arm that simply
    took more risk, which is the opposite of risk adjustment."""
    assert _one(_spec(), _arms(120.0, 60.0, 90.0, 20.0))["pass"] is False


def test_equal_drawdown_reduces_to_a_plain_return_comparison():
    """When the drawdowns match, the scale factor is 1 and this must agree with
    `beats_benchmark_symbol`. A rule that disagreed with itself in the trivial
    case would be scoring something other than what it claims."""
    assert _one(_spec(), _arms(80.0, 20.0, 60.0, 20.0))["pass"] is True
    assert _one(_spec(), _arms(50.0, 20.0, 60.0, 20.0))["pass"] is False


def test_a_tie_passes():
    """`>=`, matching `beats_buy_hold` and `beats_benchmark_symbol`. Pinned so
    the boundary cannot drift between rules a reader will assume behave alike."""
    assert _one(_spec(), _arms(50.0, 10.0, 100.0, 20.0))["pass"] is True


def test_the_two_benchmark_rules_can_disagree():
    """If they could not, this rule would be an alias for `beats_benchmark_
    symbol` and §51 would be re-running §50 under a new number. §50's own
    2014-2019 row is the counterexample and is pinned here."""
    arms = _arms(85.01, 11.74, 97.39, 19.35)
    plain = _one(_spec(rule="beats_benchmark_symbol"), arms)
    risk_adj = _one(_spec(), arms)
    assert plain["pass"] is False and risk_adj["pass"] is True


# ------------------------------------------------------------------- guards

def test_an_absent_benchmark_FAILS():
    """§33 RUN 1: a check that quietly did not run must not read as passed."""
    out = _one(_spec(), _arms(999.0, 10.0, None, 20.0))
    assert out["pass"] is False
    assert "unavailable" in out["detail"]


def test_an_absent_benchmark_DRAWDOWN_FAILS():
    """The return alone is not enough for this rule — without the benchmark's
    drawdown there is no risk to match against."""
    out = _one(_spec(), _arms(999.0, 10.0, 50.0, None))
    assert out["pass"] is False
    assert "unavailable" in out["detail"]


def test_a_benchmark_computed_for_a_DIFFERENT_symbol_FAILS():
    out = _one(_spec("SPY"), _arms(999.0, 10.0, 10.0, 20.0, symbol="QQQ"))
    assert out["pass"] is False
    assert "unavailable" in out["detail"]


def test_a_zero_benchmark_drawdown_FAILS():
    """No risk to match. Treating it as a bar of zero would make the clause
    trivially clearable by any profitable arm."""
    out = _one(_spec(), _arms(50.0, 10.0, 10.0, 0.0))
    assert out["pass"] is False
    assert "no risk to match" in out["detail"]


def test_a_zero_STRATEGY_drawdown_FAILS():
    """The scale factor would be a division by zero, and the honest reading of
    a multi-year arm with no drawdown is a bug or a bot that never traded — not
    an infinite edge. THE LOAD-BEARING GUARD: without it this rule would report
    the project's first EDGE pass on an arm that did nothing."""
    out = _one(_spec(), _arms(50.0, 0.0, 10.0, 20.0))
    assert out["pass"] is False
    assert "undefined" in out["detail"]


def test_a_losing_strategy_does_not_win_by_shrinking_its_drawdown():
    """A NEGATIVE return scaled up by a favourable drawdown gap gets MORE
    negative, not less. Pinned because the scale factor is a multiplication and
    sign errors there are silent."""
    out = _one(_spec(), _arms(-20.0, 5.0, 40.0, 20.0))
    assert out["pass"] is False
    assert "-80.00%" in out["detail"]


# ------------------------------------------------------- gatespec validation

def _valid_spec(clause):
    return {"id": "sX", "claim": "EDGE", "title": "t",
            "snapshot": {"path": "p", "sha256": "0" * 64},
            "arms": [{"name": "baseline"}], "clauses": [clause],
            "prior": "Expected to be rejected; this is a validation "
                     "fixture and not a real registration.",
            "failure_modes": ["fixture — not a real failure mode"]}


def test_the_rule_is_accepted_and_needs_no_second_arm():
    """A SELF rule, so §39's single-arm shape stays legal — which is what lets
    §51 put the incumbent on trial against the market alone."""
    gs.validate(_valid_spec({"id": "a", "rule": RULE, "symbol": "SPY"}))


def test_a_missing_symbol_is_rejected():
    """Inherited from SYMBOL_RULES. A defaulted symbol would let the instrument
    being raced change underneath a registered claim without moving the hash."""
    with pytest.raises(gs.SpecError, match="symbol"):
        gs.validate(_valid_spec({"id": "a", "rule": RULE}))


def test_the_symbol_is_inside_the_frozen_hash():
    a = _valid_spec({"id": "a", "rule": RULE, "symbol": "SPY"})
    b = _valid_spec({"id": "a", "rule": RULE, "symbol": "IWM"})
    assert gs.canonical_sha256(a) != gs.canonical_sha256(b)


def test_the_rule_is_distinct_from_its_plain_sibling_in_the_hash():
    """Swapping one rule for the other must move the goalpost hash, or a
    registered §50-style claim could be silently rescored as a §51-style one."""
    a = _valid_spec({"id": "a", "rule": RULE, "symbol": "SPY"})
    b = _valid_spec({"id": "a", "rule": "beats_benchmark_symbol",
                     "symbol": "SPY"})
    assert gs.canonical_sha256(a) != gs.canonical_sha256(b)
