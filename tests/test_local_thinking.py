"""The judge's hidden reasoning trace: the switch that decides whether it works.

WHY THIS EXISTS. Flipping `llm.provider` to `local` on 2026-08-28 was, on its
own, not a fix. Measured on the Bizon against the real judge prompt, n=3 each:

    enable_thinking unset -> 47.1s, 2261 completion tokens
    enable_thinking false ->  4.8s,  224 completion tokens, same verdict

`llm.timeout_seconds` is 30. So at 47s EVERY call raised, every raise became a
degradation, and `on_unavailable: block` refused the entries the judge gates —
the exact blocked state the provider switch was meant to end. The bot would
have been running a "working" local judge and still not trading.

The tokens bought nothing an audit can see: `reasoning_content` came back
empty, llm.py slices `{`..`}` out of `content` and drops the surrounding prose,
and the ledger records only the visible `reasoning` field.

WHAT THIS GUARDS. That the request actually carries the field (a resolver that
returns the right dict but is never wired into the body is the failure this
whole file exists to catch), that the tri-state is honoured so a non-vLLM
server can be left alone, and that an extra field can never shadow the model
or the token ceiling.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm_client  # noqa: E402


def _cfg(**over):
    c = {"llm": {"enabled": True, "provider": "local",
                 "base_url": "http://vllm-qwen38:8000/v1",
                 "model": "qwen3.8-27b", "timeout_seconds": 30}}
    c["llm"].update(over)
    return c


# --- the resolver's tri-state -------------------------------------------

def test_unset_sends_nothing():
    """`chat_template_kwargs` is a vLLM extension. A different
    OpenAI-compatible server may reject an unknown field outright, so the
    default must be to stay silent and take the server's own default."""
    assert llm_client.thinking_kwargs(_cfg()) == {}


def test_false_asks_for_no_trace():
    assert llm_client.thinking_kwargs(_cfg(enable_thinking=False)) == {
        "chat_template_kwargs": {"enable_thinking": False}}


def test_true_asks_for_one():
    """The knob has to work in both directions. A resolver that only ever
    turns thinking off is a hardcoded false wearing a config key."""
    assert llm_client.thinking_kwargs(_cfg(enable_thinking=True)) == {
        "chat_template_kwargs": {"enable_thinking": True}}


def test_missing_llm_block_does_not_raise():
    assert llm_client.thinking_kwargs({}) == {}


# --- it reaches the wire ------------------------------------------------

class _Capture:
    """Stands in for urllib, recording the body the adapter actually sent."""

    def __init__(self):
        self.body = None

    def __call__(self, req, timeout=None):
        self.body = json.loads(req.data.decode())

        class _R:
            def __enter__(_s):
                return _s

            def __exit__(*_a):
                return False

            def read(_s):
                return json.dumps({
                    "choices": [{"message": {"content": '{"verdict":"approve"}'},
                                 "finish_reason": "stop"}],
                    "model": "qwen3.8-27b",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }).encode()
        return _R()


def _send(monkeypatch, cfg):
    cap = _Capture()
    monkeypatch.setattr("urllib.request.urlopen", cap)
    client = llm_client._create(cfg)
    client.messages.create(model=cfg["llm"]["model"], max_tokens=4000,
                           system="sys", messages=[{"role": "user",
                                                    "content": "u"}])
    return cap.body


def test_field_is_actually_in_the_request(monkeypatch):
    """The bug this catches: a correct resolver that nothing calls. Asserting
    on thinking_kwargs() alone would pass while every real call still spent
    47s and timed out."""
    body = _send(monkeypatch, _cfg(enable_thinking=False))
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_absent_field_is_absent_from_the_request(monkeypatch):
    body = _send(monkeypatch, _cfg())
    assert "chat_template_kwargs" not in body


def test_extra_body_cannot_shadow_model_or_ceiling(monkeypatch):
    """A config typo must not be able to silently re-point the model or lift
    max_tokens — both are safety-relevant and neither is this knob's business."""
    cap = _Capture()
    monkeypatch.setattr("urllib.request.urlopen", cap)
    client = llm_client._OpenAICompatClient(
        "http://x/v1", 30,
        {"model": "something-else", "max_tokens": 999999, "keep": 1})
    client.messages.create(model="qwen3.8-27b", max_tokens=4000, system="s",
                           messages=[{"role": "user", "content": "u"}])
    assert cap.body["model"] == "qwen3.8-27b"
    assert cap.body["max_tokens"] == 4000
    assert cap.body["keep"] == 1


