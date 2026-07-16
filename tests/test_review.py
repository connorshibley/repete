from datetime import datetime, timedelta, timezone

import pytest

import review


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _decision(tid, executed=True, ts_days_ago=10, veto=False, rejected=False):
    return {"type": "decision", "trade_id": tid, "symbol": "SPY", "action": "buy",
            "executed": executed and not rejected,
            "detail": "risk rejection: cap" if rejected else "",
            "llm_review": {"verdict": "veto"} if veto else None,
            "ts": (NOW - timedelta(days=ts_days_ago)).isoformat()}


def _outcome(tid, pnl, reason="strategy_sell"):
    return {"type": "outcome", "trade_id": tid, "pnl": pnl, "exit_price": 1.0,
            "pnl_pct": 1.0, "result": "win" if pnl > 0 else "loss",
            "exit_reason": reason,
            "ts": NOW.isoformat()}


def test_report_aggregates():
    records = [
        _decision("a", ts_days_ago=70), _outcome("a", 100.0),
        _decision("b"), _outcome("b", -50.0, reason="stop_loss"),
        _decision("c"),                       # still open
        _decision("d", veto=True, executed=False),
        _decision("e", rejected=True),
    ]
    r = review.build_report(records, [], NOW)
    assert r["history_days"] == 70
    assert r["n_closed"] == 2 and r["n_open"] == 1
    assert r["win_rate"] == 0.5
    assert r["profit_factor"] == 2.0
    assert r["realized_pnl"] == 50.0
    assert r["n_vetoes"] == 1 and r["n_risk_rejections"] == 1
    assert r["exit_reasons"] == {"strategy_sell": 1, "stop_loss": 1}


def test_report_empty_ledger():
    r = review.build_report([], [], NOW)
    assert r["n_closed"] == 0 and r["win_rate"] is None
    assert r["profit_factor"] is None and r["history_days"] == 0


def test_profit_factor_inf_when_no_losses():
    records = [_decision("a"), _outcome("a", 10.0)]
    assert review.build_report(records, [], NOW)["profit_factor"] == float("inf")


def test_last_lesson_date_parsed():
    lines = ["# Learnings\n", "- **2026-06-01** (trade x): n=1 observation\n",
             "- **2026-07-01** (trade y): possible pattern\n"]
    r = review.build_report([], lines, NOW)
    assert r["last_lesson_date"] == "2026-07-01"


def test_lesson_book_summary():
    ts = "2026-07-01T00:00:00+00:00"
    states = {
        "a": {"status": "active", "created_ts": "2026-06-01T00:00:00+00:00",
              "hypothesis": "old active", "supports": ["1"], "contradicts": []},
        "b": {"status": "active", "created_ts": ts, "hypothesis": "new active",
              "supports": [], "contradicts": []},
        "c": {"status": "refuted", "created_ts": ts, "hypothesis": "dead",
              "supports": [], "contradicts": ["1"]},
    }
    book = review.lesson_book_summary(states)
    assert book["counts"]["active"] == 2 and book["counts"]["refuted"] == 1
    assert book["oldest_active"]["hypothesis"] == "old active"


def test_lesson_book_summary_empty():
    book = review.lesson_book_summary({})
    assert book["oldest_active"] is None and book["counts"]["active"] == 0


def test_per_strategy_breakdown_with_legacy_records():
    closed = [
        {"strategy": "tsmom", "pnl": 100.0, "result": "win"},
        {"strategy": "tsmom", "pnl": -40.0, "result": "loss"},
        {"strategy": None, "pnl": 30.0, "result": "win"},   # legacy record
        {"pnl": -10.0, "result": "loss"},                   # pre-tag record
    ]
    out = review.per_strategy_breakdown(closed)
    assert out["tsmom"]["n_closed"] == 2
    assert out["tsmom"]["win_rate"] == 0.5
    assert out["tsmom"]["profit_factor"] == 2.5
    assert out["ma_crossover"]["n_closed"] == 2  # legacy default owner
    assert out["ma_crossover"]["realized_pnl"] == 20.0
