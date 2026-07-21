"""Entry price-drift guard (2026-07-21): defense in depth behind bars_fresh.
A buy is skipped when the live quote has drifted past risk.max_entry_drift_bps
from the signal price; quote outages fail OPEN; exits are never touched."""
import yaml

import main
import risk
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES


# ---- pure rail ----

def test_drift_ok_within_and_at_cap():
    cfg = {"risk": {"max_entry_drift_bps": 100}}
    assert risk.entry_drift_ok(100.0, 100.5, cfg)      # 50bps
    assert risk.entry_drift_ok(100.0, 101.0, cfg)      # exactly at cap
    assert risk.entry_drift_ok(100.0, 99.0, cfg)       # downward drift, at cap


def test_drift_rejects_over_cap_either_direction():
    cfg = {"risk": {"max_entry_drift_bps": 100}}
    assert not risk.entry_drift_ok(100.0, 101.01, cfg)
    assert not risk.entry_drift_ok(100.0, 98.9, cfg)


def test_drift_disabled_when_zero_or_absent():
    assert risk.entry_drift_ok(100.0, 200.0, {"risk": {"max_entry_drift_bps": 0}})
    assert risk.entry_drift_ok(100.0, 200.0, {"risk": {}})


# ---- cycle wiring ----

class DriftBroker(FakeCycleBroker):
    def __init__(self, bars, live_price=None, quote_error=False, **kw):
        super().__init__(bars, **kw)
        self._live = live_price
        self._quote_error = quote_error

    def latest_price(self, symbol):
        if self._quote_error:
            raise RuntimeError("quote feed down")
        return self._live


def _run(tmp_path, monkeypatch, cfg, broker, drift_bps=50):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["max_entry_drift_bps"] = drift_bps
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()
    return Ledger(cfg["memory"]["ledger_path"])


def test_cycle_blocks_buy_on_drift(tmp_path, monkeypatch, cfg):
    # signal price 20, live 30 -> 5000bps >> 50bps cap
    broker = DriftBroker(make_bars(BUY_CLOSES), live_price=30.0)
    led = _run(tmp_path, monkeypatch, cfg, broker)
    assert broker.submitted == []                      # no order placed
    rec = [r for r in led.all_records() if r["type"] == "decision"][-1]
    assert not rec["executed"] and "entry drift" in rec["detail"]

    from judgments import JudgmentStore
    js = JudgmentStore(cfg["learning"]["judgments_path"]).replay()
    assert any(j["kind"] == "rails" and "entry drift" in j["reasoning"]
               for j in js.values())


def test_cycle_allows_buy_within_cap(tmp_path, monkeypatch, cfg):
    broker = DriftBroker(make_bars(BUY_CLOSES), live_price=20.05)  # 25bps
    _run(tmp_path, monkeypatch, cfg, broker)
    assert len(broker.submitted) == 1


def test_cycle_fails_open_on_quote_outage(tmp_path, monkeypatch, cfg):
    broker = DriftBroker(make_bars(BUY_CLOSES), quote_error=True)
    _run(tmp_path, monkeypatch, cfg, broker)
    assert len(broker.submitted) == 1                  # outage != bad price
