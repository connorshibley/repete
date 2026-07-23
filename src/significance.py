"""Is a backtest difference real, or is it the 14th roll of the dice?

METHOD NOTE 2 in `knowledge/backtest_candidates.md` committed to raising the
bar as the trial count grows, but left it as a instruction to "prefer a clear
margin" — a judgement call, which is exactly the kind of thing that bends
toward whatever result you were hoping for. This module makes it arithmetic.

What it does
------------
Given the closed trades of a BASELINE arm and a CANDIDATE arm, resample both
and ask how often the candidate's per-trade edge survives. The output is a
confidence interval on the difference and a verdict at a **Bonferroni-corrected
level**: testing K arms in a section at nominal 5% means each arm is judged at
5%/K, and the cumulative trial count across the whole research programme widens
it further. A candidate that only wins at the uncorrected level is reported as
INCONCLUSIVE, not as a pass.

Why a moving-block bootstrap, not a plain one
---------------------------------------------
Swing trades overlap in time and share market regimes: a good quarter produces
a run of winners together. Resampling individual trades independently would
break that clustering and understate the true variance — making everything look
significant. Blocks of consecutive trades (length ~sqrt(n)) keep neighbouring
outcomes together, so the interval reflects "how much of this is one lucky
stretch of market?".

The conservative-direction caveat
---------------------------------
Baseline and candidate run on the SAME bars, so their trades are dependent. We
resample them independently, which overstates the variance of the difference —
i.e. it makes passing HARDER than a paired test would. That bias is in the safe
direction for a gate, and it is why a PASS here is worth something and a FAIL
is not automatically fatal.

What it still cannot do
-----------------------
Correct for the fact that the OOS window itself has been mined (METHOD NOTE 2).
A p-value computed on a selection set is a statement about *this dataset*, not
about the future. This narrows one failure mode; the live forward record
remains the only clean holdout.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

# Nominal family-wise error rate before correction.
ALPHA = 0.05
DEFAULT_RESAMPLES = 5000


@dataclass
class Comparison:
    """Result of one baseline-vs-candidate significance test."""
    n_baseline: int
    n_candidate: int
    baseline_mean: float          # mean P&L per closed trade, $
    candidate_mean: float
    diff: float                   # candidate - baseline
    ci_low: float                 # corrected-level CI on the difference
    ci_high: float
    alpha: float                  # the corrected level actually applied
    n_comparisons: int            # K used for the correction
    resamples: int

    @property
    def significant(self) -> bool:
        """The interval excludes zero in the candidate's favour."""
        return self.ci_low > 0

    @property
    def verdict(self) -> str:
        if self.ci_low > 0:
            return "SIGNIFICANT"
        if self.ci_high < 0:
            return "SIGNIFICANTLY WORSE"
        return "INCONCLUSIVE"

    def describe(self) -> str:
        pct = (1 - self.alpha) * 100
        return (f"{self.verdict}: candidate ${self.candidate_mean:+.2f}/trade "
                f"(n={self.n_candidate}) vs baseline ${self.baseline_mean:+.2f}/trade "
                f"(n={self.n_baseline}); diff ${self.diff:+.2f}, "
                f"{pct:.2f}% CI [${self.ci_low:+.2f}, ${self.ci_high:+.2f}] "
                f"(Bonferroni K={self.n_comparisons})")


def _block_length(n: int) -> int:
    """~sqrt(n), the standard rule of thumb; at least 1, at most n."""
    return max(1, min(n, int(round(math.sqrt(n)))))


def _resample_mean(pnls: list, rng: random.Random) -> float:
    """One moving-block bootstrap replicate of the mean."""
    n = len(pnls)
    b = _block_length(n)
    out = []
    while len(out) < n:
        start = rng.randrange(n)
        # Wrap around, so every observation has equal chance of appearing —
        # otherwise the tails of the series are systematically under-sampled.
        out.extend(pnls[(start + i) % n] for i in range(b))
    return sum(out[:n]) / n


def compare(baseline_pnls: list, candidate_pnls: list, *,
            n_comparisons: int = 1, resamples: int = DEFAULT_RESAMPLES,
            seed: int = 20260723, alpha: float = ALPHA) -> Comparison:
    """Bootstrap CI on (candidate - baseline) mean P&L per trade.

    `n_comparisons` is K for the Bonferroni correction: pass the number of arms
    being tested in this section, or the cumulative trial count if you want the
    programme-wide bar. Seed is fixed so a re-run reproduces the verdict.
    """
    if not baseline_pnls or not candidate_pnls:
        raise ValueError("both arms need at least one closed trade")
    k = max(1, int(n_comparisons))
    corrected = alpha / k

    rng = random.Random(seed)
    diffs = sorted(_resample_mean(candidate_pnls, rng)
                   - _resample_mean(baseline_pnls, rng)
                   for _ in range(resamples))

    lo_i = int(math.floor((corrected / 2) * resamples))
    hi_i = int(math.ceil((1 - corrected / 2) * resamples)) - 1
    hi_i = min(hi_i, resamples - 1)

    b_mean = sum(baseline_pnls) / len(baseline_pnls)
    c_mean = sum(candidate_pnls) / len(candidate_pnls)
    return Comparison(
        n_baseline=len(baseline_pnls), n_candidate=len(candidate_pnls),
        baseline_mean=b_mean, candidate_mean=c_mean, diff=c_mean - b_mean,
        ci_low=diffs[lo_i], ci_high=diffs[hi_i],
        alpha=corrected, n_comparisons=k, resamples=resamples)


def trade_pnls(result) -> list:
    """Extract closed-trade P&L from a backtest.Result (or anything with
    `.trades` whose items carry `.pnl`)."""
    return [float(t.pnl) for t in getattr(result, "trades", [])]
