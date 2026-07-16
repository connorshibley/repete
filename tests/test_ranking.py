from datetime import datetime, timezone

import ranking

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)
TS = "2026-07-18T00:00:00+00:00"


def _lesson(lid, status="active", symbols=None, regime=None, sup=2, con=0,
            hypothesis="h"):
    return {"id": lid, "status": status, "hypothesis": hypothesis,
            "scope": {"symbols": symbols, "regime": regime},
            "supports": [f"s{i}" for i in range(sup)],
            "contradicts": [f"c{i}" for i in range(con)],
            "created_ts": TS, "last_evidence_ts": TS, "source": "t"}


def test_symbol_match_outranks_regime_match():
    states = {"a": _lesson("a", symbols=["XLE"]),
              "b": _lesson("b", regime="down/high")}
    top = ranking.top_lessons(states, "XLE", "down/high", 2, NOW)
    assert top[0]["id"] == "a" and top[1]["id"] == "b"


def test_regime_match_outranks_global():
    states = {"a": _lesson("a"), "b": _lesson("b", regime="up/low")}
    top = ranking.top_lessons(states, None, "up/low", 2, NOW)
    assert top[0]["id"] == "b"


def test_strategy_scope_bonus():
    a = _lesson("a")
    b = _lesson("b")
    b["scope"]["strategy"] = "tsmom"
    states = {"a": a, "b": b}
    top = ranking.top_lessons(states, None, None, 2, NOW, strategy="tsmom")
    assert top[0]["id"] == "b"  # strategy match outranks plain global


def test_active_fills_before_candidates():
    states = {"a": _lesson("a", status="candidate", symbols=["SPY"]),
              "b": _lesson("b", status="active")}
    top = ranking.top_lessons(states, "SPY", None, 1, NOW)
    assert top[0]["id"] == "b"  # active first even when candidate scores higher


def test_refuted_caution_always_appended():
    states = {"a": _lesson("a"), "r": _lesson("r", status="refuted", sup=0, con=4)}
    top = ranking.top_lessons(states, None, None, 1, NOW)
    assert top[-1]["id"] == "r" and top[-1]["_caution"]


def test_retired_lessons_never_retrieved():
    states = {"a": _lesson("a", status="retired")}
    assert ranking.top_lessons(states, None, None, 5, NOW) == []


def test_format_block_annotates_off_regime_and_caution():
    states = {"a": _lesson("a", regime="up/low", hypothesis="bull-only idea"),
              "r": _lesson("r", status="refuted", sup=1, con=3, hypothesis="dead idea")}
    top = ranking.top_lessons(states, None, "down/high", 5, NOW)
    block = ranking.format_lessons_block(top, "down/high", 2000)
    assert "(different regime)" in block
    assert "CAUTION (refuted, n=1/3)" in block


def test_format_block_respects_char_cap():
    states = {f"l{i}": _lesson(f"l{i}", hypothesis="x" * 200) for i in range(20)}
    top = ranking.top_lessons(states, None, None, 20, NOW)
    block = ranking.format_lessons_block(top, None, 500)
    assert len(block) <= 500


def test_empty_book():
    assert ranking.format_lessons_block([], None, 100) == "(no validated lessons yet)"
