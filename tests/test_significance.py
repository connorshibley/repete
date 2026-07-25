"""The multiple-testing correction (src/significance.py).

These pin the properties the gate leans on: an honest bootstrap must call pure
noise inconclusive, must widen as K grows, and must be reproducible run to run —
otherwise "the CI excluded zero" is not a rule, it is a coin flip.
"""
import random

import pytest

import significance as sig


def _noise(n, seed, scale=100.0):
    rng = random.Random(seed)
    return [rng.gauss(0.0, scale) for _ in range(n)]


def test_identical_arms_are_inconclusive():
    """Same distribution both sides -> the CI must straddle zero."""
    a, b = _noise(200, 1), _noise(200, 2)
    c = sig.compare(a, b, n_comparisons=1)
    assert c.verdict == "INCONCLUSIVE"
    assert c.ci_low < 0 < c.ci_high
    assert not c.significant


def test_large_real_difference_is_detected():
    """A candidate that earns +$200/trade on top of noise must be caught, or
    the test has no power and every verdict below is meaningless."""
    base = _noise(200, 3, scale=50.0)
    cand = [x + 200.0 for x in _noise(200, 4, scale=50.0)]
    c = sig.compare(base, cand, n_comparisons=1)
    assert c.verdict == "SIGNIFICANT"
    assert c.ci_low > 0


def test_worse_candidate_is_named_as_worse():
    base = [x + 200.0 for x in _noise(200, 5, scale=50.0)]
    cand = _noise(200, 6, scale=50.0)
    c = sig.compare(base, cand, n_comparisons=1)
    assert c.verdict == "SIGNIFICANTLY WORSE"
    assert c.ci_high < 0


def test_bonferroni_widens_the_interval():
    """More arms tried -> a wider interval -> a higher bar. This is the whole
    mechanism METHOD NOTE 2 asked for; if K stopped mattering the correction
    would be decorative."""
    base = _noise(150, 7)
    cand = [x + 40.0 for x in _noise(150, 8)]
    narrow = sig.compare(base, cand, n_comparisons=1)
    wide = sig.compare(base, cand, n_comparisons=10)
    assert wide.ci_high - wide.ci_low > narrow.ci_high - narrow.ci_low
    assert wide.alpha < narrow.alpha


def test_deterministic_across_runs():
    """A gate verdict that changed between runs would be unfalsifiable."""
    base, cand = _noise(100, 9), _noise(100, 10)
    first = sig.compare(base, cand, n_comparisons=3)
    second = sig.compare(base, cand, n_comparisons=3)
    assert (first.ci_low, first.ci_high) == (second.ci_low, second.ci_high)


def test_block_length_is_sqrt_n():
    assert sig._block_length(100) == 10
    assert sig._block_length(1) == 1
    assert sig._block_length(0) == 1


def test_blocks_preserve_clustering():
    """With blocks, a series that is all-losses-then-all-wins keeps its runs,
    so the resampled means vary MORE than independent resampling would. That
    extra variance is the point: clustered outcomes are less informative than
    the same number of independent ones."""
    series = [-100.0] * 50 + [100.0] * 50
    rng = random.Random(11)
    block_means = [sig._resample_mean(series, rng) for _ in range(400)]
    shuffled = sorted(series)  # same values, block structure intact but sorted
    rng2 = random.Random(11)
    iid = []
    for _ in range(400):
        iid.append(sum(rng2.choice(shuffled) for _ in range(100)) / 100)

    def spread(xs):
        m = sum(xs) / len(xs)
        return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5

    assert spread(block_means) > spread(iid)


def test_empty_arm_rejected():
    with pytest.raises(ValueError):
        sig.compare([], [1.0, 2.0])


def test_trade_pnls_extracts_from_result_like():
    class T:
        def __init__(self, pnl):
            self.pnl = pnl

    class R:
        trades = [T(1.5), T(-2.0)]

    assert sig.trade_pnls(R()) == [1.5, -2.0]
    assert sig.trade_pnls(object()) == []


# ---------------- CAPACITY vs EDGE (METHOD NOTE 5) ----------------
#
# §19b, §13(d) and §20a each misfired because the EDGE test was applied to a
# claim that was never about per-trade edge. `not_worse` puts the capacity rule
# in code so the next section cannot restate it slightly differently.

def _cmp(ci_low, ci_high):
    return sig.Comparison(n_baseline=50, n_candidate=50, baseline_mean=0.0,
                          candidate_mean=0.0, diff=0.0, ci_low=ci_low,
                          ci_high=ci_high, alpha=0.01, n_comparisons=5,
                          resamples=2000)


def test_edge_needs_the_interval_to_exclude_zero_in_its_favour():
    assert _cmp(1.0, 5.0).significant is True
    assert _cmp(-1.0, 5.0).significant is False
    assert _cmp(-5.0, -1.0).significant is False


def test_capacity_only_needs_to_rule_out_being_worse():
    """The whole point: a candidate that is merely INCONCLUSIVE on edge passes
    the capacity bar, because it never claimed a better edge."""
    inconclusive = _cmp(-1.0, 5.0)
    assert inconclusive.significant is False
    assert inconclusive.not_worse is True


def test_capacity_fails_when_the_candidate_is_significantly_worse():
    worse = _cmp(-5.0, -1.0)
    assert worse.not_worse is False
    assert worse.verdict == "SIGNIFICANTLY WORSE"


def test_capacity_is_strictly_weaker_than_edge_never_stronger():
    """Anything passing the EDGE bar must also pass the CAPACITY bar. If this
    ever inverts, one of the two definitions has drifted."""
    for lo, hi in [(1.0, 5.0), (0.01, 0.02), (-1.0, 5.0), (-5.0, -1.0), (0.0, 0.0)]:
        c = _cmp(lo, hi)
        if c.significant:
            assert c.not_worse, f"edge passed but capacity failed at CI[{lo},{hi}]"


def test_a_zero_width_interval_at_zero_passes_neither():
    c = _cmp(0.0, 0.0)
    assert c.significant is False and c.not_worse is False
