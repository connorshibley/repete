"""An outage and a bad reply are different failures.

Why this file exists
--------------------
`review_signal` ran the API call and the JSON parse under ONE
`except Exception`, and both returned `{**fallback, "degraded": str(e)}`. So:

  * the vendor being unreachable, and
  * the model replying with prose instead of JSON

wrote byte-identical ledger records. Those want opposite responses — the first
is waited out, the second means the prompt or the model changed underneath you
— and the first time the difference mattered would have been the first time
anyone looked.

A third case, `llm.enabled: true` with no key, set `degraded` to the bare `True`,
which `main.py` then interpolated into the ledger detail as the literal string
"True".

Scale, stated honestly: this has never fired in production. 0 degradation events
across 564 decisions. These tests pin a diagnostic, not a fix for a live bug.

Every case here also re-asserts the thing that must never change: the fallback
approves at FULL SIZE, so it MUST be marked, or the calibration scoreboard
credits the judge for a decision it never made.
"""
import sys
import types

import llm


class _Sig:
    action, symbol, reason, indicators = "buy", "SPY", "momentum", {}


def _cfg():
    return {"llm": {"enabled": True, "model": "claude-sonnet-5",
                    "max_tokens": 500}}


def _stub_reply(monkeypatch, text):
    """Vendor reachable, returns `text`."""
    class FakeMessages:
        def create(self, **kw):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text=text)])

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(
            messages=FakeMessages())))


def _stub_outage(monkeypatch, exc):
    class FakeMessages:
        def create(self, **kw):
            raise exc

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(Anthropic=lambda: types.SimpleNamespace(
            messages=FakeMessages())))


# ---- the three classes, each pinned to its own tag ----

def test_an_api_failure_is_tagged_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    _stub_outage(monkeypatch, RuntimeError("connection reset"))
    out = llm.review_signal(_Sig(), "ctx", _cfg())
    assert out["degraded_reason"] == "api"
    assert "connection reset" in out["degraded"]


def test_an_unparseable_reply_is_tagged_parse(monkeypatch):
    """The vendor answered. The answer was not a verdict."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    _stub_reply(monkeypatch, "I'm sorry, I can't help with trading advice.")
    out = llm.review_signal(_Sig(), "ctx", _cfg())
    assert out["degraded_reason"] == "parse"


def test_a_missing_key_is_tagged_absent_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = llm.review_signal(_Sig(), "ctx", _cfg())
    assert out["degraded_reason"] == "absent_key"


def test_the_three_classes_are_distinguishable(monkeypatch):
    """The whole point, as one assertion: three causes, three tags."""
    key = "sk-ant-api03-" + "x" * 80
    seen = set()

    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    _stub_outage(monkeypatch, RuntimeError("boom"))
    seen.add(llm.review_signal(_Sig(), "c", _cfg())["degraded_reason"])

    _stub_reply(monkeypatch, "not json")
    seen.add(llm.review_signal(_Sig(), "c", _cfg())["degraded_reason"])

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    seen.add(llm.review_signal(_Sig(), "c", _cfg())["degraded_reason"])

    assert seen == {"api", "parse", "absent_key"}


# ---- the properties that must survive the split ----

def test_every_degraded_path_is_still_marked_degraded(monkeypatch):
    """The fallback approves at FULL SIZE. An unmarked degradation is recorded
    as a real approval and the judge is credited for it."""
    key = "sk-ant-api03-" + "x" * 80
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)

    _stub_outage(monkeypatch, RuntimeError("boom"))
    assert llm.review_signal(_Sig(), "c", _cfg())["degraded"]
    _stub_reply(monkeypatch, "not json")
    assert llm.review_signal(_Sig(), "c", _cfg())["degraded"]
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert llm.review_signal(_Sig(), "c", _cfg())["degraded"]


def test_a_good_reply_is_not_degraded_and_carries_no_reason(monkeypatch):
    """The permissive half — without it the tests above pass on a function that
    marks everything degraded."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    _stub_reply(monkeypatch, '{"verdict": "downsize", "scale": 0.5, '
                             '"reasoning": "thin volume", "cited_lessons": []}')
    out = llm.review_signal(_Sig(), "ctx", _cfg())
    assert not out.get("degraded")
    assert "degraded_reason" not in out
    assert out["verdict"] == "downsize" and out["scale"] == 0.5


def test_the_judge_still_cannot_enlarge_a_position(monkeypatch):
    """Invariant 2, re-asserted here because this file touched the parse path."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    _stub_reply(monkeypatch, '{"verdict": "approve", "scale": 3.0, '
                             '"reasoning": "very confident", "cited_lessons": []}')
    assert llm.review_signal(_Sig(), "ctx", _cfg())["scale"] == 1.0


def test_a_disabled_judge_is_not_a_degradation(monkeypatch):
    """Switching the judge off deliberately is a decision, not a failure — it
    must not show up in the degradation SLO."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    out = llm.review_signal(_Sig(), "ctx",
                            {"llm": {"enabled": False, "model": "m",
                                     "max_tokens": 10}})
    assert not out.get("degraded")
    assert "degraded_reason" not in out
