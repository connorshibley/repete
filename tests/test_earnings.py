"""Earnings-blackout filter: pure date logic, cache behavior, fail-open,
and the simulate() integration. Fully offline — yfinance never imported."""
import json

import backtest
import earnings

from conftest import make_bars
from test_main_cycle import BUY_CLOSES


# ---- next_within (pure) ----

def test_next_within_boundaries():
    dates = ["2026-01-10", "2026-04-15"]
    assert earnings.next_within(dates, "2026-01-08", 3)       # 2 days ahead
    assert earnings.next_within(dates, "2026-01-10", 3)       # same day
    assert not earnings.next_within(dates, "2026-01-06", 3)   # 4 days ahead
    assert earnings.next_within(dates, "2026-01-06", 5)
    # past dates are skipped; next one is April
    assert not earnings.next_within(dates, "2026-01-11", 5)
    assert earnings.next_within(dates, "2026-04-14", 1)


def test_next_within_empty_or_disabled():
    assert not earnings.next_within([], "2026-01-08", 3)
    assert not earnings.next_within(["2026-01-09"], "2026-01-08", 0)


# ---- cache + fail-open ----

def test_get_dates_uses_fresh_cache_without_fetch(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    from datetime import datetime, timezone
    cache.write_text(json.dumps({"AAPL": {
        "fetched": datetime.now(timezone.utc).isoformat(),
        "dates": ["2026-07-30"]}}))
    monkeypatch.setattr(earnings, "_fetch",
                        lambda s: (_ for _ in ()).throw(AssertionError(
                            "must not fetch on fresh cache")))
    assert earnings.get_dates("AAPL", str(cache)) == ["2026-07-30"]


def test_get_dates_failed_fetch_serves_stale_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({"AAPL": {
        "fetched": "2020-01-01T00:00:00+00:00",   # long expired
        "dates": ["2026-07-30"]}}))
    monkeypatch.setattr(earnings, "_fetch", lambda s: None)  # network down
    assert earnings.get_dates("AAPL", str(cache)) == ["2026-07-30"]


def test_get_dates_failed_fetch_no_cache_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(earnings, "_fetch", lambda s: None)
    assert earnings.get_dates("AAPL", str(tmp_path / "c.json")) == []


def test_blackout_symbols_etf_exempt(tmp_path, monkeypatch):
    calendar = {"AAPL": ["2026-07-18"], "SPY": []}
    monkeypatch.setattr(earnings, "get_dates",
                        lambda s, cache_path=None: calendar.get(s, []))
    out = earnings.blackout_symbols(["AAPL", "SPY"], 3, today="2026-07-17")
    assert out == {"AAPL"}


# ---- simulate() integration ----

def _sim(cfg, earnings_dict, days):
    bars = {"AAA": make_bars(BUY_CLOSES + [20, 20])}
    params = {"fast_period": 3, "slow_period": 5,
              "stop_atr_mult": 0, "take_profit_atr_mult": 0,
              "earnings_blackout_days": days}
    return backtest.simulate(bars, cfg, params, earnings=earnings_dict)


def test_simulate_blocks_buy_inside_blackout(cfg):
    # signal fires on 2026-01-10 (the crossover bar); earnings on 01-12
    r = _sim(cfg, {"AAA": ["2026-01-12"]}, days=3)
    assert r.n_trades == 0


def test_simulate_allows_buy_outside_blackout(cfg):
    r = _sim(cfg, {"AAA": ["2026-03-01"]}, days=3)
    assert r.n_trades == 1


def test_simulate_days_zero_ignores_calendar(cfg):
    r = _sim(cfg, {"AAA": ["2026-01-12"]}, days=0)
    assert r.n_trades == 1


def test_simulate_no_calendar_fails_open(cfg):
    r = _sim(cfg, None, days=3)
    assert r.n_trades == 1


# ---- live cycle: per-strategy blackout ----

def test_live_cycle_blackout_is_per_strategy(tmp_path, monkeypatch, cfg):
    """SPY blacked out for ma_crossover (days=3) must still be tradable by a
    strategy without the param — and the hold reason must say why."""
    import yaml
    import main
    from ledger import Ledger
    from test_main_cycle import FakeCycleBroker

    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["strategies"] = {"ma_crossover": {"enabled": True, "priority": 1,
                                          "fast_period": 3, "slow_period": 5,
                                          "earnings_blackout_days": 3}}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    import earnings as earnings_module
    monkeypatch.setattr(earnings_module, "blackout_symbols",
                        lambda syms, days, **k: {"SPY"})
    broker = FakeCycleBroker(make_bars(BUY_CLOSES))  # crossover would buy SPY
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)

    main.run_cycle()

    assert broker.submitted == []  # entry blocked
    led = Ledger(cfg["memory"]["ledger_path"])
    holds = [r for r in led.all_records() if r["type"] == "decision"
             and r["action"] == "hold" and r["symbol"] == "SPY"]
    assert any("earnings within 3d" in str(r["indicators"]) for r in holds)
