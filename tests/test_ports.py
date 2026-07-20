"""2026-07-19 common-trade ports: post-exit runner tracking, model-version
fingerprint, citation-graded lessons, trailing stop, risk-based sizing,
re-entry cooldown, heat report. All offline."""
from datetime import datetime, timedelta, timezone

import backtest
import learn
import modelver
import postexit
import review
import risk
from judgments import JudgmentStore
from ledger import Ledger
from lessons import LessonStore
from conftest import make_bars


PCFG = {"enabled": True, "mark_days": [15, 30, 60],
        "left_on_table_pct": 8.0, "good_lock_in_pct": -5.0,
        "max_marks_per_run": 10}


# ---- post-exit runner tracking ----

def _bars_after(closes, start_day=1):
    return make_bars(closes, start_day=start_day)


def test_compute_mark_windows():
    # exit at day 5 @ 100; days 6..20 rally to 115 then fade to 96
    closes = [100] * 5 + [104, 110, 115, 100, 96] + [96] * 10
    bars = _bars_after(closes)
    exit_ts = bars[4]["ts"]
    m = postexit.compute_mark(bars, exit_ts, 100.0, 15)
    assert m["max_extension_pct"] == 15.0
    assert m["leftover_pct"] == -4.0
    assert postexit.compute_mark(bars, exit_ts, 100.0, 1)["max_extension_pct"] == 4.0
    assert postexit.compute_mark([], exit_ts, 100.0, 15) is None


def test_mark_due_and_verdicts():
    state = {"exit_ts": "2026-01-01T00:00:00+00:00", "marks": {},
             "result": "win"}
    now = datetime(2026, 2, 5, tzinfo=timezone.utc)   # 35 days later
    assert postexit.mark_due(state, now, [15, 30, 60]) == [15, 30]

    # winner that kept running -> left_on_table
    s = {"result": "win", "marks": {60: {"leftover_pct": 2.0,
                                         "max_extension_pct": 12.0}}}
    assert postexit.verdict_for(s, PCFG) == "left_on_table"
    # winner that faded hard -> good_lock_in
    s = {"result": "win", "marks": {60: {"leftover_pct": -9.0,
                                         "max_extension_pct": 1.0}}}
    assert postexit.verdict_for(s, PCFG) == "good_lock_in"
    # winner, nothing dramatic -> mixed
    s = {"result": "win", "marks": {60: {"leftover_pct": 1.0,
                                         "max_extension_pct": 3.0}}}
    assert postexit.verdict_for(s, PCFG) == "mixed"
    # loser that bounced -> stopped_then_recovered
    s = {"result": "loss", "marks": {60: {"leftover_pct": 9.0,
                                          "max_extension_pct": 11.0}}}
    assert postexit.verdict_for(s, PCFG) == "stopped_then_recovered"
    # loser that kept falling -> stop_confirmed
    s = {"result": "loss", "marks": {60: {"leftover_pct": -12.0,
                                          "max_extension_pct": 1.0}}}
    assert postexit.verdict_for(s, PCFG) == "stop_confirmed"
    # no final mark yet -> no verdict
    s = {"result": "win", "marks": {15: {"leftover_pct": 0, "max_extension_pct": 0}}}
    assert postexit.verdict_for(s, PCFG) is None


class _FakeLedger:
    def __init__(self, closed=None):
        self._closed = closed or []
        self.events = []

    def closed_trades(self):
        return self._closed

    def log_event(self, event, detail=""):
        self.events.append((event, detail))


class _FakeBroker:
    def __init__(self, bars):
        self._bars = bars

    def bars(self, symbol, timeframe, limit):
        return self._bars


