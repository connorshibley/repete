"""Every judged decision can be reconstructed; every unjudged one says so.

Audit Gate 1d (2026-08-21): "are per-decision traces logged well enough that
an independent auditor could verify temporal consistency action by action?"
The answer was no. The ledger kept the judge's OUTPUT and nothing it was
SENT — the prompt was an unnamed inline f-string, bound to nothing, and
src/llm_shadow.py carried a copy of it under a comment saying the two "MUST
match". 1,344 decisions, none reconstructible.

Three properties, each pinned here:

  1. A judged decision carries hashes that reproduce the stored bodies.
  2. An UNjudged decision carries a NULL marker, never an absent one.
  3. The bodies never enter the ledger stream, and nothing on the cycle's
     hot path opens the sidecar. Structural, not a benchmark.
"""
import hashlib
import json

import pytest

import llm
import store as store_mod
from ledger import Ledger


class _Sig:
    action, symbol, reason, indicators = "buy", "SPY", "momentum", {"rsi": 41}


def _cfg(**over):
    base = {"enabled": True, "model": "claude-sonnet-5", "max_tokens": 500}
    base.update(over)
    return {"llm": base}


def _judged_reply(monkeypatch, reply='{"verdict":"downsize","scale":0.5,'
                                     '"confidence":0.6,"reasoning":"extended"}'):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    monkeypatch.setattr(llm.llm_client, "complete", lambda *a, **k: reply)


# ---- 1. hashes reproduce the bodies ---------------------------------------

def test_the_prompt_hash_reproduces_the_stored_prompt(tmp_path, monkeypatch):
    """GATE 1d IN ONE ASSERTION. Hash the sidecar bodies; they must equal the
    ledger's fields. If they ever diverge, the trail is decoration."""
    _judged_reply(monkeypatch)
    ctx = "BOOK: 3 open\nLESSONS: none yet\nREGIME: up/low"
    review = llm.review_signal(_Sig(), ctx, _cfg())
    assert review["_prompt"] is not None

    led = Ledger(str(tmp_path / "ledger.jsonl"))
    tid = led.log_decision("SPY", "buy", "momentum", {"rsi": 41}, review,
                           executed=True)

    row = [r for r in led.all_records() if r.get("trade_id") == tid][0]
    side = [json.loads(l) for l in open(led.prompt_store_path()) if l.strip()]
    prompt_rows = [s for s in side if s["type"] == "prompt" and s["trade_id"] == tid]
    ctx_rows = [s for s in side if s["type"] == "context"]
    assert len(prompt_rows) == 1 and len(ctx_rows) == 1

    user = prompt_rows[0]["user"]
    body = ctx_rows[0]["body"]
    assert body == ctx
    assert hashlib.sha256(body.encode()).hexdigest() == row["context_sha256"]
    assert (hashlib.sha256((llm._SYSTEM + "\n\n" + user).encode()).hexdigest()
            == row["prompt_sha256"])
    assert row["system_sha256"] == hashlib.sha256(llm._SYSTEM.encode()).hexdigest()
    assert row["prompt_chars"] == len(llm._SYSTEM) + len(user)
    # and the user message is the one the judge was actually sent
    assert user == llm.review_user_message(_Sig(), ctx)


def test_the_bodies_never_enter_llm_review_or_the_ledger_stream(tmp_path, monkeypatch):
    _judged_reply(monkeypatch)
    review = llm.review_signal(_Sig(), "CTX", _cfg())
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, review, executed=False)
    raw = open(led.path).read()
    assert "_prompt" not in raw, "the reserved key must be stripped, not written"
    assert "Reply with JSON only" not in raw, "a prompt body leaked into the ledger"
    row = led.all_records()[0]
    assert "_prompt" not in row["llm_review"]
    assert "_user" not in raw and "_context" not in raw


# ---- 2. null, never absent -------------------------------------------------

@pytest.mark.parametrize("how", ["disabled", "absent_key", "api", "unknown_verdict", "unparseable"])
def test_an_unjudged_review_carries_a_null_prompt_not_an_absent_one(how, monkeypatch):
    """All five fallback paths. The marker lives on the fallback dict so every
    path inherits it; this is the test that would catch one path building its
    own dict and forgetting."""
    if how == "disabled":
        out = llm.review_signal(_Sig(), "c", _cfg(enabled=False))
    elif how == "absent_key":
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        out = llm.review_signal(_Sig(), "c", _cfg())
    elif how == "api":
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
        def boom(*a, **k): raise RuntimeError("vendor down")
        monkeypatch.setattr(llm.llm_client, "complete", boom)
        out = llm.review_signal(_Sig(), "c", _cfg())
    elif how == "unknown_verdict":
        _judged_reply(monkeypatch, '{"verdict":"maybe","scale":1.0}')
        out = llm.review_signal(_Sig(), "c", _cfg())
    else:
        _judged_reply(monkeypatch, "not json at all")
        out = llm.review_signal(_Sig(), "c", _cfg())
    assert "_prompt" in out, f"{how}: the key is ABSENT — indistinguishable from pre-2026-08-22 rows"
    assert out["_prompt"] is None, f"{how}: an unjudged verdict must not carry a prompt"


