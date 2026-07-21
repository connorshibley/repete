"""2026-07-21 brainstorm build-out: vendor cross-check, portfolio heat cap,
judge book-context, watchdog catch-up, revalidation report."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import yaml

import datacheck
import main
import risk
import watchdog
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES


# ---- 1. second-vendor cross-check ----

def test_divergence_reason_fires_and_holds():
    assert datacheck.divergence_reason(
        "2026-07-21T20:00:00Z", 630.0, "2026-07-21", 636.5, 0.5)  # ~1%
    assert datacheck.divergence_reason(
        "2026-07-21T20:00:00Z", 630.0, "2026-07-21", 630.5, 0.5) is None
    # different sessions => nothing comparable
    assert datacheck.divergence_reason(
        "2026-07-21T20:00:00Z", 630.0, "2026-07-20", 700.0, 0.5) is None


def test_crosscheck_disabled_or_outage_fails_open(cfg, monkeypatch):
    cfg["risk"]["data_crosscheck"] = {"enabled": False}
    assert datacheck.crosscheck_spy([{"ts": "t", "close": 1}], cfg) is None
    cfg["risk"]["data_crosscheck"] = {"enabled": True}
    monkeypatch.setattr(datacheck, "_yf_spy_close", lambda: None)  # outage
    assert datacheck.crosscheck_spy([{"ts": "t", "close": 1}], cfg) is None


def test_cycle_blocks_entries_on_divergence(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["data_crosscheck"] = {"enabled": True,
                                      "max_divergence_pct": 0.5}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    bars = make_bars(BUY_CLOSES)
    # yfinance "reports" the same session at a very different close
    monkeypatch.setattr(datacheck, "_yf_spy_close",
                        lambda: (bars[-1]["ts"][:10], 40.0))
    broker = FakeCycleBroker(bars)
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()
    assert broker.submitted == []
    recs = Ledger(cfg["memory"]["ledger_path"]).all_records()
    assert any(r.get("event") == "degradation"
               and "data_crosscheck" in r.get("detail", "") for r in recs)
    assert any(r.get("type") == "decision"
               and "data_crosscheck" in r.get("detail", "") for r in recs)


# ---- 2. portfolio heat cap ----

def _open_trade(qty, entry, stop):
    return {"qty": qty, "entry_price": entry,
            "order": {"stop_price": stop}}


def test_portfolio_heat_math(cfg):
    trades = {"a": _open_trade(10, 100.0, 95.0),   # $50
              "b": _open_trade(20, 50.0, 48.0)}    # $40
    assert risk.portfolio_heat(trades, cfg, 100_000.0) == 90.0
    # missing stop -> conservative per-trade fallback (1% of equity)
    trades["c"] = {"qty": 5, "entry_price": 10.0, "order": {}}
    assert risk.portfolio_heat(trades, cfg, 100_000.0) == 90.0 + 1000.0


def test_heat_cap_blocks_and_disabled(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_portfolio_heat_pct"] = 1.0   # $1000 cap on 100k
    acct = {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}
    open_trades = {"a": _open_trade(100, 100.0, 92.0)}  # $800 heat
    with pytest.raises(risk.RiskRejection, match="portfolio heat cap"):
        risk.pre_trade_checks("buy", "SPY", 100, 20.0, acct, {}, cfg,
                              open_trades=open_trades, candidate_stop=17.0)
    # within cap passes ($800 + 100*(20-19)=$100)
    risk.pre_trade_checks("buy", "SPY", 100, 20.0, acct, {}, cfg,
                          open_trades=open_trades, candidate_stop=19.0)
    cfg["risk"]["max_portfolio_heat_pct"] = 0     # disabled
    risk.pre_trade_checks("buy", "SPY", 100, 20.0, acct, {}, cfg,
                          open_trades=open_trades, candidate_stop=10.0)


# ---- 3. judge sees the book ----

def test_book_block_rendered_and_flat(tmp_path, cfg):
    from memory import Memory
    cfg["memory"]["ledger_path"] = str(tmp_path / "l.jsonl")
    cfg["learning"]["lessons_path"] = str(tmp_path / "le.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "j.jsonl")
    mem = Memory(cfg, Ledger(cfg["memory"]["ledger_path"]))
    positions = {"NVDA": {"qty": 5, "market_value": 900.0,
                          "unrealized_pl": -25.0}}
    acct = {"equity": 100_000.0}
    ctx = mem.context_for_llm(positions=positions, account=acct)
    assert "CURRENT BOOK (1 open positions" in ctx
    assert "NVDA" in ctx and "$-25" in ctx
    assert "CURRENT BOOK" not in mem.context_for_llm()  # flat/absent => absent


# ---- 4. watchdog late catch-up ----

ET = ZoneInfo("America/New_York")


def _hb(tmp_path, monkeypatch, iso_ts):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    if iso_ts:
        (tmp_path / "memory" / "heartbeat").write_text(iso_ts)


def test_catchup_runs_when_cycle_missed(tmp_path, monkeypatch):
    _hb(tmp_path, monkeypatch, "2026-07-20T19:50:00+00:00")  # yesterday
    ran = []
    monkeypatch.setattr(main, "run_cycle", lambda: ran.append(1))
    out = watchdog.catchup(datetime(2026, 7, 21, 15, 55, tzinfo=ET))
    assert out == "ran late cycle" and ran == [1]


def test_catchup_noop_when_already_ran_or_closed(tmp_path, monkeypatch):
    _hb(tmp_path, monkeypatch, "2026-07-21T19:50:00+00:00")  # today
    monkeypatch.setattr(main, "run_cycle",
                        lambda: (_ for _ in ()).throw(AssertionError))
    assert "already ran" in watchdog.catchup(
        datetime(2026, 7, 21, 15, 55, tzinfo=ET))
    _hb(tmp_path, monkeypatch, "2026-07-20T19:50:00+00:00")
    assert "closed" in watchdog.catchup(
        datetime(2026, 7, 21, 16, 5, tzinfo=ET))     # after close
    assert "weekend" in watchdog.catchup(
        datetime(2026, 7, 19, 15, 55, tzinfo=ET))    # Sunday


# ---- 5. revalidation report (offline smoke) ----

def test_revalidate_reports_per_enabled_strategy(tmp_path, cfg):
    import revalidate
    from datetime import timedelta, timezone
    start = datetime(2025, 8, 1, 21, tzinfo=timezone.utc)
    sym_bars = {"SPY": [
        {"ts": (start + timedelta(days=i)).isoformat(),
         "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
         "volume": 1000}
        for i, c in enumerate(10 + (i % 7) + i * 0.05 for i in range(240))]}
    cfg["strategies"] = {
        "ma_crossover": {"enabled": True, "priority": 1,
                         "fast_period": 3, "slow_period": 5},
        "tsmom": {"enabled": False, "priority": 2, "momentum_bars": 63},
    }
    out = revalidate.revalidate(cfg, sym_bars,
                                trials_path=str(tmp_path / "t.jsonl"))
    assert set(out) == {"ma_crossover"}          # disabled ones skipped
    r = out["ma_crossover"]
    assert isinstance(r["gate_pass"], bool) and "oos" in r
    if not r["gate_pass"]:
        assert r["reasons"]                      # failures carry reasons