def test_postexit_run_opens_marks_and_resolves(tmp_path, cfg):
    cfg["learning"]["postexit"] = {**PCFG,
                                   "path": str(tmp_path / "postexit.jsonl")}
    closed = [{"trade_id": "t1", "symbol": "SPY", "strategy": "meanrev",
               "exit_ts": "2026-01-05T21:00:00+00:00", "exit_price": 100.0,
               "entry_price": 95.0, "pnl_pct": 5.26, "exit_reason":
               "strategy_sell", "result": "win"},
              {"trade_id": "t2", "symbol": "SPY", "exit_ts": "2026-01-05T21:00:00+00:00",
               "exit_price": 0.0, "exit_reason": "entry_unfilled", "result": "loss"}]
    ledger = _FakeLedger(closed)
    # 70 days of REAL calendar bars after the exit: runs to 115 (left_on_table)
    base = datetime(2026, 1, 1, 21, tzinfo=timezone.utc)
    closes75 = [100] * 5 + [110, 115] + [102] * 68
    bars = [{"ts": (base + timedelta(days=i)).isoformat(), "open": c,
             "high": c, "low": c, "close": c, "volume": 1000}
            for i, c in enumerate(closes75)]
    now = datetime(2026, 3, 20, tzinfo=timezone.utc)  # all marks due

    out = postexit.run(ledger, _FakeBroker(bars), cfg, now=now)
    assert out == {"opened": 1, "marked": 3, "resolved": 1}

    states = postexit.PostExitStore(cfg["learning"]["postexit"]["path"]).replay()
    assert list(states) == ["t1"]                     # entry_unfilled skipped
    assert states["t1"]["verdict"] == "left_on_table"
    assert ("postexit_verdict", "SPY trade t1: left_on_table") in ledger.events

    # idempotent: second run opens/marks nothing new
    out2 = postexit.run(ledger, _FakeBroker(bars), cfg, now=now)
    assert out2 == {"opened": 0, "marked": 0, "resolved": 0}

    s = postexit.summary(states)
    assert s["n_resolved"] == 1 and s["verdicts"] == {"left_on_table": 1}
    assert s["avg_winner_max_extension_pct"] == 15.0


# ---- model-version fingerprint ----

def test_fingerprint_changes_with_content(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "strategies").mkdir()
    (tmp_path / "config.yaml").write_text("a: 1\n")
    (tmp_path / "src" / "risk.py").write_text("x = 1\n")
    f1 = modelver.fingerprint(str(tmp_path))
    f2 = modelver.fingerprint(str(tmp_path))
    assert f1["version"] == f2["version"] and len(f1["version"]) == 12
    (tmp_path / "src" / "risk.py").write_text("x = 2\n")
    assert modelver.fingerprint(str(tmp_path))["version"] != f1["version"]
    # a new strategy file changes the version too
    (tmp_path / "src" / "strategies" / "new.py").write_text("y = 1\n")
    f3 = modelver.fingerprint(str(tmp_path))
    assert "src/strategies/new.py" in f3["files"]


