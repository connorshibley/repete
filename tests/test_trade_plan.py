"""The trade-plan narration (src/trade_plan.py).

The value of this module depends entirely on it staying HONEST: every field must
trace to a real signal, order, or config value. The tests that matter most here
are the ones asserting it does NOT invent things — no fabricated price target,
no fabricated hold length for an open-ended trend trade, and no claim of a
protective stop when none was placed.
"""
import trade_plan
from strategies.base import Signal


CFG = {
    "risk": {
        "min_holding_days": 2,
        "brackets": {"trailing_atr_mult": 3.0, "trailing_strategies": ["tsmom"]},
    },
    "strategies": {
        "meanrev": {"exit_sma_period": 5, "max_hold_days": 7},
        "tsmom": {"momentum_bars": 63, "trend_sma_period": 200},
        "ma_crossover": {"fast_period": 10, "slow_period": 30},
    },
}


def sig(strategy="meanrev", reason="RSI2 at 8 — oversold dip in an uptrend"):
    return Signal("AAPL", "buy", reason, {"rsi2": 8.0, "close": 190.0}, strategy)


ORDER = {"stop_price": 180.0}


# ---------------- it reports the real plan ----------------

def test_thesis_is_the_strategy_reason_verbatim():
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull")
    assert p["thesis"] == "RSI2 at 8 — oversold dip in an uptrend"


def test_exit_levels_come_from_the_actual_order():
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull")
    ex = p["exit_plan"]
    assert ex["stop_price"] == 180.0
    assert ex["risk_per_share"] == 10.0
    assert ex["risk_pct_of_entry"] == 5.26
    assert "180" in ex["invalidated_if"]


def test_meanrev_hold_is_bounded_and_cites_real_config():
    h = trade_plan.build(sig("meanrev"), CFG, 190.0, 10, ORDER, "bull")["expected_hold"]
    assert h["hard_cap_days"] == 7
    assert "SMA5" in h["ends_when"] and "7 days" in h["ends_when"]
    assert h["min_holding_days"] == 2
    assert h["earliest_exit"] == "day 3"


def test_tsmom_hold_is_open_ended_and_says_so():
    """The honest answer for a trend trade is 'no fixed horizon'. Inventing
    'about two weeks' would be a forecast the bot cannot make."""
    h = trade_plan.build(sig("tsmom"), CFG, 190.0, 10, ORDER, "bull")["expected_hold"]
    assert h["hard_cap_days"] is None
    assert "no fixed horizon" in h["typical"]
    assert "63-bar momentum" in h["ends_when"]


def test_trailing_stop_is_reported_per_strategy():
    """The chandelier is scoped to tsmom only (§7) — the plan must not claim
    a trailing stop for a strategy that does not get one."""
    t = trade_plan.build(sig("tsmom"), CFG, 190.0, 10, ORDER, "bull")["exit_plan"]
    assert "3.0×ATR" in t["trailing_stop"]
    m = trade_plan.build(sig("meanrev"), CFG, 190.0, 10, ORDER, "bull")["exit_plan"]
    assert m["trailing_stop"] == "none — fixed stop only"


# ---------------- it does NOT invent things ----------------

def test_no_take_profit_is_stated_not_fabricated():
    """No preset target must read as 'no fixed target', never as a number."""
    ex = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull")["exit_plan"]
    assert ex["take_profit_price"] is None
    assert "no fixed target" in ex["target"]


def test_missing_stop_is_declared_not_glossed():
    """An unbracketed position is a material fact — it must be visible."""
    ex = trade_plan.build(sig(), CFG, 190.0, 10, None, "bull")["exit_plan"]
    assert ex["stop_price"] is None
    assert "no protective stop" in ex["invalidated_if"]


def test_unknown_strategy_says_unknown():
    h = trade_plan.build(sig("mystery"), CFG, 190.0, 10, ORDER, "x")["expected_hold"]
    assert h["shape"] == "unknown"
    assert "unknown" in h["ends_when"]


def test_missing_regime_says_unknown_not_a_guess():
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, None)
    assert p["market_context"]["regime"] == "unknown"


def test_indicators_are_carried_through_unmodified():
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull")
    assert p["signals_that_fired"] == {"rsi2": 8.0, "close": 190.0}


# ---------------- selection context + rendering ----------------

def test_why_this_one_only_appears_when_rank_is_known():
    assert "why_this_one" not in trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "b")
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "b", rank=2, candidates=5)
    assert "candidate 2 of 5" in p["why_this_one"]


def test_review_verdict_is_surfaced():
    p = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull",
                         review={"verdict": "downsize", "scale": 0.5,
                                 "reasoning": "elevated vol"})
    assert p["review"]["verdict"] == "downsize"
    assert p["review"]["size_scale"] == 0.5


def test_to_text_is_readable_and_covers_the_key_facts():
    txt = trade_plan.to_text(
        trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull"))
    for expected in ("WHY:", "STRATEGY:", "PLAN:", "EXITS WHEN:", "STOP:",
                     "EARLIEST EXIT:", "REGIME:"):
        assert expected in txt
    assert "180" in txt


def test_to_text_handles_an_empty_plan():
    assert trade_plan.to_text({}) == ""
    assert trade_plan.to_text(None) == ""


def test_ledger_accepts_and_stores_the_plan():
    """The plan has to survive the round trip into the append-only record."""
    import tempfile, os, json
    from ledger import Ledger
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.jsonl"))
        plan = trade_plan.build(sig(), CFG, 190.0, 10, ORDER, "bull")
        tid = led.log_decision("AAPL", "buy", "r", {}, None, executed=True,
                               trade_plan=plan)
        rec = [r for r in led.all_records() if r.get("trade_id") == tid][0]
        assert rec["trade_plan"]["exit_plan"]["stop_price"] == 180.0
        assert rec["trade_plan"]["expected_hold"]["hard_cap_days"] == 7


def test_plan_is_absent_on_records_that_do_not_supply_one():
    """Exits and rejections carry no forward plan — the field must be None,
    not an empty shell that reads like a plan."""
    import tempfile, os
    from ledger import Ledger
    with tempfile.TemporaryDirectory() as d:
        led = Ledger(os.path.join(d, "l.jsonl"))
        tid = led.log_decision("AAPL", "sell", "r", {}, None, executed=True)
        rec = [r for r in led.all_records() if r.get("trade_id") == tid][0]
        assert rec["trade_plan"] is None
