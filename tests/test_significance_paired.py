"""§55. The paired estimator, validated on data whose truth is KNOWN.

Every other gate in this repo asks a question about markets, where the answer
is unknown and the verdict is the whole point. This file asks a question about
an ESTIMATOR, where the answer can be constructed — so these tests are checks
in a way no §-gate can be.

Three things have to hold, and the first is the one that matters most:

  1. COVERAGE. On data built with a true effect of exactly zero, the test must
     reject at most alpha. A more powerful test that over-rejects is not more
     powerful, it is broken, and it would launder noise into verdicts. This was
     written before the estimator worked: the first implementation used the
     percentile interval and rejected 0.0750 against a nominal 0.0500, and this
     test is what said so.
  2. POWER. On data with a real effect, it must beat `compare()` outright.
     Otherwise there is no reason to have it.
  3. REPRODUCTION. `compare()` itself must not have moved a digit —
     `tests/test_significance_golden.py`.

The corpora are fat-tailed on purpose (a normal core plus an 8% tail draw at
6x spread) because that is the shape the module docstring names and the shape
that makes swing-trade P&L hard to test at n = 60-260.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import significance as sig  # noqa: E402

ALPHA = 0.05

# Sized so the coverage test can SEPARATE the correct estimator from the broken
# one. The percentile version measured 0.0750; at 600 seeds the acceptance
# bound is alpha + 2.5 standard errors = 0.0722, which is below it. Fewer seeds
# would widen the bound past 0.0750 and the test would pass either way — a
# check that cannot fail.
COVERAGE_SEEDS = 600
RESAMPLES = 400


def synth(seed: int, n: int = 179, keep: int = 128, shift: float = 0.0,
          *, tail: float = 0.08, spread: float = 120.0, mean: float = 14.91):
    """A baseline arm and a filtered candidate arm, shaped like §23.

    n=179 and keep=128 are §23's real trade counts. `shift` moves the REMOVED
    trades' mean down: 0.0 is the exact null.

    Removal is by coin flip, NEVER by outcome. Removing the worst trades would
    be an oracle filter, and an oracle leaves no shared variance to cancel —
    measured: the pairing narrows a realistic filter's interval 2.1x and an
    oracle's not at all. No real filter is in the oracle case, and testing
    against one would understate the method it is meant to validate.
    """
    rng = random.Random(seed)
    keys = [(f"20{24 + i // 250:02d}-{1 + (i // 20) % 12:02d}-"
             f"{1 + i % 20:02d}T21:00:00Z", f"S{i % 38}") for i in range(n)]
    removed = set(rng.sample(range(n), n - keep))
    pnls = []
    for i in range(n):
        mu = mean - (shift if i in removed else 0.0)
        pnls.append(rng.gauss(mu, spread * 6) if rng.random() < tail
                    else rng.gauss(mu, spread))
    kept = [i for i in range(n) if i not in removed]
    return keys, pnls, [keys[i] for i in kept], [pnls[i] for i in kept]


# ---- 1. coverage -----------------------------------------------------------

def test_coverage_on_a_true_null_is_at_most_alpha():
    """THE decisive test. Reject rate on zero effect must not exceed alpha.

    Two-sided, because an estimator that over-rejects "candidate worse" is
    just as broken as one that over-rejects "candidate better" — it would make
    a harmless filter look actively harmful.
    """
    high = low = 0
    for s in range(COVERAGE_SEEDS):
        c = sig.compare_paired(*synth(5000 + s, shift=0.0),
                               n_comparisons=1, resamples=RESAMPLES,
                               alpha=ALPHA)
        high += c.ci_low > 0
        low += c.ci_high < 0
    rate = (high + low) / COVERAGE_SEEDS
    se = math.sqrt(ALPHA * (1 - ALPHA) / COVERAGE_SEEDS)
    bound = ALPHA + 2.5 * se
    assert rate <= bound, (
        f"paired estimator over-rejects: {rate:.4f} two-sided "
        f"(high {high / COVERAGE_SEEDS:.4f}, low {low / COVERAGE_SEEDS:.4f}) "
        f"against nominal {ALPHA} and a bound of {bound:.4f}. An interval that "
        f"does not cover is not a sharper instrument, it is a broken one.")


def test_the_two_tails_are_roughly_balanced():
    """A one-sided defect would pass the test above while biasing every verdict
    in one direction — the failure mode that check alone cannot see."""
    high = low = 0
    for s in range(COVERAGE_SEEDS):
        c = sig.compare_paired(*synth(5000 + s, shift=0.0),
                               n_comparisons=1, resamples=RESAMPLES,
                               alpha=ALPHA)
        high += c.ci_low > 0
        low += c.ci_high < 0
    assert high + low > 0, "no rejections at all — the corpus is degenerate"
    skew = abs(high - low) / (high + low)
    assert skew < 0.6, f"tails lopsided: {high} high vs {low} low"


# ---- 2. power --------------------------------------------------------------

@pytest.mark.parametrize("shift,floor", [(80.0, 0.40), (120.0, 0.70)])
def test_paired_detects_real_effects_the_incumbent_misses(shift, floor):
    """Measured on the same seeds and the same alpha, so the only difference
    is the estimator. At shift=80 the incumbent finds 2-3 of 100 and the
    paired test finds ~65 — that gap is the whole argument for §55."""
    seeds = 100
    ind = pair = 0
    for s in range(seeds):
        bk, bp, ck, cp = synth(1000 + s, shift=shift)
        ind += sig.compare(bp, cp, n_comparisons=1,
                           resamples=RESAMPLES).significant
        pair += sig.compare_paired(bk, bp, ck, cp, n_comparisons=1,
                                   resamples=RESAMPLES).significant
    assert pair / seeds >= floor, (
        f"paired power {pair / seeds:.3f} below {floor} at shift {shift}")
    assert pair > ind, (
        f"paired ({pair}/{seeds}) did not beat independent ({ind}/{seeds}) "
        f"at shift {shift} — then there is no reason for this estimator")


def test_the_incumbent_is_the_conservative_one_and_that_is_the_point():
    """Pins the direction of the bias the module docstring claims.

    If this ever inverts, the §55 argument is wrong and the write-up needs
    retracting, not patching.
    """
    bk, bp, ck, cp = synth(4242, shift=80.0)
    ind = sig.compare(bp, cp, n_comparisons=1, resamples=RESAMPLES)
    par = sig.compare_paired(bk, bp, ck, cp, n_comparisons=1,
                             resamples=RESAMPLES)
    assert (ind.ci_high - ind.ci_low) > (par.ci_high - par.ci_low)


# ---- 3. the honest limits --------------------------------------------------

def test_with_no_shared_trades_the_pairing_buys_nothing():
    """Degrades correctly rather than silently misbehaving.

    Zero overlap means there is no shared variance to cancel, so the interval
    should land near the independent one. Documenting this stops
    `compare_paired` from being read as free power in every situation.
    """
    bk, bp, _, _ = synth(77)
    ck = [(ts.replace("20", "19", 1), sym) for ts, sym in bk][:120]
    cp = bp[:120]
    par = sig.compare_paired(bk, bp, ck, cp, resamples=RESAMPLES)
    assert par.n_shared == 0
    assert par.shared_fraction == 0.0
    assert not par.nested
    ind = sig.compare(bp, cp, resamples=RESAMPLES)
    ratio = (par.ci_high - par.ci_low) / (ind.ci_high - ind.ci_low)
    assert 0.6 < ratio < 1.6, f"disjoint arms diverged from independent: {ratio:.2f}"


def test_identical_arms_give_a_zero_difference():
    bk, bp, _, _ = synth(31)
    par = sig.compare_paired(bk, bp, bk, bp, resamples=RESAMPLES)
    assert par.diff == 0.0
    assert par.n_shared == len(bp)
    assert par.shared_fraction == 1.0
    assert not par.nested, "a set is not a STRICT subset of itself"
    assert par.ci_low == 0.0 and par.ci_high == 0.0
    assert par.verdict == "INCONCLUSIVE"


# ---- overlap accounting ----------------------------------------------------

def test_a_filter_is_reported_as_nested():
    bk, bp, ck, cp = synth(11)
    par = sig.compare_paired(bk, bp, ck, cp, resamples=RESAMPLES)
    assert par.nested
    assert (par.n_shared, par.n_baseline_only, par.n_candidate_only) == (128, 51, 0)
    assert "nested" in par.describe() and "128 shared" in par.describe()


def test_an_arm_that_opens_new_trades_is_not_nested():
    bk, bp, ck, cp = synth(12)
    ck = ck + [("2099-01-01T21:00:00Z", "NEW")]
    cp = cp + [123.45]
    par = sig.compare_paired(bk, bp, ck, cp, resamples=RESAMPLES)
    assert not par.nested and par.n_candidate_only == 1
    assert "overlapping" in par.describe()


def test_duplicate_keys_are_refused_not_collapsed():
    """Silently keeping the last would drop a closed trade from one arm's mean
    while `n_trades` still counted it — a discrepancy that surfaces sections
    later as an unexplained gap."""
    with pytest.raises(ValueError, match="duplicate trade key"):
        sig.compare_paired([("a", "X"), ("a", "X")], [1.0, 2.0],
                           [("a", "X")], [1.0], resamples=50)


def test_mismatched_key_and_pnl_counts_are_refused():
    with pytest.raises(ValueError, match="keys for"):
        sig.compare_paired([("a", "X")], [1.0, 2.0], [("a", "X")], [1.0],
                           resamples=50)


def test_an_empty_arm_is_refused():
    with pytest.raises(ValueError, match="at least one closed trade"):
        sig.compare_paired([("a", "X")], [1.0], [], [], resamples=50)


# ---- the disjoint (nested) report ------------------------------------------

def test_disjoint_report_compares_the_removed_trades_against_the_kept_ones():
    """The sharpest form. §23's real question, asked correctly."""
    bk, bp, ck, cp = synth(13, shift=120.0)
    rep = sig.disjoint_report(bk, bp, ck, cp, n_comparisons=1,
                              resamples=RESAMPLES)
    assert rep is not None
    assert (rep.n_baseline, rep.n_candidate) == (51, 128)   # removed vs kept
    kept_mean = sum(cp) / len(cp)
    assert rep.candidate_mean == pytest.approx(kept_mean)
    assert rep.diff > 0, "the filter threw away worse trades; diff must be > 0"


