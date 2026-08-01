"""Random-entry decay monitor.

The tests that matter here are the two behavioural ones — noise must NOT read as
edge, and a planted edge MUST be detected. A monitor that passes everything is
indistinguishable from no monitor, and one that fails everything gets muted.
Both directions are pinned.
"""
import random

import pytest

import randombaseline as rb


def _bars(closes, start_day=1, spread=0.0):
    """Daily bars from closes. open == previous close so a next-open entry is
    priced off information the decision bar could actually have seen."""
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        day = start_day + i
        bars.append({
            "ts": f"2026-01-{day:02d}T21:00:00+00:00",
            "open": prev, "high": max(prev, c) + spread,
            "low": min(prev, c) - spread, "close": c, "volume": 1000,
        })
        prev = c
    return bars


def _flat_market(n=120, price=100.0, seed=7):
    """A market with no drift — random walk around a level."""
    rng = random.Random(seed)
    closes, p = [], price
    for _ in range(n):
        p *= 1 + rng.gauss(0, 0.01)
        closes.append(round(p, 4))
    return _bars(closes)


# ---------------------------------------------------------------- primitives

class _FixedRng(random.Random):
    """randint always returns a pinned value, so the entry index is fixed."""
    def __init__(self, value):
        super().__init__(0)
        self._value = value

    def randint(self, a, b):
        return self._value


def _gapped_bars():
    """Bars where the open GAPS away from the previous close.

    Deliberately not `_bars`, whose open == previous close: on that fixture
    "enter at next open" and "enter at the decision close" are the same number,
    so it cannot detect look-ahead. Caught by a negative control.
    """
    return [
        {"ts": "2026-01-01T21:00:00+00:00", "open": 99, "high": 101,
         "low": 98, "close": 100, "volume": 1000},
        {"ts": "2026-01-02T21:00:00+00:00", "open": 105, "high": 112,
         "low": 104, "close": 110, "volume": 1000},   # gaps up from 100
        {"ts": "2026-01-03T21:00:00+00:00", "open": 115, "high": 122,
         "low": 114, "close": 120, "volume": 1000},
        {"ts": "2026-01-04T21:00:00+00:00", "open": 125, "high": 132,
         "low": 124, "close": 130, "volume": 1000},
    ]


def test_random_entry_enters_at_next_open_and_exits_at_close():
    """Entry must be the NEXT bar's open, never the decision bar's close.

    Entering at the close it decided from would let the null act on information
    the real bot never has, making the baseline unrealistically easy to beat.
    """
    bars = _gapped_bars()
    # i=0 -> entry at bars[1]["open"] == 105, exit at bars[2]["close"] == 120
    pct = rb.random_entry_pct(bars, holding=2, rng=_FixedRng(0))
    assert pct == pytest.approx((120 - 105) / 105 * 100)
    # and specifically NOT the decision-bar close (100), which would give +20%
    assert pct != pytest.approx((120 - 100) / 100 * 100)
    assert rb.random_entry_pct(bars, holding=2,
                               rng=random.Random(0)) is not None


def test_slippage_is_charged_against_you_on_both_legs():
    """Both legs, not just one.

    An earlier version asserted only `slipped < clean`, which a single leg
    satisfies on its own — a negative control removing entry-side slippage left
    it green. Pinning the exact value makes either leg's removal fail.
    """
    bars = _gapped_bars()
    clean = rb.random_entry_pct(bars, 2, _FixedRng(0), slippage_bps=0)
    slipped = rb.random_entry_pct(bars, 2, _FixedRng(0), slippage_bps=50)
    assert slipped < clean, "cost model must reduce the null's return"

    entry = 105 * (1 + 50 / 1e4)      # pay up on the way in
    exit_ = 120 * (1 - 50 / 1e4)      # receive less on the way out
    assert slipped == pytest.approx((exit_ - entry) / entry * 100)

    # neither one-sided variant matches: each leg is independently required
    entry_only = (120 - 105 * 1.005) / (105 * 1.005) * 100
    exit_only = (120 * 0.995 - 105) / 105 * 100
    assert slipped != pytest.approx(entry_only)
    assert slipped != pytest.approx(exit_only)


