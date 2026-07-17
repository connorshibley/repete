"""Morning market awareness: nomination validation, today-only loading,
judge-context injection, and the cycle's watchlist behavior — the invariant
under test everywhere: news can point the scanner, never place the trade."""
import json
from datetime import date

import yaml

import main
import market_context
from ledger import Ledger
from memory import Memory

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES


class BarsBroker:
    def __init__(self, bars_by_sym):
        self._bars = bars_by_sym

    def bars(self, symbol, timeframe, limit):
        if symbol not in self._bars:
            raise RuntimeError("unknown symbol")
        return self._bars[symbol]


def _fresh_bars(n=80):
    from datetime import datetime, timedelta, timezone
    out = []
    for i in range(n):
        ts = datetime.now(timezone.utc) - timedelta(days=n - i)
        out.append({"ts": ts.isoformat(), "open": 10, "high": 10, "low": 10,
                    "close": 10, "volume": 100})
    return out


# ---- validation ----

def test_validate_nominations_filters_garbage(cfg):
    cfg["risk"]["max_bar_age_days"] = 4
    broker = BarsBroker({"AAPL": _fresh_bars(), "MSFT": _fresh_bars()})
    raw = [
        {"symbol": "AAPL", "reason": "ok"},
        {"symbol": "aapl", "reason": "dup after uppercase"},
        {"symbol": "SPY", "reason": "already in universe"},
        {"symbol": "$GARBAGE!", "reason": "not a ticker"},
        {"symbol": "TOOLONGG", "reason": "not a ticker"},
        {"symbol": "XXXX", "reason": "bars fetch fails"},
        "not-a-dict",
        {"symbol": "MSFT", "reason": "ok too"},
    ]
    out = market_context.validate_nominations(raw, cfg, broker)
    assert [n["symbol"] for n in out] == ["AAPL", "MSFT"]


def test_validate_nominations_drops_stale_and_short(cfg):
    cfg["risk"]["max_bar_age_days"] = 4
    stale = make_bars([10] * 20)          # fixed Jan-2026 ts => stale
    short = _fresh_bars(10)               # too little history
    broker = BarsBroker({"AAPL": stale, "MSFT": short})
    raw = [{"symbol": "AAPL", "reason": "x"}, {"symbol": "MSFT", "reason": "y"}]
    assert market_context.validate_nominations(raw, cfg, broker) == []


def test_validate_nominations_cap(cfg):
    cfg["news"] = {"max_nominations": 1}
    cfg["risk"]["max_bar_age_days"] = 0
    broker = BarsBroker({"AAPL": _fresh_bars(), "MSFT": _fresh_bars()})
    raw = [{"symbol": "AAPL", "reason": "a"}, {"symbol": "MSFT", "reason": "b"}]
    assert len(market_context.validate_nominations(raw, cfg, broker)) == 1


# ---- today-only load ----

def _write_ctx(tmp_path, day, nominations=None):
    p = tmp_path / "market_context.json"
    p.write_text(json.dumps({"date": day, "summary": "markets are calm",
                             "events_today": ["CPI 8:30am"],
                             "symbol_flags": {"NVDA": "earnings tonight"},
                             "nominations": nominations or []}))
    return {"news": {"context_path": str(p)}}


def test_load_returns_todays_context(tmp_path):
    cfg = _write_ctx(tmp_path, date.today().isoformat())
    assert market_context.load(cfg)["summary"] == "markets are calm"


def test_load_ignores_yesterdays_context(tmp_path):
    cfg = _write_ctx(tmp_path, "2026-07-16")
    assert market_context.load(cfg) is None


def test_load_missing_file(tmp_path):
    assert market_context.load({"news": {"context_path":
                                         str(tmp_path / "nope.json")}}) is None


# ---- judge context injection ----

