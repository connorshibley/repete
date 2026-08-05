"""learn.py end-to-end, fully offline: monkeypatched llm, fake broker."""
from datetime import datetime, timedelta, timezone

import pytest

import learn
import llm
from ledger import Ledger
from lessons import LessonStore
from judgments import JudgmentStore


@pytest.fixture
def env(tmp_path, cfg):
    cfg["memory"]["ledger_path"] = str(tmp_path / "m" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "m" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "m" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "m" / "judgments.jsonl")
    return (Ledger(cfg["memory"]["ledger_path"]),
            LessonStore(cfg["learning"]["lessons_path"]),
            JudgmentStore(cfg["learning"]["judgments_path"]), cfg)


def _close_trade(ledger, symbol="SPY", pnl=-50.0):
    tid = ledger.log_decision(symbol, "buy", "crossover", {}, None,
                              executed=True, entry_price=100.0, qty=10)
    ledger.close_trade(tid, 95.0 if pnl < 0 else 110.0, pnl,
                       pnl / 10, exit_reason="stop_loss")
    return tid


class BarsBroker:
    """Serves bars that pierce the stop the day after any decision."""
    def bars(self, symbol, timeframe, limit):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [{"ts": (base + timedelta(days=i)).isoformat(), "open": 100,
                 "high": 101, "low": 85, "close": 95, "volume": 1}
                for i in range(400)]


def test_evaluator_records_evidence_and_cursor(env, monkeypatch):
    ledger, lessons, judgments, cfg = env
    lid = lessons.add_lesson("SPY buys stop out", {"symbols": ["SPY"]}, "seed")
    tid = _close_trade(ledger)
    monkeypatch.setattr(llm, "evaluate_trade_vs_lessons",
                        lambda t, ls, c: [{"lesson_id": lid,
                                           "relation": "supports",
                                           "reason": "stopped out"}])
    n = learn.evaluate_closed_trades(ledger, lessons, cfg, limit=5)
    assert n == 1
    s = lessons.replay()[lid]
    assert s["supports"] == [tid]
    assert tid in lessons.evaluated_trade_ids()


def test_evaluator_skips_source_trade_self_evidence(env, monkeypatch):
    ledger, lessons, judgments, cfg = env
    tid = _close_trade(ledger)
    lessons.add_lesson("hypothesis from that trade", {}, source=tid)
    called = []
    monkeypatch.setattr(llm, "evaluate_trade_vs_lessons",
                        lambda t, ls, c: called.append(ls) or [])
    learn.evaluate_closed_trades(ledger, lessons, cfg, limit=5)
    assert called == []  # only lesson was its own source -> no LLM call
    assert tid in lessons.evaluated_trade_ids()


def test_evaluator_llm_failure_retries_next_run(env, monkeypatch):
    ledger, lessons, judgments, cfg = env
    lessons.add_lesson("h", {}, "seed")
    tid = _close_trade(ledger)
    monkeypatch.setattr(llm, "evaluate_trade_vs_lessons", lambda *a: None)
    assert learn.evaluate_closed_trades(ledger, lessons, cfg, 5) == 0
    assert tid not in lessons.evaluated_trade_ids()  # cursor NOT advanced


def test_resolve_realized_joins_outcomes(env):
    ledger, lessons, judgments, cfg = env
    tid = _close_trade(ledger, pnl=-50.0)
    judgments.log_judgment(tid, "SPY", "buy", "approve", 1.0, 100.0,
                           None, "llm", True)
    assert learn.resolve_realized(ledger, judgments) == 1
    j = next(iter(judgments.replay().values()))
    assert j["resolution"]["assessment"] == "bad_approve"


