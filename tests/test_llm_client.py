"""The vendor seam: one definition of a key, one call path, no silent swaps.

Why this file exists
--------------------
`llm.py` used to construct `anthropic.Anthropic()` in five places and
`preflight.py` hard-coded the literal `sk-ant-`. Two copies of the same fact is
how §29 happened: preflight and risk.py disagreed about what
`max_order_value_usd: 0` meant, and every cycle silently aborted for a day.

These tests pin three properties:

  1. preflight and the call path read the SAME key rule
  2. the fallback model is OFF unless configured — a quietly weaker judge is a
     trading behaviour change, not a resilience freebie
  3. no vendor SDK is imported at module scope, because preflight imports this
     module and is documented pure

None of the strings here are credentials.
"""
import sys
import types

import pytest

import llm_client
import preflight

GOOD = "sk-ant-api03-" + "x" * 80


class _FakeMessage:
    def __init__(self, text):
        self.content = [types.SimpleNamespace(type="text", text=text)]


def _fake_anthropic(monkeypatch, *, replies=None, fail_models=(),
                    init_kwargs=None):
    """Stub the SDK. `replies` maps model -> text; `fail_models` always raise.

    `init_kwargs`, when a list is passed, collects the kwargs each client was
    constructed with — that is how the timeout assertion below reads what was
    actually handed to the SDK rather than trusting the config.
    """
    calls = []

    class FakeMessages:
        def create(self, model, max_tokens, system, messages):
            calls.append(model)
            if model in fail_models:
                raise RuntimeError(f"model {model} unavailable")
            return _FakeMessage((replies or {}).get(model, f"ok:{model}"))

    class FakeClient:
        def __init__(self, **kw):
            # **kw so the stub keeps accepting client-level options the real
            # SDK takes (timeout= since 2026-08-02). A stub with a stricter
            # signature than the thing it stands in for fails on the change
            # rather than on the defect.
            if init_kwargs is not None:
                init_kwargs.append(kw)
            self.messages = FakeMessages()

    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=FakeClient))
    return calls


def _cfg(**llm):
    base = {"enabled": True, "model": "claude-sonnet-5", "max_tokens": 1000}
    base.update(llm)
    return {"llm": base}


# ---- one definition of a key ----

def test_preflight_and_the_call_path_share_one_key_rule():
    """The §29 property, stated as a test: two readers, one definition."""
    for value in (GOOD, GOOD + GOOD, "test-anthropic", "sk-ant-api03", "x" * 90):
        assert (preflight.anthropic_key_shape_fail(value)
                == llm_client.key_shape_fail(
                    value, llm_client.PROVIDERS["anthropic"])), value


def test_every_declared_provider_has_the_facts_the_checker_needs():
    """A KEYED provider added without a key_env or a prefix would make
    preflight silently skip the check for it.

    Widened 2026-08-23 for the keyless `local` provider. The original loop
    asserted every provider carries key facts, which was right while every
    provider had a key. A self-hosted server authenticates with nothing, and
    the danger it introduces is the mirror image: not a key check skipped by
    accident, but a key check demanded where none can exist — preflight
    refusing the cycle over an absent key, which is failing UNSAFE while
    looking like caution.

    So the rule splits by what the provider IS: keyed providers must carry the
    facts, keyless ones must declare `key_env: None` explicitly rather than
    omitting it. An omitted key_env would read as "forgot to fill this in".
    """
    for name, spec in llm_client.PROVIDERS.items():
        assert "key_env" in spec, f"{name} does not say whether it needs a key"
        if spec["key_env"] is None:
            assert spec["key_prefix"] is None, name
            assert spec["key_len"] is None, name
            continue
        assert spec["key_prefix"], name
        lo, hi = spec["key_len"]
        assert 0 < lo < hi, name


