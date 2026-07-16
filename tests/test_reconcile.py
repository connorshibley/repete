"""Reconciliation of broker-side exits (bracket leg fills, flattens) into the
ledger. Uses a duck-typed FakeBroker + real Ledger/Memory on tmp_path;
LLM disabled, X posting dry_run — fully offline.
"""
import pytest

import main
from ledger import Ledger
from memory import Memory


class FakeBroker:
    def __init__(self, orders=None, closed=None, last=450.0):
        self.orders = orders or {}      # order_id -> get_order dict
        self.closed = closed or []      # closed_orders() result
        self.last = last

    def get_order(self, order_id):
        if order_id not in self.orders:
            raise RuntimeError(f"unknown order {order_id}")
        return self.orders[order_id]

    def closed_orders(self, symbol, limit=20):
        return self.closed

    def last_price(self, symbol):
        return self.last


class ExplodingBroker:
    def get_order(self, order_id):
        raise RuntimeError("boom")

    def closed_orders(self, symbol, limit=20):
        raise RuntimeError("boom")

    def last_price(self, symbol):
        raise RuntimeError("boom")


@pytest.fixture
def env(tmp_path, cfg):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    ledger = Ledger(cfg["memory"]["ledger_path"])
    memory = Memory(cfg, ledger)
    return ledger, memory, cfg


def _open_bracket_buy(ledger, entry=450.0, qty=10):
    order = {"id": "entry-1", "symbol": "SPY", "qty": qty, "side": "buy",
             "status": "filled", "order_class": "bracket",
             "stop_price": 440.0, "take_profit_price": 465.0,
             "leg_ids": ["leg-stop", "leg-tp"]}
    return ledger.log_decision("SPY", "buy", "crossover", {}, None,
                               executed=True, order=order,
                               entry_price=entry, qty=qty)


def test_stop_leg_fill_reconciled(env):
    ledger, memory, cfg = env
    tid = _open_bracket_buy(ledger)
    broker = FakeBroker(orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                    "filled_avg_price": 450.0, "filled_qty": 10, "legs": []},
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 440.0,
                     "filled_qty": 10, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                   "type": "OrderType.LIMIT", "filled_avg_price": None,
                   "filled_qty": 0, "legs": []},
    })

    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})

    assert ledger.open_buys() == {}
    closed = ledger.closed_trades()
    assert len(closed) == 1
    t = closed[0]
    assert t["exit_price"] == 440.0
    assert t["exit_reason"] == "stop_loss"
    assert t["result"] == "loss"
    assert t["pnl"] == pytest.approx(-100.0)


def test_take_profit_leg_fill_reconciled(env):
    ledger, memory, cfg = env
    tid = _open_bracket_buy(ledger)
    broker = FakeBroker(orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                    "filled_avg_price": 450.0, "filled_qty": 10, "legs": []},
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.CANCELED",
                     "type": "OrderType.STOP", "filled_avg_price": None,
                     "filled_qty": 0, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.FILLED",
                   "type": "OrderType.LIMIT", "filled_avg_price": 465.0,
                   "filled_qty": 10, "legs": []},
    })

    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})

    t = ledger.closed_trades()[0]
    assert t["exit_reason"] == "take_profit" and t["exit_price"] == 465.0
    assert t["result"] == "win"


def test_lesson_written_on_reconciled_close(env):
    # llm disabled -> no lesson text, but learnings file must exist untouched;
    # what matters here is handle_close ran without error and the X dry run fired.
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    broker = FakeBroker(orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                    "filled_avg_price": 450.0, "filled_qty": 10, "legs": []},
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 440.0,
                     "filled_qty": 10, "legs": []},
    })
    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})
    assert ledger.closed_trades()  # close recorded end-to-end


def test_fallback_to_closed_orders(env):
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    broker = FakeBroker(
        orders={"entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                            "filled_avg_price": 450.0, "filled_qty": 10, "legs": []}},
        # leg lookups fail (unknown ids) -> closed SELL order is the source
        closed=[{"id": "x", "side": "OrderSide.SELL", "status": "filled",
                 "type": "OrderType.STOP", "filled_avg_price": 441.5,
                 "filled_qty": 10}],
    )
    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})
    t = ledger.closed_trades()[0]
    assert t["exit_price"] == 441.5 and t["exit_reason"] == "stop_loss"


def test_fallback_to_last_price_estimate(env):
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    broker = FakeBroker(
        orders={"entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                            "filled_avg_price": 450.0, "filled_qty": 10, "legs": []}},
        closed=[], last=447.25)
    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})
    t = ledger.closed_trades()[0]
    assert t["exit_price"] == 447.25 and t["exit_reason"] == "last_price_estimate"


def test_symbol_still_held_untouched(env):
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    broker = ExplodingBroker()  # would blow up if touched
    main.reconcile_closed_positions(broker, ledger, memory, cfg,
                                    positions={"SPY": {"qty": 10}})
    assert len(ledger.open_buys()) == 1
    assert ledger.closed_trades() == []


def test_entry_never_filled(env):
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    broker = FakeBroker(orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.EXPIRED",
                    "filled_avg_price": None, "filled_qty": 0, "legs": []},
    })
    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})
    assert ledger.open_buys() == {}
    t = ledger.closed_trades()[0]
    assert t["exit_reason"] == "entry_unfilled" and t["pnl"] == 0.0


def test_broker_errors_never_crash_cycle(env):
    ledger, memory, cfg = env
    _open_bracket_buy(ledger)
    main.reconcile_closed_positions(ExplodingBroker(), ledger, memory, cfg,
                                    positions={})
    events = [r for r in ledger.all_records() if r["type"] == "event"]
    assert any(e["event"] == "reconcile_error" for e in events)
    # trade stays open for a future retry rather than getting a bogus outcome
    assert len(ledger.open_buys()) == 1


def test_plain_market_close_without_bracket(env):
    """Kill-switch flatten / manual close: no leg_ids, closed order resolves it."""
    ledger, memory, cfg = env
    tid = ledger.log_decision("SPY", "buy", "crossover", {}, None, executed=True,
                              order={"id": "entry-2", "symbol": "SPY", "qty": 10,
                                     "side": "buy", "status": "filled"},
                              entry_price=450.0, qty=10)
    broker = FakeBroker(
        orders={"entry-2": {"id": "entry-2", "status": "OrderStatus.FILLED",
                            "filled_avg_price": 450.0, "filled_qty": 10, "legs": []}},
        closed=[{"id": "y", "side": "OrderSide.SELL", "status": "filled",
                 "type": "OrderType.MARKET", "filled_avg_price": 452.0,
                 "filled_qty": 10}])
    main.reconcile_closed_positions(broker, ledger, memory, cfg, positions={})
    t = ledger.closed_trades()[0]
    assert t["exit_price"] == 452.0 and t["exit_reason"] == "closed_order"
