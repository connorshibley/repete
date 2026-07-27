"""A win against a rising market is not automatically a good decision.

Why this file exists
--------------------
`ledger.close_trade()` recorded `pnl`, `pnl_pct` and `result` — all absolute.
A +3% trade in a +5% week was stored as `"result": "win"`, that record fed
`lessons.py`, and the judge learned that riding the market was skill.

The repo already measures itself against a benchmark everywhere else: buy-and-
hold in `backtest.py`, the monthly SPY scorecard in `scorecard.py`, the
exposure-matched clause in the enablement gate. Per trade it never did.

The sharpest property below is `test_missing_benchmark_records_nothing_rather
_than_zero`. "I could not compute the benchmark" and "the benchmark was flat"
are different facts, and collapsing them writes the flattering one: a trade
with unknown alpha would read as having exactly matched the market.
"""
import scorecard


def _bars(pairs):
    return [{"ts": ts, "close": c} for ts, c in pairs]


SPY = _bars([
    ("2026-07-01T20:00:00Z", 100.0),
    ("2026-07-02T20:00:00Z", 102.0),
    ("2026-07-03T20:00:00Z", 105.0),
    ("2026-07-06T20:00:00Z", 110.0),
])


# ---- the benchmark window ----

def test_benchmark_return_is_measured_over_the_holding_window():
    r = scorecard.benchmark_return_pct(SPY, "2026-07-01T20:00:00Z",
                                       "2026-07-03T20:00:00Z")
    assert round(r, 4) == 5.0        # 100 -> 105


def test_a_later_exit_uses_a_later_bar():
    """Boundary pair: same entry, one bar further out."""
    early = scorecard.benchmark_return_pct(SPY, "2026-07-01T20:00:00Z",
                                           "2026-07-03T20:00:00Z")
    late = scorecard.benchmark_return_pct(SPY, "2026-07-01T20:00:00Z",
                                          "2026-07-06T20:00:00Z")
    assert round(early, 4) == 5.0 and round(late, 4) == 10.0


def test_the_benchmark_never_sees_a_bar_the_trade_could_not_have():
    """Strictly on-or-before. An exit between bars must use the PRIOR close,
    never the next one — that would be lookahead in the one number whose job is
    to make the bot look worse."""
    r = scorecard.benchmark_return_pct(SPY, "2026-07-01T20:00:00Z",
                                       "2026-07-04T12:00:00Z")
    assert round(r, 4) == 5.0        # the 07-03 close, not the 07-06 one


def test_missing_benchmark_records_nothing_rather_than_zero():
    """The one that matters. None, never 0.0."""
    assert scorecard.benchmark_return_pct([], "a", "b") is None
    assert scorecard.benchmark_return_pct(None, "a", "b") is None
    assert scorecard.benchmark_return_pct(SPY, "", "2026-07-03T20:00:00Z") is None
    # window entirely before the bars: no starting close to measure from
    assert scorecard.benchmark_return_pct(
        SPY, "2020-01-01T00:00:00Z", "2020-02-01T00:00:00Z") is None


def test_an_inverted_window_is_refused():
    assert scorecard.benchmark_return_pct(
        SPY, "2026-07-06T20:00:00Z", "2026-07-01T20:00:00Z") is None


# ---- what reaches the ledger ----

def test_alpha_is_recorded_alongside_the_absolute_result(tmp_path):
    from ledger import Ledger
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.close_trade("t1", 103.0, 30.0, 3.0, exit_reason="strategy_sell",
                    benchmark_pnl_pct=5.0)
    out = [r for r in led.all_records() if r.get("type") == "outcome"][0]
    # absolute meaning is untouched — 564 existing records still read the same
    assert out["result"] == "win" and out["pnl_pct"] == 3.0
    # ...and the honest comparison sits next to it
    assert out["benchmark_pnl_pct"] == 5.0
    assert out["alpha_pct"] == -2.0


def test_a_win_can_now_be_seen_to_have_lost_to_the_market(tmp_path):
    """The exact case the absolute record hid."""
    from ledger import Ledger
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.close_trade("t1", 103.0, 30.0, 3.0, benchmark_pnl_pct=5.0)
    out = [r for r in led.all_records() if r.get("type") == "outcome"][0]
    assert out["result"] == "win"
    assert out["alpha_pct"] < 0


def test_a_loss_can_now_be_seen_to_have_beaten_the_market(tmp_path):
    """And the mirror image, which the absolute record punished."""
    from ledger import Ledger
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.close_trade("t1", 98.0, -20.0, -2.0, benchmark_pnl_pct=-6.0)
    out = [r for r in led.all_records() if r.get("type") == "outcome"][0]
    assert out["result"] == "loss"
    assert out["alpha_pct"] == 4.0


def test_no_benchmark_means_no_alpha_fields_at_all(tmp_path):
    """Absent, not zero — in the record as well as in the computation."""
    from ledger import Ledger
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.close_trade("t1", 103.0, 30.0, 3.0)
    out = [r for r in led.all_records() if r.get("type") == "outcome"][0]
    assert "alpha_pct" not in out
    assert "benchmark_pnl_pct" not in out


def test_close_trade_still_works_without_the_new_argument(tmp_path):
    """Every existing caller and every one of the 564 historical records must
    keep their meaning; the parameter is additive and optional."""
    from ledger import Ledger
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.close_trade("t1", 100.0, 0.0, 0.0, exit_reason="entry_unfilled")
    out = [r for r in led.all_records() if r.get("type") == "outcome"][0]
    assert out["exit_reason"] == "entry_unfilled"
    assert out["result"] == "loss"      # 0 pnl is not a win, unchanged