def test_a_null_prompt_writes_null_fields_and_no_sidecar(tmp_path):
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("SPY", "buy", "m", {},
                     {"verdict": "approve", "scale": 1.0, "_prompt": None},
                     executed=True)
    row = led.all_records()[0]
    assert row["prompt_sha256"] is None and "prompt_sha256" in row
    assert row["context_sha256"] is None
    import os
    assert not os.path.exists(led.prompt_store_path())


def test_a_pre_record_review_leaves_the_fields_null_but_present(tmp_path):
    """A caller that never heard of _prompt (old code, a test fixture) must
    still produce a row whose fields are present-and-None, so readers can
    always distinguish 'no prompt' from 'old schema' by the llm_review shape."""
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, {"verdict": "approve", "scale": 1.0},
                     executed=True)
    row = led.all_records()[0]
    assert row["prompt_sha256"] is None and "prompt_sha256" in row


# ---- 3. the hot path never opens the sidecar -------------------------------

def test_readers_never_open_the_prompt_store(tmp_path, monkeypatch):
    """THE PERFORMANCE INVARIANT, STRUCTURALLY. all_records()/open_buys()/
    closed_trades() parse the whole ledger, and closed_trades() runs per
    signal inside the cycle. If any of them ever opened the sidecar, prompt
    bodies would be on the hot path. Record every path open_store() is asked
    for and assert the sidecar is not among them during reads."""
    opened = []
    real = store_mod.open_store
    monkeypatch.setattr(store_mod, "open_store",
                        lambda p: opened.append(p) or real(p))
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, {"verdict": "approve", "scale": 1.0,
                                              "_prompt": None}, executed=True)
    opened.clear()
    led.all_records(); led.open_buys(); led.closed_trades()
    assert not any(p.endswith(Ledger.PROMPT_STREAM_SUFFIX) for p in opened), (
        f"a READER opened the prompt sidecar: {opened}")


def test_the_sidecar_is_opened_lazily_and_only_on_a_judged_write(tmp_path, monkeypatch):
    opened = []
    real = store_mod.open_store
    monkeypatch.setattr(store_mod, "open_store",
                        lambda p: opened.append(p) or real(p))
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    assert not any(p.endswith(Ledger.PROMPT_STREAM_SUFFIX) for p in opened)
    _judged_reply(monkeypatch)
    led.log_decision("SPY", "buy", "m", {}, llm.review_signal(_Sig(), "c", _cfg()),
                     executed=True)
    assert any(p.endswith(Ledger.PROMPT_STREAM_SUFFIX) for p in opened)


def test_context_dedups_across_a_cycle(tmp_path, monkeypatch):
    """Same context for every symbol in a cycle -> one body, N join rows."""
    _judged_reply(monkeypatch)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    ctx = "BOOK: identical for every symbol this cycle"
    for sym in ("SPY", "QQQ", "IWM"):
        class S(_Sig): symbol = sym
        led.log_decision(sym, "buy", "m", {}, llm.review_signal(S(), ctx, _cfg()),
                         executed=False)
    side = [json.loads(l) for l in open(led.prompt_store_path()) if l.strip()]
    assert sum(1 for s in side if s["type"] == "context") == 1
    assert sum(1 for s in side if s["type"] == "prompt") == 3


def test_a_sidecar_failure_never_loses_the_decision(tmp_path, monkeypatch):
    """The audit trail is not a trading path. A broken sidecar must degrade
    to a warning, and the decision row must still land with null hashes
    rather than no row at all."""
    _judged_reply(monkeypatch)
    led = Ledger(str(tmp_path / "ledger.jsonl"))

    class Broken:
        def append(self, rec): raise OSError("disk full")

    real = store_mod.open_store
    monkeypatch.setattr(store_mod, "open_store",
                        lambda p: Broken() if p.endswith(Ledger.PROMPT_STREAM_SUFFIX) else real(p))
    tid = led.log_decision("SPY", "buy", "m", {}, llm.review_signal(_Sig(), "c", _cfg()),
                           executed=True)
    rows = led.all_records()
    assert len(rows) == 1 and rows[0]["trade_id"] == tid, "the decision must survive"
    # the hashes are still recorded — they were computed before the write
    assert rows[0]["prompt_sha256"] is not None


def test_llm_shadow_copy_can_delegate():
    """The extraction exists so llm_shadow._user_message can stop being a copy
    maintained by comment. This pins the public name it should call."""
    assert callable(llm.review_user_message)
    assert "Reply with JSON only" in llm.review_user_message(_Sig(), "ctx")
