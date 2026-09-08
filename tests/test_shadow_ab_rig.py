"""The A/B rig: N candidates on one signal, joined to outcomes, never trading.

WHY THIS EXISTS. #160 made the judge's approve arm visible; this makes two
judges comparable. Three properties, in the order they would hurt:

  1. **It must never reach the cycle.** A comparison tool that can delay or
     break a trading cycle is worse than the question it answers.
  2. **Candidates must score the SAME signal.** A signal happens once. Running
     candidate B tomorrow compares two models on two market days, which
     measures the market, not the models.
  3. **"Better" must mean better, not similar.** score_llm_shadow's original
     metrics score agreement with the incumbent — useful for a safe swap,
     useless when the incumbent is the thing you want to improve on. The join
     to realized P&L is what makes the question answerable.
"""
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import llm_shadow  # noqa: E402
import score_llm_shadow as scorer  # noqa: E402


def _sig():
    return types.SimpleNamespace(action="buy", symbol="AAPL", strategy="tsmom",
                                 reason="r", indicators={"rsi": 58})


def _cfg(tmp_path, n=2, enabled=True):
    cands = [{"name": f"c{i}", "base_url": f"http://h{i}/v1",
              "model": f"m{i}", "enable_thinking": False} for i in range(n)]
    return {"llm_shadow": {"enabled": enabled, "candidates": cands,
                           "log_path": str(tmp_path / "shadow.jsonl"),
                           "timeout_seconds": 1}}


# --- 2. every candidate sees the same signal --------------------------------

def test_every_candidate_gets_a_row_for_one_signal(monkeypatch, tmp_path):
    seen = []

    def fake(base_url, model, system, user, max_tokens, timeout,
             extra_body=None):
        seen.append((model, user))
        return '{"verdict":"downsize","scale":0.5,"confidence":0.6}'

    monkeypatch.setattr(llm_shadow, "_call_local", fake)
    cfg = _cfg(tmp_path)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx",
                              {"verdict": "approve", "scale": 1.0}, cfg)
    rows = [json.loads(x) for x in
            open(cfg["llm_shadow"]["log_path"]) if x.strip()]
    assert len(rows) == 2
    assert {r["candidate"] for r in rows} == {"c0", "c1"}
    # identical prompt to both — this is the whole point of N-way
    assert seen[0][1] == seen[1][1]


def test_one_broken_candidate_does_not_blind_the_others(monkeypatch, tmp_path):
    def fake(base_url, model, system, user, max_tokens, timeout,
             extra_body=None):
        if model == "m0":
            raise OSError("connection refused")
        return '{"verdict":"veto","scale":0.0}'

    monkeypatch.setattr(llm_shadow, "_call_local", fake)
    cfg = _cfg(tmp_path)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx", {"verdict": "approve"},
                              cfg)
    rows = [json.loads(x) for x in
            open(cfg["llm_shadow"]["log_path"]) if x.strip()]
    assert len(rows) == 2
    bad = [r for r in rows if r["candidate"] == "c0"][0]
    good = [r for r in rows if r["candidate"] == "c1"][0]
    assert bad["shadow_ok"] is False and bad["shadow_error"].startswith("call_failed")
    assert good["shadow_ok"] is True and good["shadow_verdict"] == "veto"


# --- the join key -----------------------------------------------------------

def test_the_prompt_hash_is_carried_so_outcomes_can_be_joined(monkeypatch,
                                                              tmp_path):
    """Scoring against agreement needs nothing; scoring against OUTCOMES needs
    the trade the signal became. prompt_sha256 is on the review dict at hook
    time and on the decision row after — the exact join."""
    monkeypatch.setattr(llm_shadow, "_call_local",
                        lambda *a, **k: '{"verdict":"approve","scale":1.0}')
    cfg = _cfg(tmp_path, n=1)
    live = {"verdict": "downsize", "scale": 0.5,
            "_prompt": {"prompt_sha256": "abc123"}}
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx", live, cfg)
    row = json.loads(open(cfg["llm_shadow"]["log_path"]).readline())
    assert row["prompt_sha256"] == "abc123"


def test_an_unjudged_signal_carries_a_null_hash_not_an_empty_string(
        monkeypatch, tmp_path):
    """A fallback verdict has no `_prompt` — no model produced it, so there is
    no decision to join to. None, not ''."""
    monkeypatch.setattr(llm_shadow, "_call_local",
                        lambda *a, **k: '{"verdict":"approve","scale":1.0}')
    cfg = _cfg(tmp_path, n=1)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx", {"verdict": "approve"},
                              cfg)
    row = json.loads(open(cfg["llm_shadow"]["log_path"]).readline())
    assert row["prompt_sha256"] is None


# --- the latency fix --------------------------------------------------------

