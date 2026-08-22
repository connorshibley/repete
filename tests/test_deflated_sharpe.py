"""Deflated Sharpe — frozen against the paper, not against this code.

test_significance_golden.py already records why: a golden generated from the
code under test is a check that cannot fail. So the anchor here is Bailey &
López de Prado's own published figure, and the Result-side tests pin the
repo's standing rule that a missing number is None and never 0.0.
"""
import math

import pytest

import significance as sig
from backtest import Result


# ---- the paper's anchor ----------------------------------------------------

def test_expected_max_sharpe_reproduces_the_published_figure():
    """Bailey & López de Prado (2014), "The Deflated Sharpe Ratio": with N=1000
    independent trials and unit variance of Sharpes, E[max SR] ≈ 3.26. This is
    the single number the method is usually introduced with."""
    assert sig.expected_max_sharpe(1000, 1.0) == pytest.approx(3.26, abs=0.01)


def test_the_luck_bar_rises_with_trials_and_with_dispersion():
    """Monotone in both arguments — the two things the audit said the project
    could not account for: how many things were tried, and how spread out
    their results were."""
    assert sig.expected_max_sharpe(10, 1.0) < sig.expected_max_sharpe(100, 1.0) \
        < sig.expected_max_sharpe(1000, 1.0)
    assert sig.expected_max_sharpe(100, 0.5) < sig.expected_max_sharpe(100, 2.0)


def test_one_trial_is_not_deflated():
    """A single honest test has no luckiest-of-N to beat."""
    assert sig.expected_max_sharpe(1, 1.0) == 0.0
    assert sig.expected_max_sharpe(0, 1.0) == 0.0


def test_psr_is_a_probability_and_moves_the_right_way():
    p_lo = sig.probabilistic_sharpe(0.05, 0.0, 250, 0.0, 3.0)
    p_hi = sig.probabilistic_sharpe(0.20, 0.0, 250, 0.0, 3.0)
    assert 0.0 <= p_lo <= p_hi <= 1.0
    # more observations at the same SR -> more confidence
    assert sig.probabilistic_sharpe(0.1, 0.0, 50, 0.0, 3.0) \
        < sig.probabilistic_sharpe(0.1, 0.0, 500, 0.0, 3.0)


def test_fat_tails_and_negative_skew_reduce_confidence():
    """The reason DSR takes moments at all: a Sharpe earned with a fat left
    tail is worth less than the same Sharpe from normal returns."""
    normal = sig.probabilistic_sharpe(0.1, 0.0, 250, 0.0, 3.0)
    ugly = sig.probabilistic_sharpe(0.1, 0.0, 250, -1.0, 8.0)
    assert ugly < normal


def test_kurtosis_is_raw_not_excess():
    """Pinned to a VALUE, not an inequality.

    The first version of this test asserted only raw != excess. A mutation
    shifting the kurtosis term by +3 (the off-by-three between raw and
    excess conventions) SURVIVED it, because the two calls still differed —
    just both wrongly. An inequality pins nothing.

    For normal returns (skew 0, raw kurtosis 3) the PSR denominator is
    1 + sr²/2 exactly, so PSR has a closed form: Φ(sr·√(T−1) / √(1+sr²/2)).
    sr=0.3, T=100: Φ(0.3·√99/√1.045) = Φ(2.9198) = 0.99825.
    """
    from statistics import NormalDist
    sr, T = 0.3, 100
    expected = NormalDist().cdf(sr * (T - 1) ** 0.5 / (1 + sr * sr / 2) ** 0.5)
    assert sig.probabilistic_sharpe(sr, 0.0, T, 0.0, 3.0) == pytest.approx(expected, abs=1e-9)
    assert expected == pytest.approx(0.99825, abs=1e-4)


def test_the_reading_carries_every_input():
    d = sig.deflated_sharpe(0.1, 0.004, 225, 250, -0.3, 4.5)
    for field in ("sharpe", "n_trials", "n_obs", "skew", "kurtosis",
                  "var_sharpe", "sr_max_expected", "psr"):
        assert getattr(d, field) is not None
    assert "N=225" in d.line() and "T=250" in d.line()
    assert d.clears == (d.psr >= 1.0 - sig.ALPHA)


# ---- Result side: derived, None-not-zero -----------------------------------

def _result(curve):
    """A Result with every required field zeroed; only the curve matters here."""
    return Result(params={}, start="2024-01-01", end="2024-12-31",
                  n_trades=0, n_guard_skipped_exits=0, total_return_pct=0.0,
                  buy_hold_return_pct=0.0, buy_hold_max_drawdown_pct=0.0,
                  avg_deployment_pct=0.0, win_rate=0.0, profit_factor=0.0,
                  max_drawdown_pct=0.0, equity_curve=curve)


def test_an_empty_curve_yields_none_not_zero():
    """The repo's standing rule (benchmark_return_pct, fill quality, cost):
    absent must never collapse to zero, because 0.0 is a claim."""
    r = _result([])
    assert r.sharpe is None
    assert r.n_obs is None
    assert r.return_skew is None
    assert r.return_kurtosis is None
    s = r.summary()
    assert s["sharpe"] is None and s["n_obs"] is None


def test_a_flat_curve_yields_none_not_zero():
    """Zero variance is not a Sharpe of zero; it is an undefined one."""
    r = _result([100.0] * 50)
    assert r.sharpe is None


def test_moments_are_derived_from_the_curve_and_reach_summary():
    curve = [100.0]
    for i in range(300):
        curve.append(curve[-1] * (1.0 + (0.002 if i % 3 else -0.001)))
    r = _result(curve)
    assert r.n_obs == 300
    assert r.sharpe is not None and r.sharpe > 0
    assert r.return_kurtosis is not None and r.return_kurtosis > 0
    s = r.summary()
    assert "equity_curve" not in s            # still popped
    assert s["sharpe"] == r.sharpe            # but its moments survive
    assert s["n_obs"] == 300


def test_summary_keys_are_a_superset_of_the_old_ones():
    """Adding fields must not drop any — every reader of verdicts.jsonl uses
    .get(), but a key that vanished would still be a silent schema change."""
    s = _result([100.0, 101.0, 102.0]).summary()
    for k in ("n_trades", "n_symbols_traded", "financing_paid_usd",
              "benchmark_return_pct", "sharpe", "n_obs",
              "return_skew", "return_kurtosis"):
        assert k in s


def test_sharpe_is_unannualised():
    """Per-bar on purpose: a 252 baked in here would be wrong for every
    intraday run and invisible in the log.

    Built from a KNOWN mean and stdev so the expected per-bar Sharpe is
    arithmetic, not a guess: alternating +2% / -1% bars have mean 0.5% and
    stdev 1.5%, so per-bar SR is 1/3. An annualised number would be ~5.3.
    (An earlier draft used a near-constant series with one perturbed bar,
    which has almost no variance and a legitimately huge Sharpe — the code
    was right and the fixture was wrong.)
    """
    curve = [100.0]
    for i in range(400):
        curve.append(curve[-1] * (1.02 if i % 2 == 0 else 0.99))
    r = _result(curve)
    assert r.sharpe is not None and not math.isnan(r.sharpe)
    assert r.sharpe == pytest.approx(1 / 3, abs=0.01)
    assert r.sharpe < 1.0, "per-bar, never annualised"
