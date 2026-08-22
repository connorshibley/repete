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

§55: the caveat above turned out to have a price, and it is large
-----------------------------------------------------------------
"Safe direction" was the right call and an incomplete one. §23 gated a
relative-volume entry filter whose candidate trades are a strict SUBSET of the
baseline's: 128 of 179. Every shared trade therefore appears in both arms, and
its idiosyncratic P&L should cancel out of the difference. Under independent
resampling it does not cancel — it is counted twice.

What §23 was really asking, in arithmetic the independent test cannot see:
179 x $14.91 minus 128 x $31.78 means the filter REMOVED 51 trades averaging
-$27.43 and KEPT 128 averaging +$31.78. That is a question about two DISJOINT
samples. It was scored as a question about two overlapping ones, and came back
INCONCLUSIVE at P(better) = 85.8%.

`compare_paired` answers it with common random numbers: ONE moving-block index
draw per replicate, applied to BOTH arms, so the shared component cancels where
it genuinely is shared. `disjoint_report` adds the sharper nested form when one
arm's trades really are a subset of the other's.

Stated plainly, because a more powerful test is exactly the kind of thing that
gets mistaken for a lower bar: this changes the INSTRUMENT, not the pass mark.
`compare()` is untouched and every §1-§54 number still reproduces — pinned by
`tests/data/significance_golden.json`, frozen from the unmodified function
before this was written. And a sharper referee does not retroactively pass §23:
re-scoring it would be a fresh EDGE claim, which §52 froze.

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


class _IntervalVerdict:
    """The three readings of a confidence interval on (candidate - baseline).

    Shared by `Comparison` and `PairedComparison` deliberately: two copies of
    this logic is how a future change lands in one estimator and not the other,
    and the two would then disagree about what "significant" means while both
    printing the word. `tests/data/significance_golden.json` — frozen from the
    unmodified function before this class existed — is the proof that pulling
    it out moved nothing.
    """

    @property
    def significant(self) -> bool:
        """The interval excludes zero in the candidate's favour.

        This is the test for an **EDGE** claim (METHOD NOTE 5): "each trade is
        better." Wrong instrument for a capacity claim — see `not_worse`.
        """
        return self.ci_low > 0

    @property
    def not_worse(self) -> bool:
        """The interval does not exclude zero *against* the candidate.

        This is the test for a **CAPACITY** claim (METHOD NOTE 5): "the book
        reaches more, at no worse a per-trade edge." A capacity candidate is
        not claiming better trades, so demanding `ci_low > 0` would reject it
        for failing a claim it never made — the §19b misfire. What must be ruled
        out is the opposite: that reaching further made each trade WORSE.

        Deliberately permissive, and safe only because METHOD NOTE 5 keeps the
        deterministic drawdown and PF clauses mandatory alongside it. Capacity
        without an edge is otherwise just more slippage.
        """
        return self.ci_high > 0

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


@dataclass
class Comparison(_IntervalVerdict):
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


# ---- §55: the paired estimator ---------------------------------------------

# How many times a replicate may be redrawn when it happens to select no member
# of one arm. Bounded, and exhausting it RAISES rather than quietly returning a
# narrower interval built from fewer replicates than advertised.
_MAX_REDRAWS = 50


@dataclass
class PairedComparison(_IntervalVerdict):
    """A common-random-numbers CI on (candidate - baseline) mean P&L.

    Same three readings as `Comparison` — the shared `_IntervalVerdict` is what
    guarantees "significant" means the same thing in both — plus the overlap
    accounting, which is the part a reader needs in order to know whether the
    pairing bought anything at all.
    """
    n_baseline: int
    n_candidate: int
    baseline_mean: float
    candidate_mean: float
    diff: float
    ci_low: float
    ci_high: float
    alpha: float
    n_comparisons: int
    resamples: int
    n_shared: int                 # trades present in BOTH arms
    n_baseline_only: int          # in baseline, removed by the candidate
    n_candidate_only: int         # trades the candidate opened and baseline did not
    nested: bool                  # candidate's trades are a strict subset

    @property
    def shared_fraction(self) -> float:
        """0.0 means the pairing bought nothing; 1.0 means the arms took the
        identical trade set and the difference is exactly zero."""
        union = self.n_shared + self.n_baseline_only + self.n_candidate_only
        return self.n_shared / union if union else 0.0

    def describe(self) -> str:
        base = super().describe()
        shape = "nested" if self.nested else "overlapping"
        return (f"{base} [PAIRED, {shape}: {self.n_shared} shared, "
                f"{self.n_baseline_only} baseline-only, "
                f"{self.n_candidate_only} candidate-only, "
                f"{self.shared_fraction:.0%} common]")