def test_an_unknown_provider_is_reported_not_crashed(monkeypatch):
    """A typo in llm.provider must fail preflight with a readable message, not
    raise KeyError somewhere inside the 15:45 cycle."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    cfg = {
        "mode": "paper",
        "risk": {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
                 "daily_loss_limit_pct": 3.0, "min_holding_days": 2,
                 "max_order_value_usd": 0, "max_trades_per_day": 15},
        "strategy": {"timeframe": "1Day"},
        "llm": {"enabled": True, "provider": "openai"},
        "learning": {"regime": {"sma_period": 200, "vol_period": 20}},
        "memory": {"ledger_path": "memory/does-not-exist.jsonl"},
    }
    fails = preflight.run(cfg)
    assert any("does not implement" in f for f in fails), fails
    assert llm_client.provider_spec(cfg) is llm_client.PROVIDERS["anthropic"]


# ---- the fallback model is opt-in ----

def test_no_fallback_configured_means_the_error_propagates(monkeypatch):
    """Boundary pair, half one: without a fallback the caller sees the failure
    and its own except decides what to do."""
    calls = _fake_anthropic(monkeypatch, fail_models=("claude-sonnet-5",))
    with pytest.raises(RuntimeError):
        llm_client.complete(_cfg(), "sys", "user", max_tokens=10)
    assert calls == ["claude-sonnet-5"], "must not retry when unconfigured"


def test_a_configured_fallback_retries_once_on_the_other_model(monkeypatch):
    """Boundary pair, half two: same failure, one config key different."""
    calls = _fake_anthropic(monkeypatch, fail_models=("claude-sonnet-5",))
    out = llm_client.complete(_cfg(fallback_model="claude-haiku-4-5"),
                              "sys", "user", max_tokens=10)
    assert out == "ok:claude-haiku-4-5"
    assert calls == ["claude-sonnet-5", "claude-haiku-4-5"]


def test_a_fallback_equal_to_the_primary_is_not_a_retry(monkeypatch):
    """Retrying the same model twice is a slower failure, not a fallback."""
    calls = _fake_anthropic(monkeypatch, fail_models=("claude-sonnet-5",))
    with pytest.raises(RuntimeError):
        llm_client.complete(_cfg(fallback_model="claude-sonnet-5"),
                            "sys", "user", max_tokens=10)
    assert calls == ["claude-sonnet-5"]


def test_the_happy_path_never_touches_the_fallback(monkeypatch):
    calls = _fake_anthropic(monkeypatch)
    out = llm_client.complete(_cfg(fallback_model="claude-haiku-4-5"),
                              "sys", "user", max_tokens=10)
    assert out == "ok:claude-sonnet-5"
    assert calls == ["claude-sonnet-5"]


def test_an_unimplemented_provider_refuses_to_call_anything(monkeypatch):
    """`openai` used to be the example of an unimplemented provider. It still
    is — the keyless provider added 2026-08-23 is `local`, a server the
    operator hosts, not a second vendor."""
    _fake_anthropic(monkeypatch)
    with pytest.raises(RuntimeError, match="are implemented"):
        llm_client.complete(_cfg(provider="openai"), "sys", "user",
                            max_tokens=10)


# ---- preflight stays pure ----

def test_importing_llm_client_does_not_import_a_vendor_sdk():
    """preflight.py's docstring promises pure checks and no network. It imports
    llm_client, so llm_client must not drag the SDK in at module scope."""
    src = open(llm_client.__file__).read()
    module_level = [ln for ln in src.splitlines()
                    if ln.startswith("import ") or ln.startswith("from ")]
    assert not any("anthropic" in ln for ln in module_level), module_level


def test_configured_needs_both_the_switch_and_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    assert llm_client.configured(_cfg()) is True
    assert llm_client.configured(_cfg(enabled=False)) is False
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm_client.configured(_cfg()) is False


# ---- the judge call is time-bounded (2026-08-02) --------------------------
#
# The SDK's default timeout is 600s. The judge runs once per actionable signal
# inside a cycle, so an unbounded call is not "slow" — it is a cycle that can
# still be waiting when the market closes. These pin that a bound is actually
# handed to the SDK, not merely written in config.

def test_a_timeout_is_passed_to_the_sdk(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    seen = []
    _fake_anthropic(monkeypatch, init_kwargs=seen)
    llm_client.complete(_cfg(), "sys", "user", max_tokens=10)
    assert seen, "no client was constructed"
    assert seen[0].get("timeout") == llm_client.DEFAULT_TIMEOUT_SECONDS


def test_the_configured_timeout_is_the_one_used(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    seen = []
    _fake_anthropic(monkeypatch, init_kwargs=seen)
    llm_client.complete(_cfg(timeout_seconds=7.5), "sys", "user", max_tokens=10)
    assert seen[0].get("timeout") == 7.5


def test_the_fallback_retry_is_also_time_bounded(monkeypatch):
    """A timeout on the first call and not the retry reads as fixed and is not."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD)
    seen = []
    _fake_anthropic(monkeypatch, fail_models=("primary",), init_kwargs=seen)
    llm_client.complete(_cfg(model="primary", fallback_model="backup"),
                        "sys", "user", max_tokens=10)
    assert seen, "no client was constructed"
    assert all(kw.get("timeout") == llm_client.DEFAULT_TIMEOUT_SECONDS
               for kw in seen), seen


def test_a_nonsense_timeout_cannot_restore_an_unbounded_call(monkeypatch):
    """The failure mode this guard exists for is a typo re-creating a 600s
    hang. Every unusable value must land on the bounded default."""
    for bad in (0, -1, "abc", "", [], {}):
        assert llm_client.timeout_seconds({"llm": {"timeout_seconds": bad}}) \
            == llm_client.DEFAULT_TIMEOUT_SECONDS, bad


def test_an_absent_timeout_still_gets_the_default():
    assert llm_client.timeout_seconds({}) == llm_client.DEFAULT_TIMEOUT_SECONDS
    assert llm_client.timeout_seconds({"llm": {}}) == \
        llm_client.DEFAULT_TIMEOUT_SECONDS
