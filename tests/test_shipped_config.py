"""The config we actually ship must be able to start the bot.

Why this file exists
--------------------
On 2026-07-26 §29 set `max_order_value_usd: 0` to disable the per-order cap,
and taught `risk.py` and `backtest.py` to read 0 as "disabled". Nobody told
`preflight.py`, which still demanded a positive number. From that commit
onward every cycle aborted at `main.py:500` with

    risk.max_order_value_usd missing or not a positive number (0)

and refused to trade. It went unnoticed for a day.

**861 tests were green the whole time.** Every preflight test used the `cfg`
fixture from `tests/conftest.py`, which carries `max_order_value_usd: 2000`.
Not one test ever fed the shipped `config.yaml` to the function whose entire
job is deciding whether the shipped config can run.

That is the gap this file closes. It is deliberately not a unit test: it reads
the real file from disk, because the defect lived exactly in the space between
"the fixture is fine" and "the artifact is fine".
"""
import os

import pytest
import yaml

import preflight

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")


@pytest.fixture
def shipped():
    """The real config.yaml, read by absolute path.

    Absolute on purpose: the autouse fixtures chdir into a tmp_path, so a
    relative open() would miss the file and the test would pass vacuously —
    which is the same class of mistake this file exists to catch.
    """
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def test_shipped_config_passes_preflight(shipped, monkeypatch):
    """The one that matters. If this fails, the bot cannot trade at all."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    # Shaped like a real key, because preflight now checks the shape. Was
    # "test-anthropic" until 2026-07-27; that value is exactly the kind of
    # thing the new check exists to reject. Not a credential — 'x' padding.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    fails = preflight.run(shipped)
    assert fails == [], (
        "the shipped config.yaml cannot start a cycle:\n  "
        + "\n  ".join(fails))


def test_preflight_and_risk_agree_on_what_zero_means(shipped):
    """The actual root cause: two modules reading the same key differently.

    `risk.py` treats 0 as "disabled" for these keys. Preflight must not call
    the same value invalid. A disagreement here is silent and total — the
    cycle simply stops running.
    """
    for key in preflight.DISABLEABLE_RISK:
        assert key not in preflight.REQUIRED_POSITIVE_RISK, (
            f"{key} is in both lists — preflight contradicts itself")
    src = open(os.path.join(ROOT, "src", "risk.py")).read()
    for key in preflight.DISABLEABLE_RISK:
        assert key in src, f"{key} is declared disableable but risk.py ignores it"


def test_zero_still_rejected_where_zero_would_disable_a_safety_rail(shipped):
    """0 must NOT be quietly accepted for the rails that protect the account.

    Widening the fix to "any risk number may be 0" would have switched off the
    swing guard and the kill switch by the same edit that unblocked trading.
    """
    for key in ("min_holding_days", "daily_loss_limit_pct",
                "risk_per_trade_pct", "max_position_pct"):
        assert key in preflight.REQUIRED_POSITIVE_RISK
        cfg = {**shipped, "risk": {**shipped["risk"], key: 0}}
        assert any(key in f for f in preflight.run(cfg)), (
            f"{key}=0 must fail preflight — it disables a safety rail")


def test_negative_is_still_a_config_error(shipped):
    """0 means disabled; -1 means someone made a mistake."""
    cfg = {**shipped, "risk": {**shipped["risk"], "max_order_value_usd": -1}}
    assert any("max_order_value_usd" in f for f in preflight.run(cfg))


def test_shipped_config_names_the_key_the_shorting_guard_keys_off(shipped):
    """preflight.run_account_checks refuses shorting only when it finds an
    ENABLED strategy carrying `short_bottom_fraction` — a key that, before
    this test, appeared nowhere but that one check and its own tests: not in
    config.yaml, not in src/strategies/xsmom.py. A later phase is free to
    pick a different key for 'this strategy shorts'; if it does and nobody
    updates the guard, the guard goes dead and STAYS GREEN, because nothing
    forces the key preflight reads to match the key a strategy actually
    sets. Pinning that the shipped xsmom block carries this exact name makes
    that drift fail loudly instead of silently."""
    assert "short_bottom_fraction" in shipped["strategies"]["xsmom"], (
        "preflight.py's shorting guard keys off "
        "strategies.xsmom.short_bottom_fraction, but the shipped config no "
        "longer names it — the guard can never fire and nothing says so")


def test_a_configured_but_absent_judge_fails_preflight(shipped, monkeypatch):
    """`llm.enabled: true` with no key approves every trade unjudged at full
    size, and looks identical to a judged trade in the evidence pack. Claiming
    to have a judge and not having one is a misconfiguration, not a degraded
    mode."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if not shipped.get("llm", {}).get("enabled"):
        pytest.skip("llm disabled in the shipped config")
    assert any("ANTHROPIC_API_KEY" in f for f in preflight.run(shipped))