def test_counterfactual_respects_embargo_then_resolves(env):
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "buy", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=90.0, tp_price=115.0)
    early = datetime.now(timezone.utc)  # horizon = 2 + 5 days, not yet passed
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg, now=early) == 0
    late = early + timedelta(days=8)
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg, now=late) == 1
    j = next(iter(judgments.replay().values()))
    assert j["resolution"]["exit_reason"] == "stop_loss"    # low 85 pierces 90
    assert j["resolution"]["assessment"] == "good_veto"


def test_transitions_mirrored_to_ledger(env):
    ledger, lessons, judgments, cfg = env
    lid = lessons.add_lesson("h", {}, "seed")
    for i in range(3):
        lessons.add_evidence(lid, f"t{i}", "supports", "x")
    assert learn.run_transitions(ledger, lessons, cfg) == 1
    assert lessons.replay()[lid]["status"] == "active"
    events = [r for r in ledger.all_records() if r["type"] == "event"]
    assert any(e["event"] == "lesson_active" for e in events)


def test_meta_merge_applied_with_caps(env, monkeypatch):
    ledger, lessons, judgments, cfg = env
    a = lessons.add_lesson("XLE stops fire", {"symbols": ["XLE"]}, "t1")
    b = lessons.add_lesson("XLE buys stop out", {"symbols": ["XLE"]}, "t2")
    lessons.add_evidence(a, "e1", "supports", "x")
    lessons.add_evidence(b, "e1", "supports", "x")
    lessons.add_evidence(b, "e2", "contradicts", "y")
    monkeypatch.setattr(llm, "propose_merge",
                        lambda ls, c: {"source_ids": [a, b],
                                       "hypothesis": "XLE buys stop out (merged)",
                                       "scope": {"symbols": ["XLE"]}})
    assert learn.apply_meta_merge(ledger, lessons, cfg)
    states = lessons.replay()
    merged = [s for s in states.values() if s["source"] == "consolidation"][0]
    assert merged["status"] == "candidate"           # must re-earn active
    assert merged["supports"] == ["e1"]              # union, not double-count
    assert merged["contradicts"] == ["e2"]
    assert states[a]["status"] == "retired" and states[b]["status"] == "retired"


def test_full_run_offline_no_llm_no_broker(env, monkeypatch, capsys):
    ledger, lessons, judgments, cfg = env
    _close_trade(ledger)
    # llm disabled in cfg fixture -> evaluate returns None -> nothing breaks
    s = learn.full_run(cfg, with_meta=False, broker=None)
    assert s["counterfactual"] == 0 and s["merged"] is False
    assert "candidate" in s["book"]
    # rendered view exists with the GENERATED header
    text = open(cfg["memory"]["learnings_path"]).read()
    assert "GENERATED from memory/lessons.jsonl" in text


def test_migrate_legacy_idempotent(env):
    ledger, lessons, judgments, cfg = env
    with open(cfg["memory"]["learnings_path"], "w") as f:
        f.write("# Learnings\n\n- **2026-06-01** (trade ab): old note\n")
    assert learn.migrate_legacy(ledger, lessons, cfg) == 1
    learn.full_run(cfg, broker=None)  # renders GENERATED file
    assert learn.migrate_legacy(ledger, lessons, cfg) == 0  # second run: no-op


def test_counterfactual_fetch_covers_old_judgments(env):
    """Regression: a judgment left unresolved for weeks must fetch enough
    bars to reach back to its decision date (was capped at horizon+10)."""
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "buy", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=90.0, tp_price=None)

    class LimitBroker(BarsBroker):
        def __init__(self):
            self.limits = []

        def bars(self, symbol, timeframe, limit):
            self.limits.append(limit)
            return super().bars(symbol, timeframe, limit)

    broker = LimitBroker()
    late = datetime.now(timezone.utc) + timedelta(days=40)  # 40 days stale
    assert learn.resolve_counterfactuals(broker, judgments, cfg, now=late) == 1
    assert broker.limits[0] >= 40 + 7  # covers age + horizon


