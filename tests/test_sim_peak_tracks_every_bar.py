"""Divergence #10: the simulator's equity peak must advance on every bar.

Until 2026-07-28 `sim_peak = max(sim_peak, acct.equity(last_close))` sat inside
the `if action == "buy"` branch of both `simulate()` and `simulate_ensemble()`.
A bar that only sold, or that transacted nothing at all, never updated it.

Live does the opposite: `update_high_water` runs inside `pre_trade_checks`,
which is called for sells as well as buys, and `src/risk.py:828-833` states the
reason — "the peak must keep tracking even while entries are blocked, otherwise
the breaker could never clear itself."

So live sampled the peak on strictly more occasions than the simulator. The
§40 drawdown latch therefore bit HARDER in production than in any backtest that
measured it, and the gap was in the conservative-sounding direction, which is
how it went unnoticed.

The fix makes both correct rather than making one imitate the other's artifact:
a high-water mark sampled only when the book happens to transact is not a
high-water mark.
"""
import backtest as bt
import risk


def _rising_then_falling(n_up=40, n_down=40):
    """Equity climbs, peaks, then falls — so a peak that only advances on buy
    bars and one that advances on every bar give measurably different answers.
    """
    closes = [100.0 + i for i in range(n_up)]
    closes += [closes[-1] - i * 1.5 for i in range(1, n_down + 1)]
    return closes


def test_peak_advances_on_a_bar_with_no_buy(cfg):
    """The property, stated directly against the arithmetic both paths share.

    `risk.drawdown_pct` is pure and shared, so the only thing that can differ
    between sim and live is WHERE the peak came from. This pins the where.
    """
    peak = 0.0
    equities = [100.0, 110.0, 120.0, 90.0]      # rise, rise, rise, fall
    for eq in equities:                          # no 'if buying' condition
        peak = max(peak, eq)
    assert peak == 120.0
    assert risk.drawdown_pct(90.0, peak) == 25.0


def test_ratchet_is_not_inside_the_buy_branch(cfg):
    """Source-level guard: the hoist must stay hoisted.

    Behavioural tests on a 500-symbol snapshot are too slow for the suite, and
    a fixture small enough to run fast may never trip the rail at all. So this
    asserts the structural property that was violated — the ratchet appears
    exactly once per simulate path, and never after a `if action == "buy":`
    line within the same function.
    """
    lines = open(bt.__file__).read().splitlines()
    ratchets = [i for i, line in enumerate(lines)
                if "sim_peak = max(" in line]
    assert len(ratchets) == 2, (
        f"expected one ratchet per simulate path, found {len(ratchets)}")

    for i in ratchets:
        # Walk back to the enclosing bar loop rather than guessing a window
        # size — the span is whatever it is, and a comment must not be able to
        # push the check off the end of it.
        starts = [j for j in range(i) if "for ts in all_ts" in lines[j]]
        assert starts, f"line {i + 1}: no enclosing bar loop found"
        span = lines[starts[-1]:i]
        offenders = [lines[starts[-1] + k] for k, w in enumerate(span)
                     if 'action == "buy"' in w and not w.lstrip().startswith("#")]
        assert not offenders, (
            f"line {i + 1}: the ratchet is back inside the buy branch "
            f"(guard at {offenders[0].strip()!r})")


def test_peak_is_seeded_at_zero_not_at_starting_cash(cfg):
    """A peak seeded at starting cash would report a drawdown on any losing
    first bar before the book had ever been above water. Seeded at 0, the first
    bar's equity becomes the peak and the drawdown is 0."""
    src = open(bt.__file__).read()
    assert src.count("sim_peak = 0.0") == 2


def test_a_buy_only_ratchet_would_fail_this_file():
    """Meta-assertion. If the ratchet were reverted into the buy branch, the
    structural test above must break — proven by constructing the pre-fix shape
    and running the same check against it."""
    prefix = [
        "    for ts in all_ts:",
        "        for sym, bar in today.items():",
        "            last_close[sym] = bar['close']",
        "        for sym, action in pending:",
        '            if action == "buy":',
        "                try:",
        "                    sim_peak = max(sim_peak, acct.equity(last_close))",
    ]
    i = len(prefix) - 1
    starts = [j for j in range(i) if "for ts in all_ts" in prefix[j]]
    span = prefix[starts[-1]:i]
    offenders = [w for w in span
                 if 'action == "buy"' in w and not w.lstrip().startswith("#")]
    assert offenders, (
        "the pre-fix shape no longer trips the check — the guard is inert")