def test_turning_the_judge_off_is_allowed(shipped, monkeypatch):
    """Trading on rules alone is a legitimate choice — it just has to be one."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = {**shipped, "llm": {**shipped.get("llm", {}), "enabled": False}}
    assert preflight.run(cfg) == []


def test_a_missing_key_is_marked_degraded(monkeypatch):
    """The ledger must record that no judge saw this trade.

    The fallback returns `approve` at scale 1.0 — the most permissive verdict
    the judge can give. Without the marker it is byte-identical to a real
    approval, so the ledger, the calibration scoreboard and the evidence pack
    all count an unjudged trade as a judged one.
    """
    import llm
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    class Sig:
        action, symbol, reason, indicators = "buy", "SPY", "r", {}

    out = llm.review_signal(Sig(), "ctx", {"llm": {"enabled": True}})
    assert out["degraded"] is True
    assert out["scale"] == 1.0          # it really is the permissive verdict
    assert "UNAVAILABLE" in out["reasoning"]

    # Switched off on purpose is NOT degraded — that is a decision, not a hole.
    off = llm.review_signal(Sig(), "ctx", {"llm": {"enabled": False}})
    assert not off.get("degraded")


def test_degraded_review_does_not_count_as_judged(tmp_path):
    """The evidence pack is the executive-facing artifact.

    It previously tested only that an `llm_review` block was PRESENT. The
    degraded fallback is a present block, so a run with no judge configured
    could report `every_entry_judged: pass` — an artifact asserting something
    untrue, which is worse than shipping no artifact.
    """
    import evidence
    judged = {"type": "decision", "executed": True, "action": "buy",
              "trade_id": "t1", "llm_review": {"verdict": "approve",
                                               "scale": 1.0}}
    unjudged = {"type": "decision", "executed": True, "action": "buy",
                "trade_id": "t2", "llm_review": {"verdict": "approve",
                                                 "scale": 1.0,
                                                 "degraded": True}}

    ok = evidence.invariants_check({}, [judged], root=str(tmp_path))
    assert ok["checks"]["every_entry_judged"]["pass"] is True

    bad = evidence.invariants_check({}, [judged, unjudged],
                                    root=str(tmp_path))["checks"]
    assert bad["every_entry_judged"]["pass"] is False, (
        "a degraded fallback must not be counted as a judge verdict")
    assert "1 without a real judge verdict" in bad["every_entry_judged"]["detail"]


def test_shipped_config_is_paper(shipped):
    """Invariant #1, checked against the artifact rather than a fixture."""
    assert shipped.get("mode") == "paper"


def test_shipped_config_keeps_the_swing_timeframe(shipped):
    """Invariant #3. A daily timeframe is what makes this a swing bot rather
    than a day-trading bot."""
    assert shipped["strategy"]["timeframe"] == "1Day"
    assert shipped["risk"]["min_holding_days"] >= 1


def test_the_shipped_judge_does_not_fail_open(shipped):
    """An ENTRY the judge could not judge must not execute at full size.

    THIS TEST EXISTS BECAUSE NOTHING PROTECTED THE VALUE. On 2026-08-21 the
    fail-open default was flipped `approve` -> `block`, and a mutation putting
    it straight back left ALL 2,548 TESTS GREEN. Every existing test drives
    `unavailable_policy` with an explicit `_cfg(on_unavailable=...)`, so the
    behaviour was covered in both directions while the SHIPPED ARTIFACT was
    covered in neither — the exact fixture-versus-artifact gap this file was
    written for, and the same shape as the dashboard badge that was hardcoded
    without a single test going red.

    What the flip is worth: the audit found six live entries taken at full size
    with no risk review, and those six were 100% of the `approve` verdicts on
    record. The judge, on every occasion it was actually reached, never once
    approved at full size.

    If this is ever deliberately reverted, change it here and say why. A pin
    does not forbid the change; it makes it a decision rather than a drift.
    """
    assert shipped["llm"]["on_unavailable"] == "block", (
        "the shipped config must refuse an entry the judge could not judge; "
        "'approve' silently executes unreviewed trades at full size")
