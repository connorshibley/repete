"""Phase C: audit pack exporter — offline, fixture ledger only."""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import disclaimer
import evidence
from ledger import Ledger

NOW = datetime(2026, 7, 22, 20, 0, tzinfo=timezone.utc)


def _seed(tmp_path, n_closed=2, n_open=1):
    led = Ledger(str(tmp_path / "memory" / "ledger.jsonl"))
    for i in range(n_closed):
        tid = led.log_decision(
            "SPY", "buy", "crossover", {"rsi": 40},
            {"verdict": "approve", "scale": 1.0, "confidence": 0.7,
             "reasoning": "ok", "cited_lessons": []},
            executed=True, entry_price=100.0, qty=10, strategy="tsmom",
            regime="up/low")
        led.close_trade(tid, 105.0, 50.0, 5.0, exit_reason="strategy_sell")
    for i in range(n_open):
        led.log_decision(
            "AAPL", "buy", "dip", {"rsi": 25},
            {"verdict": "approve", "scale": 1.0, "confidence": 0.6,
             "reasoning": "ok", "cited_lessons": []},
            executed=True, entry_price=200.0, qty=5, strategy="meanrev",
            regime="up/low")
    return led


def _cfg(tmp_path):
    return {"mode": "paper",
            "memory": {"ledger_path": str(tmp_path / "memory" / "ledger.jsonl")}}


def test_pack_builds_and_lineage_links(tmp_path):
    led = _seed(tmp_path)
    pack = evidence.build_pack(_cfg(tmp_path), led.all_records(), NOW,
                               root=str(tmp_path))
    assert set(pack) == {"summary.md", "performance.json", "lineage.json",
                        "invariants.json"}
    lin = pack["lineage.json"]
    assert lin["n_trades"] == 3            # 2 closed + 1 open, all executed
    closed = [t for t in lin["trades"].values() if t["exit"]]
    assert len(closed) == 2
    for t in closed:                        # full chain: entry -> judge -> exit
        assert t["entry"]["ts"] and t["judge"]["verdict"] == "approve"
        assert t["exit"]["pnl"] == 50.0
    assert not lin["incomplete"]
    assert pack["performance.json"]["report"]["n_closed"] == 2


def test_invariants_pass_on_clean_state(tmp_path):
    led = _seed(tmp_path)
    inv = evidence.invariants_check(_cfg(tmp_path), led.all_records(),
                                    root=str(tmp_path))
    assert inv["all_pass"], inv["failed"]
    assert inv["checks"]["ledger_stream_integrity"]["pass"] is True
    assert inv["checks"]["outcome_embargo"]["pass"] is True
    assert inv["checks"]["every_entry_judged"]["pass"] is True


def test_tampered_ledger_tail_flagged(tmp_path):
    led = _seed(tmp_path)
    records = led.all_records()             # read BEFORE tampering the file
    with open(tmp_path / "memory" / "ledger.jsonl", "a") as f:
        f.write('{"type": "decision", "truncat')   # simulated partial write
    inv = evidence.invariants_check(_cfg(tmp_path), records,
                                    root=str(tmp_path))
    assert inv["checks"]["ledger_stream_integrity"]["pass"] is False
    assert not inv["all_pass"]
    assert "ledger_stream_integrity" in inv["failed"]


def test_missing_stream_fail_soft(tmp_path):
    cfg = {"mode": "paper",
           "memory": {"ledger_path": str(tmp_path / "nope" / "ledger.jsonl")}}
    inv = evidence.invariants_check(cfg, [], root=str(tmp_path))
    assert inv["checks"]["ledger_stream_integrity"]["pass"] is None
    assert inv["all_pass"]                  # None = unverifiable, not failed


def test_disclaimer_page_check(tmp_path):
    led = _seed(tmp_path)
    (tmp_path / "dashboard.html").write_text(
        f"<html>{disclaimer.DISCLAIMER}</html>")
    (tmp_path / "journal.html").write_text("<html>no disclaimer here</html>")
    inv = evidence.invariants_check(_cfg(tmp_path), led.all_records(),
                                    root=str(tmp_path))
    c = inv["checks"]["disclaimer_on_pages"]
    assert c["pass"] is False               # journal.html rendered WITHOUT it
    pages = json.loads(c["detail"])
    assert pages["dashboard.html"] == "present"
    assert pages["journal.html"] == "MISSING"
    assert pages["blog.html"] == "not rendered"


def test_export_writes_dated_bundle(tmp_path):
    led = _seed(tmp_path)
    dest = evidence.export(str(tmp_path / "evidence"), _cfg(tmp_path),
                           led.all_records(), NOW, root=str(tmp_path))
    assert os.path.basename(dest) == "pack-20260722-2000"
    for name in ("summary.md", "performance.json", "lineage.json",
                 "invariants.json"):
        p = os.path.join(dest, name)
        assert os.path.exists(p)
        if name.endswith(".json"):
            with open(p) as f:
                json.load(f)                # strictly valid JSON, no Infinity
    with open(os.path.join(dest, "summary.md")) as f:
        summary = f.read()
    assert "PAPER-TRADING experiment" in summary   # disclaimer on the pack too


def test_publisher_disclaimer_is_the_same_object():
    """Single source of truth: publisher re-exports src/disclaimer.py."""
    from publisher import gates
    assert gates.DISCLAIMER is disclaimer.DISCLAIMER
