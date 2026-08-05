"""Counterfactual resolution for vetoed / rails-rejected entries.

Answers "what would that blocked trade have done?" so the judge's vetoes can
be scored. Walks daily bars from the decision date with the SAME pessimistic
intrabar leg semantics as backtest.simulate step 2 (stop checked before
take-profit when both touch in one bar) — parity-tested, but deliberately not
imported from backtest.py, which promises isolation from the live loop.

Direction-aware since 2026-08-05: a short's stop is above entry, its target
below, and its return is the negation of a long's. See
simulate_veto_counterfactual for what went wrong when all three were assumed.

Embargo: resolution_due() blocks resolution until the full swing horizon
(min_holding_days + extra_days) has passed — memory never sees an outcome
before it was observable.
"""
from datetime import datetime, timedelta


def resolution_due(decision_ts: str, min_holding_days: int, extra_days: int,
                   now: datetime) -> bool:
    decided = datetime.fromisoformat(decision_ts)
    return now - decided >= timedelta(days=min_holding_days + extra_days)


def simulate_veto_counterfactual(bars: list[dict], decision_ts: str,
                                 entry_price: float,
                                 stop: float | None, tp: float | None,
                                 horizon_days: int,
                                 direction: str = "buy") -> dict | None:
    """Replay what a blocked ENTRY would have done over the horizon.

    Bars strictly after the decision date are walked; the position "enters"
    at entry_price (the price the judge saw). Pessimistic intrabar ordering:
    stop before take-profit. If neither leg fires, exits at the last close
    inside the horizon. Returns {"pnl_pct", "exit_reason"} or None when no
    bars fall inside the window (resolution retried later).

    `direction` is "buy" or "short" and defaults to "buy", so every caller
    predating the short leg is unaffected. It selects the two things a long's
    geometry got for free and a short inverts:

      * WHICH SIDE OF THE BAR TOUCHES WHICH LEG. A long stops out when the LOW
        falls to a stop BELOW entry, and targets when the HIGH rises to a tp
        ABOVE it. A short is the mirror — stop ABOVE entry (touched by the
        high), target BELOW (touched by the low). Reading a short with the
        long's tests is not merely wrong, it resolves on bar ONE every time:
        the first bar's low is already beneath a stop placed above entry.

      * THE SIGN OF THE RETURN. (exit - entry) / entry is a long's P&L; a short
        profits as price FALLS, so its return is the negation.

    Combine the two and a vetoed short resolved `stop_loss` immediately at a
    POSITIVE return — every short veto scored as a missed WINNER, without
    exception and without a failing test, teaching the judge that its short
    vetoes were always wrong. That is why learn.py's caller filtered shorts out
    entirely until this function could take a direction.

    Pessimistic ordering survives on both sides: the stop is checked before the
    take-profit, so a bar touching both legs is read against the position
    whichever way it points.
    """
    decided = datetime.fromisoformat(decision_ts)
    end = decided + timedelta(days=horizon_days)
    window = [b for b in bars
              if decided < datetime.fromisoformat(b["ts"]) <= end]
    if not window:
        return None

    short = direction == "short"

    def _pct(exit_price):
        move = (exit_price - entry_price) / entry_price * 100
        return round(-move if short else move, 3)

    def _stop_hit(bar):
        return bar["high"] >= stop if short else bar["low"] <= stop

    def _tp_hit(bar):
        return bar["low"] <= tp if short else bar["high"] >= tp

    for bar in window:
        if stop is not None and _stop_hit(bar):
            return {"pnl_pct": _pct(stop), "exit_reason": "stop_loss"}
        if tp is not None and _tp_hit(bar):
            return {"pnl_pct": _pct(tp), "exit_reason": "take_profit"}
    return {"pnl_pct": _pct(window[-1]["close"]), "exit_reason": "horizon_close"}