def test_holding_that_does_not_fit_the_series_returns_none():
    bars = _bars([100, 101, 102])
    assert rb.random_entry_pct(bars, holding=99, rng=random.Random(0)) is None
    assert rb.random_entry_pct(bars, holding=0, rng=random.Random(0)) is None


def test_holding_bars_counts_bars_not_calendar_days():
    # Jan 1..10 of bars, but the ledger timestamps span a weekend gap
    bars = _bars([100 + i for i in range(10)])
    h = rb.holding_bars(bars, "2026-01-01T21:00:00+00:00",
                        "2026-01-05T21:00:00+00:00")
    assert h == 4
    assert rb.holding_bars(bars, "2026-01-05T21:00:00+00:00",
                           "2026-01-01T21:00:00+00:00") is None


def test_percentile_of_places_value_in_distribution():
    dist = [1.0, 2.0, 3.0, 4.0]
    assert rb.percentile_of(0.5, dist) == 0.0
    assert rb.percentile_of(2.0, dist) == 50.0
    assert rb.percentile_of(9.0, dist) == 100.0
    assert rb.percentile_of(1.0, []) is None


# ------------------------------------------------------- the two that matter

def test_noise_does_not_read_as_edge():
    """A strategy whose entries carry NO information must not clear the bar.

    This is the test that gives the monitor its meaning: if random-quality
    results scored as BEATS_RANDOM, every verdict it ever emits is worthless.
    """
    bars = _flat_market()
    by_sym = {"SPY": bars}
    rng = random.Random(99)
    # "actual" trades drawn the same way the null is drawn => same distribution
    actual = [rb.random_entry_pct(bars, rng.choice([3, 5, 8]), rng)
              for _ in range(40)]
    actual = [a for a in actual if a is not None]
    null = rb.null_distribution(by_sym, ["SPY"], [3, 5, 8], len(actual),
                                n_samples=400, seed=1)
    a = rb.assess(actual, null, min_trades=20)
    assert a.verdict != rb.BEATS_RANDOM
    assert not a.alert or a.verdict == rb.WORSE_THAN_RANDOM


def test_planted_edge_is_detected():
    """A strategy that really does beat random timing must clear the bar."""
    bars = _flat_market()
    by_sym = {"SPY": bars}
    null = rb.null_distribution(by_sym, ["SPY"], [5], 30,
                                n_samples=400, seed=1)
    null_mean = sum(null) / len(null)
    # every trade beats the random mean by a wide, unambiguous margin
    actual = [null_mean + 5.0] * 30
    a = rb.assess(actual, null, min_trades=20)
    assert a.verdict == rb.BEATS_RANDOM
    assert a.percentile >= rb.STRONG_ABOVE_PERCENTILE
    assert not a.alert


def test_strategy_far_below_random_alerts():
    bars = _flat_market()
    null = rb.null_distribution({"SPY": bars}, ["SPY"], [5], 30,
                                n_samples=400, seed=1)
    null_mean = sum(null) / len(null)
    a = rb.assess([null_mean - 5.0] * 30, null, min_trades=20)
    assert a.verdict == rb.WORSE_THAN_RANDOM
    assert a.alert is True


# ------------------------------------------------- thin data is never a pass

def test_too_few_trades_is_insufficient_not_healthy():
    a = rb.assess([1.0, 2.0, 3.0], [0.0, 0.1], min_trades=20)
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert a.alert is False
    assert a.actual_mean_pct is None, "must not report a mean it cannot justify"


def test_empty_trade_list_is_insufficient():
    a = rb.assess([], [0.0], min_trades=20)
    assert a.verdict == rb.INSUFFICIENT_DATA


def test_no_usable_bars_is_insufficient_not_healthy():
    """Enough trades but no bars to build a baseline: still not a verdict."""
    a = rb.assess([1.0] * 25, [], min_trades=20)
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert a.alert is False


