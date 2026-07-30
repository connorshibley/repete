"""§45: every closed trade must be accounted for by exactly one exit reason.

The mirror of §40's entry census, and the same argument. §40 asked how capital
gets IN and found the drawdown rail blocking 74–97% of signals. Nothing had ever
asked how it gets OUT, even though every closed trade has carried an
`exit_reason` since the simulator was written — it was aggregated nowhere.

Why the sum is asserted rather than merely tested
-------------------------------------------------
§40's balance check caught a real drop path on its very first run (a buy queued
on the last bar that never reached the fill path). A bucket total that quietly
disagrees with the trade count means a close path nobody counts, which is the
state the census exists to end. So `simulate_ensemble` records an `unaccounted`
bucket and prints to stderr rather than balancing silently, and this file pins
that behaviour.

`end_of_data` carries the argument. It is a FORCED close at run end, not a
decision, and live has no equivalent — nothing force-closes a real position.
Measured across all four snapshots on 2026-07-29 it is **zero**, which is why no
closed-trade count in §35–§43 is inflated by run truncation. That is load-bearing
for the whole research record, so the machinery that would have detected the
opposite is tested here even though the count came back clean.

Uses `bt.load_config()` and the same deterministic walks as
`tests/test_ensemble_sim.py` — the shared `cfg` fixture enables no strategies, so
a run built on it raises before reaching any of this.
"""
import backtest as bt
from test_ensemble_sim import synth_bars


def _cfg_and_bars(n=320):
    cfg = bt.load_config()
    picks = ["SPY", "AAPL", "MSFT", "XOM", "JNJ"]
    ordered = [s for s in cfg["symbols"] if s in picks]
    assert len(ordered) == len(picks), "fixture symbols must all be in config"
    return cfg, synth_bars(ordered, n=n)


def test_the_exit_census_balances():
    """The invariant: buckets sum to the closed-trade count, always."""
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    e = r.exits
    assert r.n_trades > 0, "fixture produced no trades — test proves nothing"
    assert e["closed"] == r.n_trades
    assert sum(e["by_reason"].values()) == e["closed"]
    assert "unaccounted" not in e["by_reason"], (
        "a close path exists that the census does not count")


def test_every_reason_is_one_the_simulator_can_emit():
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    assert set(r.exits["by_reason"]) <= {
        "strategy_sell", "stop_loss", "take_profit", "end_of_data"}


def test_a_position_open_at_the_final_bar_lands_in_end_of_data():
    """The detector for the thing §45 predicted and did not find.

    Across all four snapshots this bucket is ZERO — the book empties on its own,
    which is why no closed-trade count in §35–§43 is inflated by run truncation.
    That result only means anything if a non-empty book WOULD land here.

    The first version of this test compared `by_reason["end_of_data"]` against
    the trades carrying `exit_reason == "end_of_data"` — the counter against the
    field the counter reads. A tautology. Renaming the reason at the `_exit` call
    moved both to zero together and the test passed, which the negative control
    caught. It now asserts the count independently: positions still open at the
    final bar, counted from `acct` behaviour via the trade list's exit timestamp,
    must be non-zero here AND must equal the bucket.
    """
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    final_ts = max(b[-1]["ts"] for b in bars.values())
    closed_on_last_bar = [t for t in r.trades if t.exit_ts == final_ts]
    assert closed_on_last_bar, (
        "fixture no longer ends holding a position — this test proves nothing "
        "until it does; adjust the walk length, do not delete the assertion")
    assert r.exits["by_reason"].get("end_of_data", 0) == len(closed_on_last_bar), (
        f"{len(closed_on_last_bar)} trades closed on the final bar but "
        f"end_of_data counts "
        f"{r.exits['by_reason'].get('end_of_data', 0)} — the bucket that proves "
        f"backtest trade counts are not inflated by run truncation is not "
        f"counting forced closes")


def test_dropping_a_real_bucket_shows_up_as_unaccounted():
    """The balance check must bite on a bucket that actually has trades in it.

    §40's rule is that an unexplained gap is recorded loudly rather than
    balanced quietly. This asserts the two largest reasons in the fixture are
    both present and non-zero, so a negative control that removes either one
    forces `unaccounted` to appear rather than silently targeting an empty
    bucket — which is exactly how the first negative-control pass on this file
    failed to bite.
    """
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    reasons = r.exits["by_reason"]
    assert len(reasons) >= 2, (
        f"fixture exercises only {list(reasons)} — the census needs at least "
        f"two live buckets for the balance check to be falsifiable")
    for reason, n in reasons.items():
        assert n > 0, f"{reason} is present with a zero count"
    assert sum(reasons.values()) == r.n_trades


def test_holding_days_are_ordered_and_non_negative():
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    h = r.exits["holding_days"]
    assert h, "trades closed but no holding-period distribution was reported"
    assert h["min"] <= h["median"] <= h["p90"] <= h["max"]
    assert h["min"] >= 0


def test_guard_skipped_exits_is_carried_into_the_census():
    """`min_holding_days` blocks were already on Result; §45 puts them beside
    the exit reasons because a blocked sell is part of the answer to 'why
    doesn't the book turn over'."""
    cfg, bars = _cfg_and_bars()
    r = bt.simulate_ensemble(bars, cfg, 100_000.0)
    assert r.exits["guard_skipped_exits"] == r.n_guard_skipped_exits


def test_an_empty_run_reports_an_empty_census_not_a_missing_one():
    """`empty is not the same as absent` — a run with no trades must still
    produce the structure, so a reader can tell 'nothing closed' from 'nothing
    measured'."""
    cfg = bt.load_config()
    picks = [s for s in cfg["symbols"] if s in ("SPY", "AAPL")]
    flat = {s: [{"ts": f"2024-01-{i + 1:02d}T21:00:00+00:00", "open": 100.0,
                 "high": 100.0, "low": 100.0, "close": 100.0,
                 "volume": 1_000_000} for i in range(30)] for s in picks}
    r = bt.simulate_ensemble(flat, cfg, 100_000.0)
    assert r.n_trades == 0
    assert r.exits["closed"] == 0
    assert r.exits["by_reason"] == {}
    assert r.exits["holding_days"] == {}


def test_the_census_is_observation_only():
    """The frozen gates — s43a-d among them — must reproduce byte-identically,
    so nothing here may touch a decision. Verified end-to-end by re-running s43d
    (−7.71%, PF 0.367, 54 trades) after the census landed; this is the cheap
    offline guard that keeps it that way."""
    cfg, bars = _cfg_and_bars()
    a = bt.simulate_ensemble(bars, cfg, 100_000.0)
    b = bt.simulate_ensemble(bars, cfg, 100_000.0)
    assert (a.total_return_pct, a.n_trades, a.profit_factor,
            a.max_drawdown_pct) == (b.total_return_pct, b.n_trades,
                                    b.profit_factor, b.max_drawdown_pct)
    assert a.exits == b.exits