def test_disjoint_report_is_none_when_the_candidate_is_not_a_pure_filter():
    """None means 'this instrument does not apply', never 'it applied and
    found nothing' — the §33 RUN 1 failure, where checks that quietly did not
    run manufactured a VALIDATED verdict."""
    bk, bp, ck, cp = synth(14)
    assert sig.disjoint_report(bk, bp, ck + [("2099-01-01T21:00:00Z", "NEW")],
                               cp + [1.0], resamples=RESAMPLES) is None
    assert sig.disjoint_report(bk, bp, bk, bp, resamples=RESAMPLES) is None


def test_the_disjoint_identity_holds():
    """The arithmetic §55 rests on:

        arm diff == (n_removed / n_baseline) x (mean_kept - mean_removed)

    If this drifts, "did the filter help?" and "are the removed trades worse?"
    have stopped being the same question and the disjoint report is measuring
    something else.
    """
    bk, bp, ck, cp = synth(15, shift=60.0)
    removed = [p for k, p in zip(bk, bp) if k not in set(ck)]
    mean_kept = sum(cp) / len(cp)
    mean_rem = sum(removed) / len(removed)
    arm_diff = mean_kept - sum(bp) / len(bp)
    assert arm_diff == pytest.approx(
        (len(removed) / len(bp)) * (mean_kept - mean_rem), rel=1e-9)