def test_keyed_provider_ignores_the_knob(monkeypatch):
    """Anthropic has no such parameter; setting it must not reach that client
    or crash constructing it."""
    class _FakeAnthropic:
        def __init__(self, **kw):
            self.kw = kw

    monkeypatch.setitem(sys.modules, "anthropic",
                        type("m", (), {"Anthropic": _FakeAnthropic}))
    cfg = {"llm": {"enabled": True, "provider": "anthropic",
                   "model": "claude-opus-5", "enable_thinking": False}}
    client = llm_client._create(cfg)
    assert isinstance(client, _FakeAnthropic)
    assert "chat_template_kwargs" not in client.kw


# --- the shipped config -------------------------------------------------

def test_shipped_config_turns_thinking_off_under_local():
    """Reads the SHIPPED config, not a fixture. A local provider left on the
    server's default is a 47s judge against a 30s ceiling — i.e. a bot that
    looks configured and cannot enter a trade."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    with open(root / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if llm_client.needs_key(cfg):
        pytest.skip("shipped config is on a keyed provider")
    assert cfg["llm"]["enable_thinking"] is False
    assert llm_client.timeout_seconds(cfg) >= 15


def test_shipped_config_sends_no_vendor_model_to_a_local_endpoint():
    """news.model / learning.model override llm.model on the SAME client. A
    Claude id against the local server is a 400 every hour, not a cheaper
    model — and _json_call swallows it as a warning, so it would fail quietly."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    with open(root / "config.yaml") as f:
        cfg = yaml.safe_load(f)
    if llm_client.needs_key(cfg):
        pytest.skip("shipped config is on a keyed provider")
    served = cfg["llm"]["model"]
    for section in ("news", "learning"):
        override = (cfg.get(section) or {}).get("model")
        assert override in (None, served), (
            f"{section}.model={override!r} would be sent to the local endpoint, "
            f"which serves {served!r}")


# --- the judge entry point, driven for real --------------------------------
#
# ADDED AFTER AN END-TO-END RUN CRASHED. Everything above tests llm_client and
# the shipped config. The first real call to llm.review_signal against the
# local provider raised TypeError from os.environ.get(None): key_env_var()
# returns None for a provider with key_env: None, and _key_present() asked for
# the variable without first asking whether one exists.
#
# All 2,766 tests passed at that moment. Not one of them called review_signal
# on a keyless config — the same gap that let a mutation survive in #151, in a
# different function. A judge that raises on every call is worse than the
# outage it replaced: it fails INSIDE the try that marks degradations, so the
# bot reports a working local judge and blocks every entry anyway.

import llm  # noqa: E402


def _keyless(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return {"llm": {"enabled": True, "provider": "local",
                    "base_url": "http://vllm-qwen38:8000/v1",
                    "model": "qwen3.8-27b", "max_tokens": 100,
                    "timeout_seconds": 30, "on_unavailable": "block"}}


def test_review_signal_does_not_ask_a_keyless_provider_for_a_key(monkeypatch):
    """The crash: os.environ.get(None). Asserted through the public entry
    point, because that is where the None actually arrived."""
    assert llm._key_present(_keyless(monkeypatch)) is True


def test_keyless_judge_is_not_marked_absent_key(monkeypatch):
    """Beyond not crashing: it must not report the keyless provider as an
    absent-key degradation. That marker means "configured ON but unusable",
    and with on_unavailable:block it refuses the entry — the exact blocked
    state this whole change exists to end."""
    cfg = _keyless(monkeypatch)
    sig = type("S", (), {"action": "buy", "symbol": "AAPL", "strategy": "tsmom",
                         "reason": "r", "indicators": {}})()

    captured = {}

    def _fake(cfg_, system, user, max_tokens=None, **kw):
        captured["called"] = True
        return ('{"bull_case":"b","bear_case":"c","verdict":"approve",'
                '"scale":1.0,"confidence":0.6,"reasoning":"r"}',
                {"model": "qwen3.8-27b", "input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(llm.llm_client, "complete_detailed", _fake)
    review = llm.review_signal(sig, "ctx", cfg)

    assert captured.get("called"), (
        "review_signal never reached the model — it short-circuited on a key "
        "that this provider does not use")
    assert review.get("degraded_reason") != "absent_key"
    assert not llm.is_fallback_review(review)
    assert review["verdict"] == "approve"
