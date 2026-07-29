
from ledger import Ledger
from memory import Memory


def _setup(tmp_path, cfg, wins=8, losses=2):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "memory" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "memory" / "judgments.jsonl")
    led = Ledger(cfg["memory"]["ledger_path"])
    for i in range(wins + losses):
        tid = led.log_decision("SPY", "buy", f"signal {i}", {}, None,
                               executed=True, entry_price=100.0, qty=10)
        if i < losses:
            led.close_trade(tid, 95.0, -50.0, -5.0)
        else:
            led.close_trade(tid, 110.0, 100.0, 10.0)
    return led, Memory(cfg, led)


def test_balanced_sample_forces_losers(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg, wins=8, losses=2)
    sample = mem.balanced_trade_sample()
    losers = [t for t in sample if t["result"] == "loss"]
    # n=10, quota 0.20 -> at least 2 losers despite winners dominating
    assert len(losers) >= 2


def test_balanced_sample_no_fabricated_losers(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg, wins=5, losses=0)
    sample = mem.balanced_trade_sample()
    assert sample and all(t["result"] == "win" for t in sample)


def test_context_for_llm_all_sections(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg)
    ctx = mem.context_for_llm()
    assert "[LOSS]" in ctx
    assert "VALIDATED LESSONS" in ctx
    assert "YOUR RECENT CALIBRATION" in ctx
    assert "CURRENT REGIME: unknown" in ctx


def test_context_includes_lessons_and_regime(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg)
    mem.lessons.add_lesson("XLE buys stop out in down/high",
                           {"symbols": ["XLE"], "regime": "down/high"}, "t1")
    ctx = mem.context_for_llm(symbol="XLE",
                              regime={"label": "down/high", "trend": "down",
                                      "vol": "high", "spy_close": 500,
                                      "spy_sma": 520, "ann_vol": 0.3})
    assert "XLE buys stop out" in ctx
    assert "CURRENT REGIME: down/high" in ctx


def test_context_char_cap(tmp_path, cfg):
    cfg["learning"]["max_context_chars"] = 500
    _, mem = _setup(tmp_path, cfg)
    for i in range(30):
        mem.lessons.add_lesson("x" * 190, {}, f"t{i}")
    assert len(mem.context_for_llm()) <= 500


def test_add_learning_creates_candidate_lesson(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg)
    lid = mem.add_learning("possible pattern: n=1", trade_id="abc123")
    s = mem.lessons.replay()[lid]
    assert s["status"] == "candidate" and s["source"] == "abc123"
    assert "possible pattern: n=1" in mem.recent_learnings()


def test_empty_everything(tmp_path, cfg):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "memory" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "memory" / "judgments.jsonl")
    led = Ledger(cfg["memory"]["ledger_path"])
    mem = Memory(cfg, led)
    ctx = mem.context_for_llm()
    assert "no closed trades yet" in ctx and "no validated lessons yet" in ctx
    assert mem.recent_learnings() == "(no learnings yet)"


# ---- knowledge block (curated external principles, config-gated) ----

def test_knowledge_block_absent_by_default(tmp_path, cfg):
    _, mem = _setup(tmp_path, cfg)  # cfg fixture has no llm.knowledge_path
    assert mem.knowledge_block() == ""
    assert "KNOWLEDGE" not in mem.context_for_llm()


def test_knowledge_block_included_and_labeled(tmp_path, cfg):
    kpath = tmp_path / "principles.md"
    kpath.write_text("- Doing nothing is often correct.")
    cfg["llm"]["knowledge_path"] = str(kpath)
    _, mem = _setup(tmp_path, cfg)
    ctx = mem.context_for_llm()
    assert "KNOWLEDGE (external, unverified" in ctx
    assert "Doing nothing is often correct." in ctx
    # evidence sections still present and ordered before knowledge
    assert ctx.index("VALIDATED LESSONS") < ctx.index("KNOWLEDGE")


def test_knowledge_block_missing_file_degrades(tmp_path, cfg):
    cfg["llm"]["knowledge_path"] = str(tmp_path / "nope.md")
    _, mem = _setup(tmp_path, cfg)
    assert mem.knowledge_block() == ""
    assert "CURRENT REGIME" in mem.context_for_llm()  # context still renders


def test_knowledge_block_char_capped(tmp_path, cfg):
    kpath = tmp_path / "principles.md"
    kpath.write_text("x" * 10000)
    cfg["llm"]["knowledge_path"] = str(kpath)
    cfg["learning"]["max_context_chars"] = 2000
    _, mem = _setup(tmp_path, cfg)
    assert len(mem.knowledge_block()) <= 2000 // 4 + 80  # cap + header slack


# ---- similar-setups retrieval (OpenProphet port, 2026-07-21) ----

from types import SimpleNamespace


def _setup_mixed(tmp_path, cfg):
    """Trades across strategies/symbols/regimes: 4 tsmom NVDA up/low WINS,
    3 meanrev KO down/high wins, 1 meanrev KO down/high LOSS."""
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "memory" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "memory" / "judgments.jsonl")
    led = Ledger(cfg["memory"]["ledger_path"])
    for i in range(4):
        tid = led.log_decision("NVDA", "buy", f"tsmom {i}", {"mom": 0.10 + i / 100},
                               None, executed=True, entry_price=100.0, qty=10,
                               regime="up/low", strategy="tsmom")
        led.close_trade(tid, 110.0, 100.0, 10.0)
    for i in range(3):
        tid = led.log_decision("KO", "buy", f"meanrev {i}", {"rsi": 25}, None,
                               executed=True, entry_price=60.0, qty=10,
                               regime="down/high", strategy="meanrev")
        led.close_trade(tid, 66.0, 60.0, 10.0)
    tid = led.log_decision("KO", "buy", "meanrev dip", {"rsi": 22}, None,
                           executed=True, entry_price=60.0, qty=10,
                           regime="down/high", strategy="meanrev")
    led.close_trade(tid, 55.0, -50.0, -8.0)
    return Memory(cfg, led)


def test_similar_sample_prefers_matching_setup(tmp_path, cfg):
    mem = _setup_mixed(tmp_path, cfg)
    sig = SimpleNamespace(strategy="tsmom", symbol="NVDA", action="buy",
                          indicators={"mom": 0.11})
    sample = mem.similar_trade_sample(sig, "up/low")
    # all 8 fit (n=min(8,10)), but the 4 tsmom/NVDA trades must rank first
    assert [t["strategy"] for t in sample[:4]] == ["tsmom"] * 4


def test_similar_sample_forces_loser_even_when_dissimilar(tmp_path, cfg):
    mem = _setup_mixed(tmp_path, cfg)
    cfg["memory"]["review_lookback_trades"] = 5  # window still holds the loss
    sig = SimpleNamespace(strategy="tsmom", symbol="NVDA", action="buy",
                          indicators={})
    sample = mem.similar_trade_sample(sig, "up/low")
    assert any(t["result"] == "loss" for t in sample)  # invariant #5 holds


def test_context_header_similar_vs_balanced(tmp_path, cfg):
    mem = _setup_mixed(tmp_path, cfg)
    sig = SimpleNamespace(strategy="tsmom", symbol="NVDA", action="buy",
                          indicators={})
    with_sig = mem.context_for_llm(symbol="NVDA", strategy="tsmom", signal=sig)
    without = mem.context_for_llm(symbol="NVDA", strategy="tsmom")
    assert "MOST SIMILAR PAST CLOSED TRADES" in with_sig
    assert "balanced sample" in without and "MOST SIMILAR" not in without