def test_market_context_block_in_judge_context(tmp_path, cfg):
    news_cfg = _write_ctx(tmp_path, date.today().isoformat())
    cfg["news"] = news_cfg["news"]
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    cfg["learning"]["lessons_path"] = str(tmp_path / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "judgments.jsonl")
    mem = Memory(cfg, Ledger(cfg["memory"]["ledger_path"]))
    ctx = mem.context_for_llm(symbol="NVDA")
    assert "TODAY'S MARKET CONTEXT" in ctx and "markets are calm" in ctx
    assert "NVDA news: earnings tonight" in ctx
    # other symbols don't get NVDA's flag
    assert "earnings tonight" not in mem.context_for_llm(symbol="KO")


def test_market_context_block_absent_when_stale(tmp_path, cfg):
    news_cfg = _write_ctx(tmp_path, "2026-07-16")
    cfg["news"] = news_cfg["news"]
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    cfg["learning"]["lessons_path"] = str(tmp_path / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "judgments.jsonl")
    mem = Memory(cfg, Ledger(cfg["memory"]["ledger_path"]))
    assert "MARKET CONTEXT" not in mem.context_for_llm()


# ---- cycle watchlist behavior ----

def _cycle_with_ctx(tmp_path, monkeypatch, cfg, nominations,
                    max_news_entries=1):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    cfg["risk"]["max_trades_per_day"] = 5
    cfg["news"] = {"context_path": "memory/market_context.json",
                   "max_news_entries_per_cycle": max_news_entries}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    import os
    os.makedirs("memory", exist_ok=True)
    with open("memory/market_context.json", "w") as f:
        json.dump({"date": date.today().isoformat(), "summary": "s",
                   "events_today": [], "symbol_flags": {},
                   "nominations": nominations}, f)
    broker = FakeCycleBroker(make_bars(BUY_CLOSES))  # every symbol crosses
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()
    return broker, Ledger(cfg["memory"]["ledger_path"])


def test_nominated_symbol_scanned_and_entered(tmp_path, monkeypatch, cfg):
    broker, led = _cycle_with_ctx(tmp_path, monkeypatch, cfg,
                                  [{"symbol": "AAPL", "reason": "chip news"}])
    symbols = {o["symbol"] for o in broker.submitted}
    assert symbols == {"SPY", "AAPL"}  # universe entry + news entry
    aapl = [r for r in led.all_records() if r["type"] == "decision"
            and r["symbol"] == "AAPL" and r["executed"]]
    assert aapl and aapl[0]["detail"] == "news-nominated"


def test_news_entry_cap_blocks_second_nomination(tmp_path, monkeypatch, cfg):
    broker, led = _cycle_with_ctx(
        tmp_path, monkeypatch, cfg,
        [{"symbol": "AAPL", "reason": "a"}, {"symbol": "MSFT", "reason": "b"}])
    symbols = {o["symbol"] for o in broker.submitted}
    assert "MSFT" not in symbols       # cap = 1 news entry
    holds = [r for r in led.all_records() if r["type"] == "decision"
             and r["symbol"] == "MSFT" and r["action"] == "hold"]
    assert any("news-nominated entry cap" in r["strategy_reason"]
               for r in holds)


def test_open_position_outside_universe_is_exit_scanned(tmp_path, monkeypatch,
                                                        cfg):
    """Regression: a held symbol no longer in cfg['symbols'] must still be
    scanned so its owner strategy can exit it."""
    monkeypatch.chdir(tmp_path)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)   # universe is just SPY
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("NVDA", "buy", "old entry", {}, None, executed=True,
                     entry_price=100.0, qty=5, strategy="ma_crossover")
    flat_bars = make_bars([10] * 12)  # no signals either way
    broker = FakeCycleBroker(flat_bars,
                             positions={"NVDA": {"qty": 5,
                                                 "market_value": 500.0}})
    monkeypatch.setattr(main, "Broker", lambda cfg: broker)
    main.run_cycle()

    nvda = [r for r in led.all_records() if r["type"] == "decision"
            and r["symbol"] == "NVDA" and r["action"] == "hold"]
    assert nvda  # scanned and explicitly held by its owner