def test_ledger_stamps_model_version(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.set_model_version("abc123def456")
    tid = led.log_decision("SPY", "buy", "r", {}, None, executed=True,
                           entry_price=100.0, qty=1)
    led.close_trade(tid, 105.0, 5.0, 5.0)
    recs = led.all_records()
    assert all(r["model_version"] == "abc123def456" for r in recs)
    # closed_trades carries the outcome timestamp for post-exit tracking
    assert led.closed_trades()[0]["exit_ts"] == recs[-1]["ts"]


def test_model_version_breakdown():
    closed = [{"model_version": "v1", "pnl": 10.0},
              {"model_version": "v1", "pnl": -4.0},
              {"pnl": 2.0}]
    mv = review.model_version_breakdown(closed, "v2")
    assert mv["versions"]["v1"] == {"n": 2, "pnl": 6.0}
    assert mv["versions"]["untagged"]["n"] == 1
    assert mv["mixed"] is True and mv["current"] == "v2"


# ---- citation-graded lessons ----

def _lesson_store(tmp_path):
    ls = LessonStore(str(tmp_path / "lessons.jsonl"))
    lid = ls.add_lesson("possible pattern (n=1): dip buys chop in down/low",
                        {"symbols": ["SPY"]}, source="t0")
    return ls, lid


def test_grade_cited_lessons_supports_and_contradicts(tmp_path):
    ls, lid = _lesson_store(tmp_path)
    j = {"id": "jg-1", "trade_id": "t9", "verdict": "veto",
         "cited_lessons": [lid, "ls-unknown"]}
    assert learn.grade_cited_lessons(ls, j, "good_veto") == 1
    st = ls.replay()[lid]
    assert st["supports"] == ["t9"] and not st["contradicts"]
    # same trade never evidences the same lesson twice
    assert learn.grade_cited_lessons(ls, j, "good_veto") == 0
    # a bad call contradicts (different trade)
    j2 = {"id": "jg-2", "trade_id": "t10", "verdict": "veto",
          "cited_lessons": [lid]}
    assert learn.grade_cited_lessons(ls, j2, "bad_veto") == 1
    assert ls.replay()[lid]["contradicts"] == ["t10"]


def test_resolve_realized_grades_citations(tmp_path):
    ls, lid = _lesson_store(tmp_path)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = led.log_decision("SPY", "buy", "r", {}, None, executed=True,
                           entry_price=100.0, qty=1)
    led.close_trade(tid, 110.0, 10.0, 10.0)
    js = JudgmentStore(str(tmp_path / "j.jsonl"))
    js.log_judgment(tid, "SPY", "buy", "approve", 1.0, 100.0, None,
                    kind="llm", executed=True, cited_lessons=[lid])
    assert learn.resolve_realized(led, js, ls) == 1
    st = ls.replay()[lid]
    assert st["supports"] == [tid]     # good_approve -> the lesson helped


def test_judgment_store_keeps_cited_lessons(tmp_path):
    js = JudgmentStore(str(tmp_path / "j.jsonl"))
    jid = js.log_judgment("t1", "SPY", "buy", "veto", 1.0, 100.0, None,
                          kind="llm", executed=False, cited_lessons=["ls-a"])
    assert js.replay()[jid]["cited_lessons"] == ["ls-a"]


def test_lessons_block_shows_ids(tmp_path):
    import ranking
    ls, lid = _lesson_store(tmp_path)
    now = datetime.now(timezone.utc)
    ranked = ranking.top_lessons(ls.replay(), "SPY", None, 8, now)
    block = ranking.format_lessons_block(ranked, None, 2000)
    assert lid in block


# ---- trailing stop / cooldown / risk-based sizing (pure) ----

def test_trail_stop_math(cfg):
    cfg["risk"]["brackets"]["trailing_atr_mult"] = 3.0
    assert risk.trail_stop(120.0, 2.0, cfg) == 114.0
    assert risk.trail_stop(120.0, None, cfg) is None
    cfg["risk"]["brackets"]["trailing_atr_mult"] = 0
    assert risk.trail_stop(120.0, 2.0, cfg) is None


def test_cooldown_blocked():
    now = "2026-01-10T00:00:00+00:00"
    assert risk.cooldown_blocked("2026-01-08T00:00:00+00:00", now, 5)
    assert not risk.cooldown_blocked("2026-01-01T00:00:00+00:00", now, 5)
    assert not risk.cooldown_blocked(None, now, 5)
    assert not risk.cooldown_blocked("2026-01-08T00:00:00+00:00", now, 0)


def test_cooldown_days_scoped_per_strategy(cfg):
    cfg["risk"]["reentry_cooldown"] = {"days": 5, "strategies": ["meanrev"]}
    assert risk.cooldown_days_for(cfg, "meanrev") == 5
    assert risk.cooldown_days_for(cfg, "tsmom") == 0
    cfg["risk"]["reentry_cooldown"]["strategies"] = None
    assert risk.cooldown_days_for(cfg, "tsmom") == 5
    cfg["risk"].pop("reentry_cooldown")
    assert risk.cooldown_days_for(cfg, "meanrev") == 0


def test_trail_stop_scoped_per_strategy(cfg):
    cfg["risk"]["brackets"]["trailing_atr_mult"] = 3.0
    cfg["risk"]["brackets"]["trailing_strategies"] = ["tsmom"]
    assert risk.trail_stop(120.0, 2.0, cfg, strategy="tsmom") == 114.0
    assert risk.trail_stop(120.0, 2.0, cfg, strategy="meanrev") is None


def test_risk_based_sizing(cfg, account):
    cfg["risk"]["risk_sizing"] = {"enabled": True, "risk_pct": 0.1,
                                  "strategies": None}
    # 5% stop: $100 risk / 0.05 = $2000 -> 20 shares
    assert risk.size_order(account, 100.0, cfg, stop_price=95.0) == 20
    # 10% stop halves the notional
    assert risk.size_order(account, 100.0, cfg, stop_price=90.0) == 10
    # no stop known -> falls back to notional 1% = $1000 -> 10
    assert risk.size_order(account, 100.0, cfg) == 10
    # scoped to another strategy -> notional
    cfg["risk"]["risk_sizing"]["strategies"] = ["tsmom"]
    assert risk.size_order(account, 100.0, cfg, strategy="meanrev",
                           stop_price=95.0) == 10
    assert risk.size_order(account, 100.0, cfg, strategy="tsmom",
                           stop_price=95.0) == 20
    # risk mode ignores the vol_target multiplier (mutually exclusive)
    calm = make_bars([100 + 0.01 * i for i in range(26)])
    cfg["risk"]["vol_target"] = {"enabled": True, "annual_vol": 0.20,
                                 "period": 20, "min_scale": 0.5,
                                 "max_scale": 1.5}
    assert risk.size_order(account, 100.0, cfg, bars=calm, strategy="tsmom",
                           stop_price=95.0) == 20


# ---- backtest sim: trailing + cooldown ----

def _crossover_series():
    """Cross up at bar 9, run to 30, one-bar collapse to 5."""
    return make_bars([10] * 6 + [9, 9, 9, 20, 24, 28, 30, 30, 30, 5, 5, 5])


def test_sim_trailing_stop_exits_on_collapse(cfg):
    bars = {"SPY": _crossover_series()}
    base = backtest.simulate(bars, cfg, {"stop_atr_mult": 0,
                                         "take_profit_atr_mult": 0},
                             strategy_name="ma_crossover")
    trail = backtest.simulate(bars, cfg, {"stop_atr_mult": 0,
                                          "take_profit_atr_mult": 0,
                                          "trailing_atr_mult": 3.0},
                              strategy_name="ma_crossover")
    assert not any(t.exit_reason == "stop_loss" for t in base.trades)
    stops = [t for t in trail.trades if t.exit_reason == "stop_loss"]
    assert stops, "chandelier trail should fire on the collapse"
    # the ratchet must exit above the collapse price the baseline rides down to
    assert stops[0].exit_price > 5.0
    assert trail.total_return_pct > base.total_return_pct


def test_sim_reentry_cooldown_blocks_second_entry(cfg):
    # two cross-up cycles; a long cooldown must block the second entry
    closes = ([10] * 6 + [9, 9, 9, 20, 20, 20, 1, 1]      # buy then cross down
              + [9, 9, 20, 20, 20, 20])                    # second cross up
    bars = {"SPY": make_bars(closes)}
    params = {"stop_atr_mult": 0, "take_profit_atr_mult": 0}
    base = backtest.simulate(bars, cfg, params, strategy_name="ma_crossover")
    cfg["risk"]["reentry_cooldown"] = {"days": 30, "strategies": None}
    cooled = backtest.simulate(bars, cfg, params, strategy_name="ma_crossover")
    assert base.n_trades >= 2, "fixture must produce a re-entry to be meaningful"
    assert cooled.n_trades < base.n_trades


# ---- heat report (pure) ----

def test_heat_report():
    positions = {"SPY": {"qty": 10, "market_value": 1000.0},
                 "QQQ": {"qty": 5, "market_value": 2500.0}}
    stops = [{"symbol": "SPY", "qty": 10, "stop_price": 92.0}]
    h = review.heat_report(positions, stops, {"SPY": 2.0}, 100_000.0,
                           gap_atr_fraction=0.5)
    assert h["committed_risk_usd"] == 80.0        # 10 x (100 - 92)
    assert h["gap_add_usd"] == 10.0               # 10 x 0.5 x 2.0
    assert h["gap_adjusted_usd"] == 90.0
    assert h["pct_of_equity"] == 0.09
    assert h["uncovered"] == ["QQQ"]