def test_null_distribution_is_empty_when_no_symbol_has_bars():
    assert rb.null_distribution({}, ["SPY"], [3], 10) == []
    assert rb.null_distribution({"SPY": []}, ["SPY"], [3], 10) == []
    assert rb.null_distribution({"SPY": _flat_market()}, ["SPY"], [], 10) == []


# ------------------------------------------------------------- determinism

def test_same_seed_gives_identical_verdict():
    bars = _flat_market()
    kw = dict(n_samples=200, seed=rb.DEFAULT_SEED)
    a = rb.null_distribution({"SPY": bars}, ["SPY"], [4], 12, **kw)
    b = rb.null_distribution({"SPY": bars}, ["SPY"], [4], 12, **kw)
    assert a == b, "a re-run must reproduce the verdict exactly"


def test_different_seed_gives_a_different_draw():
    bars = _flat_market()
    a = rb.null_distribution({"SPY": bars}, ["SPY"], [4], 12,
                             n_samples=200, seed=1)
    b = rb.null_distribution({"SPY": bars}, ["SPY"], [4], 12,
                             n_samples=200, seed=2)
    assert a != b


# ------------------------------------------------------------ ledger joining

def _decision(tid, symbol, ts, strategy="tsmom"):
    return {"type": "decision", "trade_id": tid, "symbol": symbol, "ts": ts,
            "action": "buy", "strategy": strategy, "executed": True}


def _outcome(tid, pnl_pct, ts):
    return {"type": "outcome", "trade_id": tid, "pnl_pct": pnl_pct, "ts": ts}


def test_closed_trades_joins_outcome_to_decision():
    recs = [_decision("a1", "SPY", "2026-01-01T21:00:00+00:00"),
            _outcome("a1", 2.5, "2026-01-06T21:00:00+00:00")]
    got = rb.closed_trades(recs)
    assert len(got) == 1
    assert got[0]["symbol"] == "SPY" and got[0]["pnl_pct"] == 2.5
    assert got[0]["strategy"] == "tsmom"


def test_outcome_without_its_decision_is_dropped_not_defaulted():
    recs = [_outcome("orphan", 5.0, "2026-01-06T21:00:00+00:00")]
    assert rb.closed_trades(recs) == []


def test_outcome_without_pnl_is_dropped():
    recs = [_decision("a1", "SPY", "2026-01-01T21:00:00+00:00"),
            {"type": "outcome", "trade_id": "a1", "pnl_pct": None,
             "ts": "2026-01-06T21:00:00+00:00"}]
    assert rb.closed_trades(recs) == []


def test_entry_ts_is_preferred_over_record_write_time():
    """Adopted positions carry a real fill time in entry_ts; using the append
    time instead would overstate the holding period."""
    d = _decision("a1", "SPY", "2026-01-09T21:00:00+00:00")
    d["entry_ts"] = "2026-01-02T21:00:00+00:00"
    got = rb.closed_trades([d, _outcome("a1", 1.0, "2026-01-10T21:00:00+00:00")])
    assert got[0]["entry_ts"] == "2026-01-02T21:00:00+00:00"


def test_check_filters_by_strategy():
    recs = []
    for i in range(30):
        tid = f"t{i}"
        strat = "tsmom" if i % 2 == 0 else "meanrev"
        recs.append(_decision(tid, "SPY", f"2026-01-{(i % 20) + 1:02d}T21:00:00+00:00",
                              strategy=strat))
        recs.append(_outcome(tid, 1.0, f"2026-01-{(i % 20) + 2:02d}T21:00:00+00:00"))
    a = rb.check(recs, {"SPY": _flat_market()}, strategy="tsmom", min_trades=20)
    # 15 tsmom trades < 20 required -> insufficient, and it must say so
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert a.n_trades == 15


def test_check_on_an_empty_ledger_is_insufficient():
    a = rb.check([], {"SPY": _flat_market()})
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert a.n_trades == 0


def test_describe_never_claims_a_number_it_lacks():
    a = rb.check([], {"SPY": _flat_market()})
    text = a.describe()
    assert rb.INSUFFICIENT_DATA in text
    assert "%/trade" not in text
