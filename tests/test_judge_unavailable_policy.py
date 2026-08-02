"""When the judge cannot judge, what happens to the trade? (2026-08-02)

Why this file exists
--------------------
`review_signal`'s fallback approves at FULL SIZE. That is a deliberate,
documented choice — every result in knowledge/backtest_candidates.md was
produced under it, and the judge has never been evidenced to add edge, so an
unjudged trade is not a known-worse trade.

But it is a choice the owner should be able to make, not one baked in. The
judge's ONLY permitted effect is to shrink risk (veto or downsize), so an
outage removes a risk-reducer and nothing else. `llm.on_unavailable: block`
lets an owner say "I would rather the book stand still than trade
unsupervised."

Three properties are pinned here, and the third is the one that would actually
hurt if it were wrong:

  1. `approve` (the default) is byte-for-byte the old behaviour — no silent
     trading change for anyone who does not opt in.
  2. `block` marks every unavailability class, so main.py can refuse the entry.
  3. **Exits are never blocked.** Blocking a sell because the reviewer is down
     would TRAP an open position — enlarging risk through the reviewer's
     absence, the exact inversion invariant 2 exists to prevent. That is
     enforced in main.py (`sig.action == "buy"`), and asserted here against the
     real source rather than by re-implementing the condition.
"""
import os
import re
import sys
import types

import llm


class _Sig:
    action, symbol, reason, indicators = "buy", "SPY", "momentum", {}


GOOD_KEY = "sk-ant-api03-" + "x" * 80


def _cfg(**llm_over):
    base = {"enabled": True, "model": "claude-sonnet-5", "max_tokens": 500}
    base.update(llm_over)
    return {"llm": base}


def _stub_outage(monkeypatch, exc=RuntimeError("vendor down")):
    class FakeMessages:
        def create(self, **kw):
            raise exc

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(Anthropic=lambda **_kw: types.SimpleNamespace(
            messages=FakeMessages())))


def _stub_reply(monkeypatch, text):
    class FakeMessages:
        def create(self, **kw):
            return types.SimpleNamespace(
                content=[types.SimpleNamespace(type="text", text=text)])

    monkeypatch.setitem(
        sys.modules, "anthropic",
        types.SimpleNamespace(Anthropic=lambda **_kw: types.SimpleNamespace(
            messages=FakeMessages())))


# ---- the policy reader ----------------------------------------------------

def test_the_default_is_approve_so_nothing_changes_without_opting_in():
    assert llm.unavailable_policy({}) == "approve"
    assert llm.unavailable_policy({"llm": {}}) == "approve"
    assert llm.unavailable_policy(_cfg()) == "approve"


def test_block_is_read_when_configured():
    assert llm.unavailable_policy(_cfg(on_unavailable="block")) == "block"
    assert llm.unavailable_policy(_cfg(on_unavailable="BLOCK")) == "block"
    assert llm.unavailable_policy(_cfg(on_unavailable=" block ")) == "block"


def test_a_typo_falls_back_to_approve_rather_than_crashing_the_cycle():
    """A misspelled policy must not take the 15:45 cycle down. Preflight is
    where a typo gets reported; the cycle keeps its documented default."""
    for bad in ("blok", "halt", "", None, 5, []):
        assert llm.unavailable_policy(_cfg(on_unavailable=bad)) == "approve", bad


# ---- approve: the historical behaviour, unchanged --------------------------

def test_approve_policy_never_sets_the_block_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_outage(monkeypatch)
    out = llm.review_signal(_Sig(), "", _cfg())
    assert out["verdict"] == "approve"
    assert out["scale"] == 1.0
    assert out["degraded_reason"] == "api"
    assert out.get("unavailable_block") is False


# ---- block: every unavailability class is marked ---------------------------

def test_block_marks_an_api_outage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_outage(monkeypatch)
    out = llm.review_signal(_Sig(), "", _cfg(on_unavailable="block"))
    assert out["degraded_reason"] == "api"
    assert out["unavailable_block"] is True


def test_block_marks_an_absent_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = llm.review_signal(_Sig(), "", _cfg(on_unavailable="block"))
    assert out["degraded_reason"] == "absent_key"
    assert out["unavailable_block"] is True


