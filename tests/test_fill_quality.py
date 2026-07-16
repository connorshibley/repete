"""Fill-quality tracking: measured slippage (signal vs actual fill) is
appended exactly once per trade and surfaces in the weekly review."""
import pytest

import main
import review
from ledger import Ledger


class FillBroker:
    def __init__(self, orders):
        self._orders = orders
        self.lookups = 0

    def get_order(self, order_id):
        self.lookups += 1
        return self._orders[order_id]


def _executed_buy(led, symbol="SPY", price=100.0, order_id="o-1"):
    return led.log_decision(symbol, "buy", "test", {}, None, executed=True,
                            order={"id": order_id}, entry_price=price, qty=10)


def test_fill_quality_recorded_for_buy(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = _executed_buy(led, price=100.0)
    broker = FillBroker({"o-1": {"filled_avg_price": 100.05}})

    main.record_fill_quality(broker, led)

    fq = [r for r in led.all_records() if r["type"] == "fill_quality"]
    assert len(fq) == 1
    assert fq[0]["trade_id"] == tid
    assert fq[0]["slippage_bps"] == pytest.approx(5.0)  # paid 5 bps more


def test_sell_slippage_sign_flipped(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("SPY", "sell", "test", {}, None, executed=True,
                     order={"id": "o-2"}, entry_price=100.0, qty=10)
    broker = FillBroker({"o-2": {"filled_avg_price": 99.95}})

    main.record_fill_quality(broker, led)

    fq = [r for r in led.all_records() if r["type"] == "fill_quality"]
    # received 5 bps less than the signal price -> positive (worse)
    assert fq[0]["slippage_bps"] == pytest.approx(5.0)


def test_idempotent_across_cycles(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _executed_buy(led)
    broker = FillBroker({"o-1": {"filled_avg_price": 100.0}})

    main.record_fill_quality(broker, led)
    main.record_fill_quality(broker, led)  # next cycle: already recorded

    fq = [r for r in led.all_records() if r["type"] == "fill_quality"]
    assert len(fq) == 1
    assert broker.lookups == 1


def test_unfilled_order_retried_not_recorded(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _executed_buy(led)
    broker = FillBroker({"o-1": {"filled_avg_price": None}})

    main.record_fill_quality(broker, led)
    assert [r for r in led.all_records() if r["type"] == "fill_quality"] == []

    broker._orders["o-1"]["filled_avg_price"] = 100.1  # fills later
    main.record_fill_quality(broker, led)
    fq = [r for r in led.all_records() if r["type"] == "fill_quality"]
    assert len(fq) == 1


def test_broker_error_degrades_gracefully(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    _executed_buy(led)
    main.record_fill_quality(FillBroker({}), led)  # KeyError inside
    assert [r for r in led.all_records() if r["type"] == "fill_quality"] == []


def test_review_reports_slippage(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    for i, bps_fill in enumerate([100.05, 100.10, 100.15]):
        led.log_decision("SPY", "buy", "t", {}, None, executed=True,
                         order={"id": f"o-{i}"}, entry_price=100.0, qty=1)
    for i, fill in enumerate([100.05, 100.10, 100.15]):
        recs = [r for r in led.all_records() if r["type"] == "decision"]
        led.log_fill_quality(recs[i]["trade_id"], "SPY", "buy", 100.0, fill,
                             (fill - 100.0) / 100.0 * 1e4)

    from datetime import datetime, timezone
    r = review.build_report(led.all_records(), [], datetime.now(timezone.utc))
    assert r["slippage"]["n_fills"] == 3
    assert r["slippage"]["median_bps"] == pytest.approx(10.0)
    assert r["slippage"]["mean_bps"] == pytest.approx(10.0)


def test_review_no_fills_is_none(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    from datetime import datetime, timezone
    r = review.build_report(led.all_records(), [], datetime.now(timezone.utc))
    assert r["slippage"] is None
