"""§58 volatility-contraction precondition — base.range_ratio,
base.contraction_pctile, risk.contraction_blocked and the history it needs.

Three things here are load-bearing and would be worthless if they could not
fail:

  * THE FAIL-OPEN TRAP. The rail permits an entry when it cannot compute a
    percentile. That is the right polarity for a data gap and the wrong one for
    a starved rail: hand it 253 bars when it needs 272 and it blocks nothing
    while appearing in config, in the census keys, and in every code path. The
    `max_lookback_bars` tests are what stand between this rail and being
    decoration.
  * THE FAST PATH. `contraction_pctile` restates `range_ratio`'s arithmetic
    rather than calling it in a loop, because the loop form is ~1000x slower.
    A duplicated formula that nothing pins WILL drift, so it is pinned.
  * THE SHIPPED CONFIG DOES NOT MOVE. 0 means off, and off has to mean the
    entry loop behaves exactly as it did before this file existed.
"""
import sys

import pytest

sys.path.insert(0, "src")
import risk                                                   # noqa: E402
import strategies                                             # noqa: E402
from strategies import base                                   # noqa: E402


def bar(high, low, close=None, volume=1000):
    return {"high": high, "low": low, "close": close if close is not None
            else (high + low) / 2, "open": low, "volume": volume}


def flat(n, high=101.0, low=99.0, close=100.0):
    return [bar(high, low, close) for _ in range(n)]


# ---------------------------------------------------------------- range_ratio

def test_range_ratio_is_the_span_over_price():
    """(max high - min low) / last close. 110-90 over 100 = 0.20."""
    bars = flat(19) + [bar(110.0, 90.0, 100.0)]
    assert base.range_ratio(bars, 20) == pytest.approx(0.20)


def test_range_ratio_reads_the_WHOLE_window_not_just_today():
    """A quiet last bar inside a violent month is not a coil. If this only
    looked at the final bar the statistic would measure a single day's range,
    which is what ATR-of-1 already is."""
    bars = [bar(150.0, 50.0, 100.0)] + flat(19)
    assert base.range_ratio(bars, 20) == pytest.approx(1.0)
    assert base.range_ratio(bars, 5) == pytest.approx(0.02)   # window excludes it


def test_range_ratio_is_scale_free():
    """The whole reason for dividing by price: a $600 fund and a $30 fund with
    the same proportional range must score identically, or the rail would be a
    price filter wearing a volatility costume."""
    cheap = [bar(30.6, 29.4, 30.0) for _ in range(20)]
    dear = [bar(612.0, 588.0, 600.0) for _ in range(20)]
    assert base.range_ratio(cheap, 20) == pytest.approx(base.range_ratio(dear, 20))


@pytest.mark.parametrize("bars,period", [
    (flat(19), 20),                       # one bar short
    (flat(20), 0),                        # nonsense period
])
def test_range_ratio_returns_None_rather_than_a_number(bars, period):
    assert base.range_ratio(bars, period) is None


def test_range_ratio_refuses_a_non_positive_close():
    """Dividing by zero or a negative price would produce a number the caller
    would trust. None is the only honest answer."""
    assert base.range_ratio(flat(19) + [bar(1.0, 0.0, 0.0)], 20) is None


# ----------------------------------------------------------- contraction_pctile

def _widening(n, period=20, lookback=252):
    """Ranges that grow monotonically, so today is the WIDEST window."""
    out = []
    for i in range(n):
        half = 1.0 + i * 0.05
        out.append(bar(100.0 + half, 100.0 - half, 100.0))
    return out


def test_the_widest_window_in_its_own_year_scores_100():
    assert base.contraction_pctile(_widening(272), 20, 252) == pytest.approx(100.0)


def test_the_narrowest_window_in_its_own_year_scores_0():
    bars = list(reversed(_widening(272)))
    assert base.contraction_pctile(bars, 20, 252) == pytest.approx(0.0)


def test_a_dead_flat_series_scores_0_not_50():
    """The strict-`<` polarity, stated in the docstring and pinned here. A
    symbol whose range never moves IS coiled; ranking it at the median would
    have the quietest possible instrument read as average and be excluded by
    every threshold below 50."""
    assert base.contraction_pctile(flat(272), 20, 252) == pytest.approx(0.0)


