"""Adoption of untracked broker positions — the complement to reconcile.

A broker position with NO open ledger buy is a 'ghost': an entry order that
filled but whose ledger write never landed (a crash/kill between broker.submit
and ledger.log_decision in _process_signal). adopt_untracked_positions must
bring it back under management so its P&L and the track record don't silently
omit a real position. Offline: real Ledger on tmp_path, no broker calls needed.
"""
import pytest

import main
import strategies
from ledger import Ledger


@pytest.fixture
def env(tmp_path, cfg):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    return Ledger(cfg["memory"]["ledger_path"]), cfg


def test_ghost_position_is_adopted(env):
    ledger, cfg = env
    # Broker holds SPY; the ledger has no record of it (the ghost).
    positions = {"SPY": {"qty": 10, "avg_entry": 450.0,
                         "market_value": 4500.0, "unrealized_pl": 0.0}}
    main.adopt_untracked_positions(broker=None, ledger=ledger, cfg=cfg,
                                   positions=positions)
    opens = ledger.open_buys()
    assert len(opens) == 1
    rec = next(iter(opens.values()))
    assert rec["symbol"] == "SPY"
    assert rec["qty"] == 10
    assert rec["entry_price"] == 450.0
    # Owner must be a real registry strategy so the exit path routes it.
    assert rec["strategy"] in strategies.REGISTRY


def test_tracked_position_not_double_adopted(env):
    ledger, cfg = env
    # A normal, already-ledgered open buy.
    ledger.log_decision("SPY", "buy", "crossover", {}, None, executed=True,
                        order={"id": "entry-1"}, entry_price=450.0, qty=10,
                        strategy="ma_crossover")
    positions = {"SPY": {"qty": 10, "avg_entry": 450.0, "market_value": 4500.0}}
    main.adopt_untracked_positions(broker=None, ledger=ledger, cfg=cfg,
                                   positions=positions)
    # Still exactly one open buy — no duplicate adoption record.
    assert len(ledger.open_buys()) == 1
    decisions = [r for r in ledger.all_records()
                 if r["type"] == "decision" and r["action"] == "buy"]
    assert len(decisions) == 1


def test_no_positions_is_noop(env):
    ledger, cfg = env
    main.adopt_untracked_positions(broker=None, ledger=ledger, cfg=cfg,
                                   positions={})
    assert ledger.open_buys() == {}


def test_adopted_position_excluded_from_fill_quality(env):
    # An adopted record has no order id, so the slippage pass must skip it
    # (it never saw a signal price / broker fill to compare).
    ledger, cfg = env
    positions = {"SPY": {"qty": 10, "avg_entry": 450.0, "market_value": 4500.0}}
    main.adopt_untracked_positions(broker=None, ledger=ledger, cfg=cfg,
                                   positions=positions)
    rec = next(iter(ledger.open_buys().values()))
    assert (rec.get("order") or {}).get("id") is None
