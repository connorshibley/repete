"""Counterfactual resolution for vetoed / rails-rejected buys.

Answers "what would that blocked trade have done?" so the judge's vetoes can
be scored. Walks daily bars from the decision date with the SAME pessimistic
intrabar leg semantics as backtest.simulate step 2 (stop checked before
take-profit when both touch in one bar) — parity-tested, but deliberately not
imported from backtest.py, which promises isolation from the live loop.

Embargo: resolution_due() blocks resolution until the full swing horizon
(min_holding_days + extra_days) has passed — memory never sees an outcome
before it was observable.
"""
from datetime import datetime, timedelta, timezone


def resolution_due(decision_ts: str, min_holding_days: int, extra_days: int,
                   now: datetime) -> bool:
    decided = datetime.fromisoformat(decision_ts)
    return now - decided >= timedelta(days=min_holding_days + extra_days)


def simulate_veto_counterfactual(bars: list[dict], decision_ts: str,
                                 entry_price: float,
                                 stop: float | None, tp: float | None,
                                 horizon_days: int) -> dict | None:
    """Replay what a blocked buy would have done over the horizon.

    Bars strictly after the decision date are walked; the position "enters"
    at entry_price (the price the judge saw). Pessimistic intrabar ordering:
    stop before take-profit. If neither leg fires, exits at the last close
    inside the horizon. Returns {"pnl_pct", "exit_reason"} or None when no
    bars fall inside the window (resolution retried later).
    """
    decided = datetime.fromisoformat(decision_ts)
    end = decided + timedelta(days=horizon_days)
    window = [b for b in bars
              if decided < datetime.fromisoformat(b["ts"]) <= end]
    if not window:
        return None

    def _pct(exit_price):
        return round((exit_price - entry_price) / entry_price * 100, 3)

    for bar in window:
        if stop is not None and bar["low"] <= stop:
            return {"pnl_pct": _pct(stop), "exit_reason": "stop_loss"}
        if tp is not None and bar["high"] >= tp:
            return {"pnl_pct": _pct(tp), "exit_reason": "take_profit"}
    return {"pnl_pct": _pct(window[-1]["close"]), "exit_reason": "horizon_close"}