def test_it_is_a_percentile_of_ITSELF_not_of_a_fixed_number():
    """The claim that makes this usable across a cross-section: a volatile fund
    and a sleepy one, each at their own quietest, must both score 0. If the
    statistic were absolute, XLU would always pass and XLE never would, and the
    rail would be a symbol filter."""
    calm = [bar(100.2 + i * 0.001, 99.8 - i * 0.001, 100.0) for i in range(272)]
    wild = [bar(110.0 + i * 0.05, 90.0 - i * 0.05, 100.0) for i in range(272)]
    assert base.contraction_pctile(list(reversed(calm)), 20, 252) \
        == base.contraction_pctile(list(reversed(wild)), 20, 252) == 0.0


def test_it_needs_period_PLUS_lookback_bars_and_says_None_below_that():
    """271 is not enough for 20+252. The rail fails OPEN on None, so this
    boundary is the difference between a filter and a no-op."""
    assert base.contraction_pctile(flat(271), 20, 252) is None
    assert base.contraction_pctile(flat(272), 20, 252) is not None


def test_the_fast_path_ORDERS_the_same_as_range_ratio():
    """`contraction_pctile` restates `range_ratio`'s arithmetic for speed, and
    this is what pins the copy to the original.

    WHAT IT CAN AND CANNOT CATCH, because the first version of this test
    overclaimed and a mutation proved it. A percentile depends only on the
    ORDER of the ratios, so multiplying every one of them by a constant is
    invisible here — and correctly so: the only consumer of this statistic is
    the rank, so a uniform rescale is a no-op rather than a defect. A mutation
    that scaled the fast path by 1.0001 SURVIVED this assertion, and the fix was
    to say so rather than to pretend otherwise.

    What does change the rank, and what this therefore does catch, is a formula
    that windows or anchors differently — the drift that actually matters
    between two copies of one calculation. `test_the_fast_path_uses_the_SAME
    _window` below is the sharper half."""
    bars = _widening(272)
    ratios = [base.range_ratio(bars[:i], 20) for i in range(20, 273)]
    today = ratios[-1]
    expected = 100.0 * sum(1 for r in ratios[:-1] if r < today) / 252
    assert base.contraction_pctile(bars, 20, 252) == pytest.approx(expected)


def test_the_fast_path_uses_the_SAME_window():
    """An off-by-one in the fast path's slice is the drift a percentile CAN see,
    because it reorders the ratios rather than rescaling them.

    One spike bar, moved by exactly one position across today's window
    boundary. Outside it, today is the quietest window of the year and scores
    0. Inside it, today is as wide as a window gets.

    The inside case is 92.5 rather than 100 and that is not slack: the spike
    also sits inside the nineteen most recent HISTORICAL windows, which are
    therefore exactly as wide as today's, and the rank is strict. 233 of 252
    are strictly narrower. Asserting 100 here would be asserting something
    false about how ranges age out of a rolling window."""
    outside = flat(300)
    outside[300 - 21] = bar(500.0, 1.0, 100.0)   # just OUTSIDE today's 20 bars
    assert base.contraction_pctile(outside, 20, 252) == pytest.approx(0.0)

    inside = flat(300)
    inside[300 - 20] = bar(500.0, 1.0, 100.0)    # just INSIDE today's 20 bars
    assert base.contraction_pctile(inside, 20, 252) == pytest.approx(
        100.0 * 233 / 252)


# --------------------------------------------------- the rail: contraction_blocked

def cfg(pctile=None, strategy_pctile=None, name="tsmom"):
    out = {"risk": {}, "strategies": {name: {}}}
    if pctile is not None:
        out["risk"]["max_contraction_pctile"] = pctile
    if strategy_pctile is not None:
        out["strategies"][name]["max_contraction_pctile"] = strategy_pctile
    return out


def test_the_rail_is_OFF_by_default():
    """Zero is disabled, matching min_rvol. The widest bars in a year must pass
    when nothing is configured, or merely adding this file would change live."""
    assert risk.contraction_blocked(_widening(272), cfg()) is False


def test_a_wide_range_is_blocked_when_the_rail_is_on():
    assert risk.contraction_blocked(_widening(272), cfg(pctile=10)) is True