def test_inline_pass_never_raises(env, monkeypatch):
    ledger, lessons, judgments, cfg = env
    monkeypatch.setattr(learn, "evaluate_closed_trades",
                        lambda *a, **k: 1 / 0)
    learn.inline_pass(ledger, lessons, judgments, cfg)  # the point is: no raise
    events = [r for r in ledger.all_records() if r["type"] == "event"]
    assert any(e["event"] == "learning_error" for e in events)


# ---------------------------------------------------------------------------
# Vetoed SHORTS reach the counterfactual scorer (2026-08-05).
#
# learn.py's filter read `!= "buy"` and was annotated LOAD-BEARING: it was the
# only thing keeping shorts away from a hard-coded-long counterfactual that
# would have scored every short veto as a missed WINNER. Widened to
# ENTRY_ACTIONS in the same commit that made counterfactual.py directional.
#
# BarsBroker serves high=101, low=85, close=95 against a decision at 100, which
# is what makes these two tests a MEASUREMENT rather than a smoke test:
#
#   short, stop 110 / tp 85, read correctly -> low 85 reaches the tp
#                                              -> take_profit, +15.0
#   the same short read with the LONG geometry -> low 85 <= stop 110 on bar one
#                                              -> stop_loss at _pct(110) = +10.0
#
# Different reason AND different sign of story, so a regression cannot hide.
# ---------------------------------------------------------------------------

def test_a_vetoed_short_is_resolved_not_skipped(env):
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "short", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=110.0, tp_price=85.0)
    late = datetime.now(timezone.utc) + timedelta(days=8)
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg,
                                         now=late) == 1


def test_the_resolved_short_reads_the_SHORT_geometry(env):
    """The old behaviour is named in the assertions so this cannot silently
    revert: stop_loss/+10.0 was the corrupted answer, take_profit/+15.0 is the
    correct one."""
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "short", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=110.0, tp_price=85.0)
    late = datetime.now(timezone.utc) + timedelta(days=8)
    learn.resolve_counterfactuals(BarsBroker(), judgments, cfg, now=late)
    res = next(iter(judgments.replay().values()))["resolution"]
    assert res["exit_reason"] == "take_profit"     # NOT stop_loss on bar one
    assert res["pnl_pct"] == 15.0                  # NOT the +10.0 of _pct(110)


def test_a_vetoed_buy_still_resolves_exactly_as_before(env):
    """The twin, and the one that runs in production: nothing shorts, so every
    counterfactual this bot has ever resolved took this path and it must be
    untouched by the widening."""
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "buy", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=90.0, tp_price=115.0)
    late = datetime.now(timezone.utc) + timedelta(days=8)
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg,
                                         now=late) == 1
    res = next(iter(judgments.replay().values()))["resolution"]
    assert res["exit_reason"] == "stop_loss"
    assert res["pnl_pct"] == -10.0


def test_an_EXECUTED_short_is_still_skipped(env):
    """The other half of the same condition. A counterfactual answers "what
    would the blocked trade have done"; a trade that was not blocked has a real
    outcome, and resolving it twice would write a fabricated row beside the
    true one."""
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "short", "approve", 1.0, 100.0,
                           None, "llm", True, stop_price=110.0, tp_price=85.0)
    late = datetime.now(timezone.utc) + timedelta(days=8)
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg,
                                         now=late) == 0


def test_an_exit_action_never_reaches_the_counterfactual_scorer(env):
    """ENTRY_ACTIONS, not "any action": a "cover" is an EXIT, and asking what a
    blocked exit "would have returned" is not a question this function can
    answer — it prices an entry at the decision price."""
    ledger, lessons, judgments, cfg = env
    judgments.log_judgment("t1", "SPY", "cover", "veto", 1.0, 100.0,
                           None, "llm", False, stop_price=110.0, tp_price=85.0)
    late = datetime.now(timezone.utc) + timedelta(days=8)
    assert learn.resolve_counterfactuals(BarsBroker(), judgments, cfg,
                                         now=late) == 0