def test_block_marks_an_unparseable_reply(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_reply(monkeypatch, "I am prose, not JSON.")
    out = llm.review_signal(_Sig(), "", _cfg(on_unavailable="block"))
    assert out["degraded_reason"] == "parse"
    assert out["unavailable_block"] is True


def test_block_marks_an_unknown_verdict_value(monkeypatch):
    """A reply that parses but names a verdict the judge may not return. Until
    2026-08-02 this returned the bare fallback with NO degraded marker, so an
    unjudged full-size approval recorded as a genuine judge approval."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_reply(monkeypatch, '{"verdict": "obliterate", "scale": 1.0}')
    out = llm.review_signal(_Sig(), "", _cfg(on_unavailable="block"))
    assert out["degraded_reason"] == "parse"
    assert out["unavailable_block"] is True
    assert out["verdict"] == "approve"          # the fallback shape is unchanged


def test_an_unknown_verdict_is_marked_degraded_even_under_approve(monkeypatch):
    """The honesty half, independent of the block policy: it must never record
    as a judgement that happened."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_reply(monkeypatch, '{"verdict": "obliterate", "scale": 1.0}')
    out = llm.review_signal(_Sig(), "", _cfg())
    assert out.get("degraded"), "an unknown verdict must be marked degraded"
    assert out["degraded_reason"] == "parse"


# ---- a genuine verdict is never touched by any of this ---------------------

def test_a_real_verdict_is_unaffected_by_the_block_policy(monkeypatch):
    """The policy must only ever fire on unavailability. A judge that answered
    is a judge that answered."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)
    _stub_reply(monkeypatch, '{"verdict": "downsize", "scale": 0.5}')
    out = llm.review_signal(_Sig(), "", _cfg(on_unavailable="block"))
    assert out["verdict"] == "downsize"
    assert out["scale"] == 0.5
    assert not out.get("degraded")
    assert not out.get("unavailable_block")


def test_a_deliberately_disabled_judge_is_not_an_outage(monkeypatch):
    """`enabled: false` is a choice, not a failure — it must not block trading
    even under the block policy, or switching the judge off would silently
    switch trading off with it."""
    out = llm.review_signal(_Sig(), "",
                            _cfg(enabled=False, on_unavailable="block"))
    assert out["verdict"] == "approve"
    assert not out.get("degraded")
    assert not out.get("unavailable_block")


# ---- the asymmetry that would actually hurt --------------------------------

def test_main_blocks_only_buys_never_exits():
    """THE LOAD-BEARING TEST. Blocking a sell because the reviewer is down
    would trap an open position and ENLARGE risk through the reviewer's
    absence — the precise inversion invariant 2 exists to prevent.

    Asserted against main.py's real source rather than by re-implementing the
    condition here: a test that restates the logic it is checking passes
    whenever the restatement is wrong in the same direction.
    """
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "main.py")).read()
    guard = re.search(
        r'if\s+sig\.action\s*==\s*"buy"\s+and\s+review\.get\(\s*"unavailable_block"\s*\)',
        src)
    assert guard, ("main.py must gate the unavailable-block refusal on "
                   "sig.action == 'buy'; an unguarded block would trap exits")


def test_the_block_is_ledgered_as_degraded_not_as_a_veto():
    """The judge vetoed nothing — it was never reached. Recording this as a
    veto would attribute a decision to a model that never made one, and every
    calibration measured off the judgment ledger would inherit the lie."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "main.py")).read()
    idx = src.find('review.get("unavailable_block")')
    assert idx != -1, "the unavailable-block path is missing from main.py"
    window = src[idx:idx + 1200]
    assert 'kind="degraded"' in window, (
        "the unavailable-block path must ledger kind='degraded'")
    assert '"veto"' not in window.split("log.warning")[0], (
        "the unavailable-block path must not record a judge veto")


# ---- preflight reports what the runtime tolerates --------------------------
#
# The runtime deliberately falls back rather than crashing the 15:45 cycle on a
# typo. That tolerance is exactly why preflight must SAY something: an owner who
# typed `blok` believing they had stopped unsupervised trading would otherwise
# get the behaviour they were switching off, with no symptom anywhere.

def _preflight_cfg(**llm_over):
    base = {"enabled": True, "provider": "anthropic", "model": "claude-sonnet-5",
            "max_tokens": 500}
    base.update(llm_over)
    return {
        "mode": "paper",
        "risk": {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
                 "daily_loss_limit_pct": 3.0, "min_holding_days": 2,
                 "max_order_value_usd": 0, "max_trades_per_day": 15},
        "strategy": {"timeframe": "1Day"},
        "llm": base,
        "learning": {"regime": {"sma_period": 200, "vol_period": 20}},
        "memory": {"ledger_path": "memory/does-not-exist.jsonl"},
    }


def _env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ANTHROPIC_API_KEY", GOOD_KEY)


def test_preflight_reports_a_misspelled_policy(monkeypatch):
    import preflight
    _env(monkeypatch)
    fails = preflight.run(_preflight_cfg(on_unavailable="blok"))
    assert any("on_unavailable" in f for f in fails), fails


def test_preflight_accepts_both_real_policies(monkeypatch):
    import preflight
    _env(monkeypatch)
    for good in ("approve", "block", None):
        cfg = _preflight_cfg() if good is None else _preflight_cfg(
            on_unavailable=good)
        fails = preflight.run(cfg)
        assert not any("on_unavailable" in f for f in fails), (good, fails)


def test_preflight_reports_a_nonpositive_timeout(monkeypatch):
    import preflight
    _env(monkeypatch)
    for bad in (0, -5, "abc"):
        fails = preflight.run(_preflight_cfg(timeout_seconds=bad))
        assert any("timeout_seconds" in f for f in fails), (bad, fails)


def test_preflight_accepts_a_sane_timeout(monkeypatch):
    import preflight
    _env(monkeypatch)
    for good in (30, 5.5, "12"):
        fails = preflight.run(_preflight_cfg(timeout_seconds=good))
        assert not any("timeout_seconds" in f for f in fails), (good, fails)
