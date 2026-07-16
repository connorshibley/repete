"""Freshness guard: risk.bars_fresh must reject stale bar windows —
the deterministic rail against the stale-data API failure mode."""
from datetime import datetime, timedelta, timezone

import risk


def _bar(days_ago: int) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"ts": ts.isoformat(), "open": 100, "high": 101, "low": 99,
            "close": 100, "volume": 1000}


def test_fresh_bars_pass():
    assert risk.bars_fresh([_bar(10), _bar(1)], max_age_days=4)


def test_stale_bars_fail():
    assert not risk.bars_fresh([_bar(60), _bar(45)], max_age_days=4)


def test_empty_bars_fail():
    assert not risk.bars_fresh([], max_age_days=4)


def test_boundary_age_passes():
    # exactly max_age_days old is still acceptable (<=)
    assert risk.bars_fresh([_bar(4)], max_age_days=4)
    assert not risk.bars_fresh([_bar(5)], max_age_days=4)


def test_zero_disables_check():
    assert risk.bars_fresh([_bar(500)], max_age_days=0)
    assert risk.bars_fresh([], max_age_days=0)


def test_naive_timestamp_treated_as_utc():
    naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    assert risk.bars_fresh([{"ts": naive.isoformat()}], max_age_days=4)


def test_cycle_aborts_on_stale_spy(tmp_path, monkeypatch, cfg):
    """End-to-end: stale SPY bars must abort the whole cycle with a ledger
    event and zero orders (the fixture's Jan-2026 bars ARE stale when the
    guard is on)."""
    import yaml
    import main
    from ledger import Ledger
    from conftest import make_bars

    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_bar_age_days"] = 4  # re-enable the guard
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    class StaleBroker:
        submitted: list = []

        def account(self):
            return {"equity": 100_000.0, "cash": 100_000.0,
                    "last_equity": 100_000.0, "buying_power": 100_000.0}

        def positions(self):
            return {}

        def bars(self, symbol, timeframe, limit):
            return make_bars([10] * 6 + [9, 9, 9, 20])  # buy shape, stale ts

        def market_order(self, *a, **k):
            raise AssertionError("stale cycle must not place orders")

        bracket_market_order = market_order

    monkeypatch.setattr(main, "Broker", lambda cfg: StaleBroker())
    main.run_cycle()

    led = Ledger(cfg["memory"]["ledger_path"])
    events = [r for r in led.all_records() if r["type"] == "event"]
    assert any(e["event"] == "stale_data_abort" for e in events)
    assert not any(r["type"] == "decision" for r in led.all_records())
