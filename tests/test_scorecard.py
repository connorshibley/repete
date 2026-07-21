"""Monthly scorecard vs S&P: measured every month, promised never."""
import json

import scorecard


def _cc(ts, equity):
    return {"type": "event", "event": "cycle_complete", "ts": ts,
            "detail": json.dumps({"equity": equity})}


def _bar(ts, close):
    return {"ts": ts, "close": close}


RECORDS = [
    _cc("2026-06-01T20:00:00+00:00", 100_000.0),
    _cc("2026-06-15T20:00:00+00:00", 99_000.0),    # intramonth dip -> DD
    _cc("2026-06-30T20:00:00+00:00", 102_000.0),   # June: +2.0%
    _cc("2026-07-01T20:00:00+00:00", 102_000.0),
    _cc("2026-07-15T20:00:00+00:00", 101_000.0),   # July: -0.98%
    {"type": "outcome", "ts": "2026-06-20T20:00:00+00:00", "pnl": 500.0,
     "pnl_pct": 5.0, "result": "win", "trade_id": "x", "exit_price": 1},
]

SPY = [
    _bar("2026-06-01T00:00:00+00:00", 600.0),
    _bar("2026-06-30T00:00:00+00:00", 606.0),      # June: +1.0% -> bot beats
    _bar("2026-07-01T00:00:00+00:00", 606.0),
    _bar("2026-07-15T00:00:00+00:00", 612.0),      # July: +0.99% -> bot trails
]


def test_monthly_scorecard_beat_and_trail():
    card = scorecard.monthly_scorecard(
        RECORDS, SPY, scorecard.realized_pnl_by_month(RECORDS))
    months = {m["month"]: m for m in card["months"]}
    assert months["2026-06"]["beat"] is True
    assert months["2026-06"]["bot_ret_pct"] == 2.0
    assert months["2026-06"]["realized_pnl"] == 500.0
    assert months["2026-06"]["max_dd_pct"] < 0          # dip captured
    assert months["2026-07"]["beat"] is False
    assert card["summary"]["months_beaten"] == 1
    assert card["summary"]["months_total"] == 2


def test_scorecard_no_spy_overlap_is_honest():
    card = scorecard.monthly_scorecard(RECORDS, [])
    assert all(m["spy_ret_pct"] is None and m["beat"] is None
               for m in card["months"])
    assert card["summary"]["months_total"] == 0         # nothing claimed


def test_scorecard_empty_history():
    card = scorecard.monthly_scorecard([], SPY)
    assert card["months"] == []
    assert "no equity history" in scorecard.format_lines(card)[0]


def test_format_lines_states_the_goal_honestly():
    card = scorecard.monthly_scorecard(RECORDS, SPY)
    text = "\n".join(scorecard.format_lines(card))
    assert "measured every month, promised never" in text
    assert "beaten 1/2 months" in text