def _keyed(keys: list, pnls: list, arm: str) -> dict:
    """key -> P&L, refusing a duplicate key rather than keeping the last.

    A duplicate would silently drop a closed trade out of one arm's mean while
    leaving it in `n_trades`, which is the kind of discrepancy that shows up
    three sections later as an unexplained gap. `(entry_ts, symbol)` is unique
    within a run because the book holds at most one position per symbol at a
    time — so a duplicate means the caller built the keys wrong, and saying so
    here is cheaper than debugging the interval.
    """
    if len(keys) != len(pnls):
        raise ValueError(f"{arm}: {len(keys)} keys for {len(pnls)} P&Ls")
    out: dict = {}
    for k, p in zip(keys, pnls):
        if k in out:
            raise ValueError(f"{arm}: duplicate trade key {k!r}")
        out[k] = float(p)
    return out


def compare_paired(baseline_keys: list, baseline_pnls: list,
                   candidate_keys: list, candidate_pnls: list, *,
                   n_comparisons: int = 1, resamples: int = DEFAULT_RESAMPLES,
                   seed: int = 20260723, alpha: float = ALPHA
                   ) -> PairedComparison:
    """Bootstrap CI on the difference, resampling both arms with ONE index draw.

    The keys identify a trade across arms; `trade_keys` builds them as
    `(entry_ts, symbol)`. **Time first, and that ordering is load-bearing** —
    the union is sorted on it, and the moving block is only meaningful over a
    temporally ordered sequence. Sorting `(symbol, entry_ts)` would group AAPL's
    2021 trades next to AAPL's 2024 trades and the blocks would preserve
    alphabetical clustering instead of the market clustering the whole method
    exists to respect.

    Each replicate draws one block sequence over the UNION of both arms' trades
    and computes each arm's mean over whichever drawn trades it actually owns.
    A trade both arms took therefore enters both means together, and its
    idiosyncratic P&L largely cancels in the difference — which is exactly the
    variance `compare()` counts twice.

    Degrades correctly: with zero overlap this reduces to something very close
    to the independent estimator, so it is never *wrong* to use, only sometimes
    pointless. `shared_fraction` says which case you are in.
    """
    if not baseline_pnls or not candidate_pnls:
        raise ValueError("both arms need at least one closed trade")
    b_map = _keyed(baseline_keys, baseline_pnls, "baseline")
    c_map = _keyed(candidate_keys, candidate_pnls, "candidate")

    union = sorted(set(b_map) | set(c_map))
    n = len(union)
    b_present = [k in b_map for k in union]
    c_present = [k in c_map for k in union]
    b_vals = [b_map.get(k, 0.0) for k in union]
    c_vals = [c_map.get(k, 0.0) for k in union]

    k_corr = max(1, int(n_comparisons))
    corrected = alpha / k_corr
    rng = random.Random(seed)
    block = _block_length(n)

    diffs = []
    for _ in range(resamples):
        for attempt in range(_MAX_REDRAWS):
            b_sum = c_sum = 0.0
            b_n = c_n = 0
            drawn = 0
            while drawn < n:
                start = rng.randrange(n)
                for i in range(block):
                    if drawn >= n:
                        break
                    j = (start + i) % n
                    if b_present[j]:
                        b_sum += b_vals[j]
                        b_n += 1
                    if c_present[j]:
                        c_sum += c_vals[j]
                        c_n += 1
                    drawn += 1
            if b_n and c_n:
                diffs.append(c_sum / c_n - b_sum / b_n)
                break
        else:
            # Only reachable when one arm is a vanishing sliver of the union.
            # Raising beats returning an interval built from fewer replicates
            # than the caller asked for and cannot tell it did not get.
            raise ValueError(
                f"could not draw a replicate containing both arms after "
                f"{_MAX_REDRAWS} attempts (union={n}, baseline={len(b_map)}, "
                f"candidate={len(c_map)}) — the arms are too disjoint in size "
                f"for a paired comparison; use compare()")
    diffs.sort()

    lo_i = int(math.floor((corrected / 2) * resamples))
    hi_i = min(int(math.ceil((1 - corrected / 2) * resamples)) - 1,
               resamples - 1)

    shared = set(b_map) & set(c_map)
    b_mean = sum(baseline_pnls) / len(baseline_pnls)
    c_mean = sum(candidate_pnls) / len(candidate_pnls)
    theta = c_mean - b_mean

    # BASIC (reverse-percentile) interval, NOT the percentile interval
    # `compare()` uses — and this is a measured correction, not a preference.
    #
    # Coverage was run before this line was written, 600 seeds against a true
    # null (removal by coin flip, so the effect is exactly zero):
    #
    #     percentile, block sqrt(n)   two-sided 0.0750   OVER-REJECTS
    #     basic,      block sqrt(n)   two-sided 0.0500   nominal
    #     percentile, block 1         two-sided 0.0600
    #     basic,      block 1         two-sided 0.0350   over-conservative
    #
    # The percentile interval is centred on the BOOTSTRAP distribution; the
    # basic interval reflects it about the observed statistic, which is what
    # corrects the shift between "how the resamples scatter" and "how the
    # estimate scatters".
    #
    # Why `compare()` does not need this and is not being changed: its
    # independent resampling is so over-conservative that it rejected 0.000 of
    # 120 true nulls, and 0.142 at a shift large enough for the paired test to
    # reach 0.883. That bias swamps the percentile bias in the SAFE direction.
    # Removing the independence bias is exactly what EXPOSES the percentile
    # one — so the correction belongs here and only here. Touching `compare()`
    # would move every number from §1 to §54.
    return PairedComparison(
        n_baseline=len(b_map), n_candidate=len(c_map),
        baseline_mean=b_mean, candidate_mean=c_mean, diff=theta,
        ci_low=2 * theta - diffs[hi_i], ci_high=2 * theta - diffs[lo_i],
        alpha=corrected, n_comparisons=k_corr, resamples=resamples,
        n_shared=len(shared),
        n_baseline_only=len(set(b_map) - shared),
        n_candidate_only=len(set(c_map) - shared),
        nested=set(c_map) < set(b_map))


