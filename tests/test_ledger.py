from ledger import Ledger


def _ledger(tmp_path):
    return Ledger(str(tmp_path / "memory" / "ledger.jsonl"))


def test_executed_buy_appears_in_open_buys(tmp_path):
    led = _ledger(tmp_path)
    tid = led.log_decision("SPY", "buy", "crossover", {}, None,
                           executed=True, entry_price=100.0, qty=10)
    assert tid in led.open_buys()
    assert led.open_buys()[tid]["entry_price"] == 100.0


def test_close_trade_round_trip(tmp_path):
    led = _ledger(tmp_path)
    tid = led.log_decision("SPY", "buy", "crossover", {}, None,
                           executed=True, entry_price=100.0, qty=10)
    led.close_trade(tid, 110.0, 100.0, 10.0)
    assert led.open_buys() == {}
    closed = led.closed_trades()
    assert len(closed) == 1
    t = closed[0]
    assert t["exit_price"] == 110.0 and t["pnl"] == 100.0 and t["result"] == "win"
    assert t["symbol"] == "SPY"  # merged with the decision record


def test_non_executed_and_holds_excluded_from_open_buys(tmp_path):
    led = _ledger(tmp_path)
    led.log_decision("SPY", "buy", "crossover", {}, None, executed=False,
                     detail="risk rejection")
    led.log_decision("SPY", "hold", "no crossover", {}, None, executed=False)
    assert led.open_buys() == {}


def test_ledger_is_append_only(tmp_path):
    led = _ledger(tmp_path)
    led.log_event("cycle_complete")
    n1 = len(led.all_records())
    led.log_event("cycle_complete")
    records = led.all_records()
    assert len(records) == n1 + 1
    assert all(r["type"] == "event" for r in records)
    assert all("ts" in r for r in records)