def test_a_coiled_range_is_allowed_when_the_rail_is_on():
    assert risk.contraction_blocked(
        list(reversed(_widening(272))), cfg(pctile=10)) is False


def test_the_threshold_is_inclusive_at_its_own_boundary():
    """`> threshold` blocks, so a name sitting exactly ON the threshold is
    admitted. Pinned because the off-by-one here silently changes what every
    §58 arm measured."""
    bars = _widening(272)
    exact = base.contraction_pctile(bars, 20, 252)
    assert risk.contraction_blocked(bars, cfg(pctile=exact)) is False
    assert risk.contraction_blocked(bars, cfg(pctile=exact - 0.001)) is True


def test_it_FAILS_OPEN_on_insufficient_history():
    """A data gap must not silently halt all trading — the freshness and
    cross-check rails own that failure class. The cost of this polarity is the
    starved-rail trap, which max_lookback_bars below is what closes."""
    assert risk.contraction_blocked(flat(50), cfg(pctile=10)) is False


def test_a_per_strategy_threshold_beats_the_global_one():
    c = cfg(pctile=10, strategy_pctile=100, name="tsmom")
    c["strategies"]["meanrev"] = {}
    assert risk.contraction_blocked(_widening(272), c, "tsmom") is False
    assert risk.contraction_blocked(_widening(272), c, "meanrev") is True


def test_period_and_lookback_are_configurable():
    """The arms in §58 move only the threshold, but the window is config rather
    than a literal so a later section can widen it without editing risk.py."""
    c = cfg(pctile=10)
    c["risk"].update(contraction_period=5, contraction_lookback=30)
    assert risk.contraction_blocked(_widening(35), c) is True
    assert risk.contraction_blocked(_widening(34), c) is False   # one short


# ------------------------------------------- the history the rail must be given

def test_the_shipped_config_lookback_does_NOT_move():
    """253 bars, exactly as before this rail existed. If adding a DISABLED rail
    changed how much history live fetches, the change would not be a no-op and
    every §1-§57 number would have been scored on a different warmup.

    253 rather than the enabled ensemble's 201: `max_lookback_bars` covers
    disabled strategies too, because their exits keep working after they are
    switched off, and xsmom asks for 253."""
    import yaml
    with open("config.yaml") as f:
        shipped = yaml.safe_load(f)
    assert shipped["risk"]["max_contraction_pctile"] == 0
    assert strategies.max_lookback_bars(shipped) == 253


def test_turning_the_rail_on_BUYS_the_history_it_needs():
    """The trap this closes: the shipped fetch is 253 bars, the rail needs 272,
    and the rail does not raise when starved — it returns None and permits every
    entry. Without this the filter would be installed, called on every signal,
    and block nothing."""
    import yaml
    with open("config.yaml") as f:
        shipped = yaml.safe_load(f)
    shipped["risk"]["max_contraction_pctile"] = 10
    assert strategies.max_lookback_bars(shipped) == 272


def test_a_PER_STRATEGY_threshold_also_buys_the_history():
    """An arm that arms the rail on one strategy only must not starve it. This
    is the half that a `risk:`-only check would miss."""
    import yaml
    with open("config.yaml") as f:
        shipped = yaml.safe_load(f)
    shipped["strategies"]["tsmom"]["max_contraction_pctile"] = 10
    assert strategies.max_lookback_bars(shipped) == 272


def test_extra_lookback_is_zero_while_the_rail_is_off():
    """Stated separately from the 253 above so a failure says WHICH half broke:
    the rail's contribution, or a strategy's own required_lookback."""
    assert risk.extra_lookback_bars({"risk": {}, "strategies": {"tsmom": {}}}) == 0
    assert risk.extra_lookback_bars({}) == 0


# --------------------------------------------------- it is wired everywhere it must be

def test_the_rail_runs_in_live_and_in_BOTH_simulators():
    """Five sim/live divergences have already cost real rework, and every one of
    them was a rail present in one place and absent in another. Reading the
    source is crude, and it is what would have caught §13 on the day."""
    seen = {}
    for path in ("src/main.py", "src/backtest.py"):
        with open(path) as f:
            seen[path] = f.read().count("risk.contraction_blocked(")
    assert seen["src/main.py"] == 1
    assert seen["src/backtest.py"] == 2      # single-strategy AND ensemble