def disjoint_report(baseline_keys: list, baseline_pnls: list,
                    candidate_keys: list, candidate_pnls: list, *,
                    n_comparisons: int = 1, resamples: int = DEFAULT_RESAMPLES,
                    seed: int = 20260723, alpha: float = ALPHA):
    """The sharpest form, available only when the candidate is a pure filter.

    When the candidate's trades are a strict subset of the baseline's, the
    difference in arm means is exactly

        (n_removed / n_baseline) x (mean_KEPT - mean_REMOVED)

    so "did the filter help?" and "are the trades it threw away worse than the
    ones it kept?" are the SAME question, and the second is a comparison of two
    genuinely DISJOINT samples. Independent resampling is not a compromise
    there, it is correct — which is why this delegates straight to `compare()`
    rather than reimplementing anything.

    Returns None when the candidate is not a strict subset. None means "this
    instrument does not apply", never "it applied and found nothing" — callers
    must not read it as a pass, the §33 RUN 1 failure.
    """
    b_map = _keyed(baseline_keys, baseline_pnls, "baseline")
    c_map = _keyed(candidate_keys, candidate_pnls, "candidate")
    if not set(c_map) < set(b_map):
        return None
    removed = [b_map[k] for k in sorted(set(b_map) - set(c_map))]
    kept = [c_map[k] for k in sorted(c_map)]
    if not removed or not kept:
        return None
    # baseline=REMOVED, candidate=KEPT, so `significant` reads as "the trades
    # the filter threw away were reliably worse than the ones it kept" — the
    # same direction as `significant` on the arms themselves.
    return compare(removed, kept, n_comparisons=n_comparisons,
                   resamples=resamples, seed=seed, alpha=alpha)


def trade_pnls(result) -> list:
    """Extract closed-trade P&L from a backtest.Result (or anything with
    `.trades` whose items carry `.pnl`)."""
    return [float(t.pnl) for t in getattr(result, "trades", [])]


def trade_keys(result) -> list:
    """Cross-arm trade identity, `(entry_ts, symbol)`, in the same order as
    `trade_pnls`.

    TIME FIRST — see `compare_paired`. The tuple is deliberately not
    `(symbol, entry_ts)`, which reads more naturally and would silently make
    the moving block cluster by ticker instead of by market.
    """
    return [(str(t.entry_ts), str(t.symbol))
            for t in getattr(result, "trades", [])]