# ---- determinism and the key contract --------------------------------------

def test_deterministic_across_runs():
    args = synth(21, shift=50.0)
    a = sig.compare_paired(*args, resamples=RESAMPLES)
    b = sig.compare_paired(*args, resamples=RESAMPLES)
    assert (a.ci_low, a.ci_high, a.diff) == (b.ci_low, b.ci_high, b.diff)


def test_bonferroni_widens_the_paired_interval_too():
    args = synth(22, shift=50.0)
    narrow = sig.compare_paired(*args, n_comparisons=1, resamples=RESAMPLES)
    wide = sig.compare_paired(*args, n_comparisons=15, resamples=RESAMPLES)
    assert (wide.ci_high - wide.ci_low) > (narrow.ci_high - narrow.ci_low)


def test_trade_keys_put_time_first_and_align_with_trade_pnls():
    """Load-bearing, and the reason it has its own test: `(symbol, entry_ts)`
    reads more naturally and would make the moving block cluster by TICKER
    instead of by market, silently defeating the reason the block exists.
    """
    class T:
        def __init__(self, s, ts, p):
            self.symbol, self.entry_ts, self.pnl = s, ts, p

    class R:
        trades = [T("ZZZ", "2024-01-02T21:00:00Z", 5.0),
                  T("AAA", "2024-03-04T21:00:00Z", -2.0)]

    keys = sig.trade_keys(R())
    assert keys == [("2024-01-02T21:00:00Z", "ZZZ"),
                    ("2024-03-04T21:00:00Z", "AAA")]
    assert sorted(keys) == keys, "sorting keys must give TIME order"
    assert len(keys) == len(sig.trade_pnls(R()))
    assert sig.trade_keys(object()) == []