def test_thinking_is_turned_off_on_the_wire(monkeypatch, tmp_path):
    """Without this the shadow spends ~47s per call against the qwen server
    generating a trace the extractor discards, vs ~4.8s. At 8-13 signals that
    decides whether the rig fits inside a cycle."""
    bodies = []

    def fake(base_url, model, system, user, max_tokens, timeout,
             extra_body=None):
        bodies.append(extra_body)
        return '{"verdict":"approve","scale":1.0}'

    monkeypatch.setattr(llm_shadow, "_call_local", fake)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx", {"verdict": "approve"},
                              _cfg(tmp_path, n=1))
    assert bodies[0] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_an_extra_field_cannot_shadow_the_model_or_the_ceiling():
    """A config typo must not silently re-point the model or lift max_tokens."""
    sent = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

    def urlopen(req, timeout=None):
        sent.update(json.loads(req.data.decode()))
        return _R()

    import urllib.request
    orig = urllib.request.urlopen
    urllib.request.urlopen = urlopen
    try:
        llm_shadow._call_local("http://h/v1", "real-model", "s", "u", 4000, 1,
                               extra_body={"model": "evil", "max_tokens": 9,
                                           "keep": 1})
    finally:
        urllib.request.urlopen = orig
    assert sent["model"] == "real-model" and sent["max_tokens"] == 4000
    assert sent["keep"] == 1


# --- 1. it never reaches the cycle ------------------------------------------

def test_disabled_makes_no_call_and_writes_nothing(monkeypatch, tmp_path):
    def explode(*a, **k):
        raise AssertionError("a disabled shadow called out")

    monkeypatch.setattr(llm_shadow, "_call_local", explode)
    cfg = _cfg(tmp_path, enabled=False)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx", {"verdict": "approve"},
                              cfg)
    assert not Path(cfg["llm_shadow"]["log_path"]).exists()


def test_the_hook_is_guarded_and_sits_before_the_decision_is_logged():
    """Structural: the hook must run before ledger.log_decision (which pops
    `_prompt`, the join key) and must be wrapped even though log_comparison
    already never raises — two rails, this repo's standing pattern."""
    src = (ROOT / "src" / "main.py").read_text()
    hook = src.find("llm_shadow.log_comparison")
    assert hook != -1, "main.py no longer runs the shadow"
    assert src.find("review = llm.review_signal(") < hook
    assert hook < src.find("ledger.log_decision", hook), (
        "the hook moved after log_decision — `_prompt` is popped there, so the "
        "join key would be gone")
    block = src[hook - 400:hook + 200]
    assert "try:" in block and "except Exception" in block


# --- 3. better means better -------------------------------------------------

def test_outcomes_join_on_the_prompt_hash():
    rows = [{"candidate": "c0", "prompt_sha256": "h1", "shadow_scale": 0.3},
            {"candidate": "c0", "prompt_sha256": "h2", "shadow_scale": 0.9},
            {"candidate": "c0", "prompt_sha256": "nope", "shadow_scale": 0.5}]
    led = [{"type": "decision", "prompt_sha256": "h1", "trade_id": "t1",
            "executed": True},
           {"type": "decision", "prompt_sha256": "h2", "trade_id": "t2",
            "executed": True},
           {"type": "outcome", "trade_id": "t1", "pnl": -100.0},
           {"type": "outcome", "trade_id": "t2", "pnl": 50.0}]
    joined = scorer.join_outcomes(rows, led)
    assert len(joined) == 2, "a row with no matching decision must be dropped"
    assert {j["pnl"] for j in joined} == {-100.0, 50.0}


def test_a_trade_still_open_is_dropped_not_counted_as_zero():
    rows = [{"candidate": "c0", "prompt_sha256": "h1", "shadow_scale": 0.3}]
    led = [{"type": "decision", "prompt_sha256": "h1", "trade_id": "t1"}]
    assert scorer.join_outcomes(rows, led) == []


def test_discrimination_is_negative_when_the_judge_disliked_the_losers():
    """The headline number. A judge with signal marks down the trades that
    lose, so mean(disliked) - mean(liked) goes NEGATIVE."""
    joined = [{"candidate": "c", "shadow_scale": 0.2, "pnl": -100.0}
              for _ in range(5)]
    joined += [{"candidate": "c", "shadow_scale": 0.9, "pnl": 100.0}
               for _ in range(5)]
    s = scorer.score_against_outcomes(joined, min_n=10)["c"]
    assert s["discrimination"] == -200.0
    assert s["mean_pnl_disliked"] == -100.0 and s["mean_pnl_liked"] == 100.0


def test_too_few_rows_is_none_not_zero_discrimination():
    """"not enough data" and "no discrimination" are opposite findings."""
    joined = [{"candidate": "c", "shadow_scale": 0.5, "pnl": 1.0}]
    s = scorer.score_against_outcomes(joined, min_n=10)["c"]
    assert s["n"] == 1
    assert s["discrimination"] is None