# ---- Deflated Sharpe Ratio (Bailey & López de Prado, 2014) -----------------
#
# Added 2026-08-22. This is the multiple-testing control the audit said the
# project lacked, and the reason it lacked it was not the formula — it was
# that nobody could count N. Step 3a/3b of the remediation made N countable
# (tests/trial_tally.py, research/trials.jsonl); this is what N is FOR.
#
# Bonferroni on a pre-declared K — what `compare` does above — asks "how many
# EDGE claims did we say we'd make?" DSR asks the question that actually
# matters: "given how many things were tried, what Sharpe would the BEST of
# them have by luck alone, and does the observed one beat that?" The two
# agree only when K is honest and every trial is an EDGE claim. Here, 187 of
# 225 logged arm runs are DIAGNOSTIC — selection that spends no K — which is
# exactly the gap DSR prices and Bonferroni-on-K cannot see.
#
# stdlib only, on purpose. This module imports math, random and dataclasses
# and is mypy-gated in CI (ci.yml); NormalDist covers everything needed.

EULER_GAMMA = 0.5772156649015329


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """E[max SR] over n_trials independent null strategies, Bailey & LdP eq. 5.

    The approximation: sqrt(V) * ((1-γ) Φ⁻¹(1 - 1/N) + γ Φ⁻¹(1 - 1/(N e))).
    This is the bar a strategy has to clear just to be distinguishable from
    the luckiest of N coin flips.
    """
    from statistics import NormalDist
    if n_trials < 2 or var_sharpe <= 0:
        return 0.0
    z = NormalDist().inv_cdf
    n = float(n_trials)
    return (var_sharpe ** 0.5) * (
        (1.0 - EULER_GAMMA) * z(1.0 - 1.0 / n)
        + EULER_GAMMA * z(1.0 - 1.0 / (n * math.e)))


def probabilistic_sharpe(sr: float, sr_star: float, n_obs: int,
                         skew: float, kurtosis: float) -> float:
    """PSR: P(true SR > sr_star | observed sr), Bailey & LdP 2012 eq. 12.

    `kurtosis` is RAW (3 for a normal), not excess. Passing excess kurtosis
    here silently understates the denominator by 3 and inflates every answer.
    """
    from statistics import NormalDist
    if n_obs < 2:
        return 0.0
    denom_sq = 1.0 - skew * sr + (kurtosis - 1.0) / 4.0 * sr * sr
    if denom_sq <= 0:
        return 0.0
    z = (sr - sr_star) * ((n_obs - 1) ** 0.5) / denom_sq ** 0.5
    return NormalDist().cdf(z)


@dataclass(frozen=True)
class DeflatedSharpe:
    """One DSR reading, with every input it was computed from.

    Carries the inputs rather than just the answer for the same reason
    _IntervalVerdict above carries its interval: a number that cannot be
    re-derived from what is printed beside it is a number nobody can check.
    """
    sharpe: float
    n_trials: int
    n_obs: int
    skew: float
    kurtosis: float
    var_sharpe: float
    sr_max_expected: float   # the luck bar
    psr: float               # P(true SR > luck bar)

    @property
    def clears(self) -> bool:
        """Does it beat the luckiest-of-N bar at 1 - ALPHA confidence?"""
        return self.psr >= 1.0 - ALPHA

    def line(self) -> str:
        return (f"DSR: SR {self.sharpe:.4f} vs luck bar {self.sr_max_expected:.4f} "
                f"over N={self.n_trials} trials, T={self.n_obs} obs "
                f"(skew {self.skew:+.2f}, kurt {self.kurtosis:.2f}) -> "
                f"PSR {self.psr:.3f} {'CLEARS' if self.clears else 'does not clear'}")


def deflated_sharpe(sharpe: float, var_sharpe: float, n_trials: int,
                    n_obs: int, skew: float, kurtosis: float) -> DeflatedSharpe:
    """The full reading. `var_sharpe` is the variance of the Sharpes ACROSS
    the N trials — which is why the trial log has to carry each run's Sharpe,
    and why the 225 backfilled rows (no curve, no Sharpe) cannot feed this.

    Every argument is required. No defaults, because a default here would be
    a silent assumption about the search that produced the number — and
    pricing that search is the entire point.
    """
    sr_max = expected_max_sharpe(n_trials, var_sharpe)
    psr = probabilistic_sharpe(sharpe, sr_max, n_obs, skew, kurtosis)
    return DeflatedSharpe(sharpe=sharpe, n_trials=n_trials, n_obs=n_obs,
                          skew=skew, kurtosis=kurtosis, var_sharpe=var_sharpe,
                          sr_max_expected=sr_max, psr=psr)
