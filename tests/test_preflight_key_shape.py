"""A key that is present is not a key that works.

Why this file exists
--------------------
On 2026-07-27 the owner set `ANTHROPIC_API_KEY` by hand through a shell prompt
with echo disabled. Because nothing appeared on screen, the paste was repeated.
`.env` finished with FIVE `ANTHROPIC_API_KEY` lines:

    line  9: length 0
    line 38: length 148, 0 prefixes
    line 39: length 324, 3 prefixes
    line 40: length 16,  0 prefixes
    line 41: length 216, 2 prefixes     <- python-dotenv takes the last one

So the agent held two keys concatenated. `preflight.run()` printed CLEAR TO
TRADE, because the check added the previous day asked only whether the variable
was set — `os.environ.get("ANTHROPIC_API_KEY")` is truthy for any string.

The failure was not going to be silent: `llm.py:112` catches the auth error and
returns `{**fallback, "degraded": ...}`, and `evidence.py` counts degraded
entries as unjudged. But that report arrives AFTER the 15:45 cycle has placed
entries at full size with no judge. A check that fires after the trade is a
record, not a rail.

The tests below are boundary pairs, in the style of test_rails_are_live.py:
every rejected value sits next to an accepted one differing in exactly the
property under test, so they pin the threshold and the check's existence at the
same time.

None of the strings here are credentials. They are 'x' padding around a real
prefix.
"""
import preflight

# One well-formed key. 13 + 80 = 93 chars, comfortably inside the band.
GOOD = "sk-ant-api03-" + "x" * 80


def _fails_for(key, monkeypatch, shipped_llm_on=True):
    """Run preflight against a minimal config with the judge switched on."""
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    if key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    cfg = {
        "mode": "paper",
        "risk": {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
                 "daily_loss_limit_pct": 3.0, "min_holding_days": 2,
                 "max_order_value_usd": 0, "max_trades_per_day": 15},
        "strategy": {"timeframe": "1Day"},
        "llm": {"enabled": shipped_llm_on},
        "learning": {"regime": {"sma_period": 200, "vol_period": 20}},
        "memory": {"ledger_path": "memory/does-not-exist.jsonl"},
    }
    return [f for f in preflight.run(cfg) if "ANTHROPIC_API_KEY" in f]


# ---- the shape function, in isolation ----

def test_a_single_well_formed_key_has_no_shape_complaint():
    assert preflight.anthropic_key_shape_fail(GOOD) is None


def test_two_keys_concatenated_are_rejected_and_the_count_is_named():
    """Line 41 of the real .env, reproduced: two keys end to end."""
    doubled = GOOD + GOOD
    why = preflight.anthropic_key_shape_fail(doubled)
    assert why is not None
    assert "2" in why, f"the message should name the prefix count: {why!r}"


def test_three_keys_concatenated_are_rejected():
    """Line 39 of the real .env held three."""
    why = preflight.anthropic_key_shape_fail(GOOD * 3)
    assert why is not None and "3" in why


def test_a_value_without_the_prefix_is_rejected():
    """`test-anthropic` — the old test fixture — is not a key."""
    assert preflight.anthropic_key_shape_fail("test-anthropic") is not None
    assert preflight.anthropic_key_shape_fail("x" * 100) is not None


def test_a_truncated_value_is_rejected_but_a_full_one_is_not():
    """Boundary pair: same prefix, only the length differs."""
    assert preflight.anthropic_key_shape_fail("sk-ant-api03") is not None
    assert preflight.anthropic_key_shape_fail(GOOD) is None


def test_the_length_band_binds_on_both_sides():
    lo = "sk-ant-" + "x" * (preflight._ANTHROPIC_MIN_LEN - 7)
    hi = "sk-ant-" + "x" * (preflight._ANTHROPIC_MAX_LEN - 7)
    assert preflight.anthropic_key_shape_fail(lo) is None
    assert preflight.anthropic_key_shape_fail(hi) is None
    assert preflight.anthropic_key_shape_fail(lo[:-1]) is not None
    assert preflight.anthropic_key_shape_fail(hi + "x") is not None


# ---- the same thing through preflight.run(), which is what main.py calls ----

def test_preflight_refuses_a_doubled_key(monkeypatch):
    """The regression. Before today this returned CLEAR TO TRADE."""
    fails = _fails_for(GOOD + GOOD, monkeypatch)
    assert fails, "a doubled key must stop the cycle before it trades"
    assert "unjudged" in fails[0], (
        "the message must say what the consequence is, not just that a string "
        f"looked odd: {fails[0]!r}")


def test_preflight_accepts_a_well_formed_key(monkeypatch):
    """The permissive half — without it the test above passes if the check
    rejects everything."""
    assert _fails_for(GOOD, monkeypatch) == []


def test_an_absent_key_still_gives_the_original_message(monkeypatch):
    """PR #32's failure text is unchanged; this adds a case, it does not
    replace one. The two are different problems with different fixes."""
    fails = _fails_for(None, monkeypatch)
    assert len(fails) == 1
    assert "is not set" in fails[0]


def test_a_disabled_judge_needs_no_key_at_all(monkeypatch):
    """Trading on rules alone stays a legitimate, deliberate choice."""
    assert _fails_for(None, monkeypatch, shipped_llm_on=False) == []
    assert _fails_for("nonsense", monkeypatch, shipped_llm_on=False) == []


# ---- the rule that outranks all of the above ----

def test_no_failure_message_ever_contains_the_key(monkeypatch):
    """A diagnosis that has to be redacted is the wrong diagnosis.

    log.py's RedactingFormatter would scrub it on the way to a handler, but
    preflight failures are also returned to callers, printed by the owner's
    verification one-liner, and written into the cycle log by main.py:503.
    The value must not be in the string in the first place.
    """
    secretish = "sk-ant-api03-" + "S3CRET" * 15
    for value in (secretish, secretish * 2, "test-anthropic", "sk-ant-api03"):
        why = preflight.anthropic_key_shape_fail(value) or ""
        assert value not in why, f"the message quoted the value: {why!r}"
        assert "S3CRET" not in why
        fails = _fails_for(value, monkeypatch)
        assert value not in "".join(fails)
        assert "S3CRET" not in "".join(fails)
