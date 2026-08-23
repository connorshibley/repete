"""The judge's verdict may influence SIZE. Nothing else. Pinned, at last.

WHY THIS FILE EXISTS

`config.yaml:180` states the invariant — "deterministic signals only, LLM never
generates them" — and two of its three halves are enforced in code:

  scale in [0,1]        llm._clamp_scale (src/llm.py:31), tested in
                        tests/test_short_rails.py:545 including the sign flip
  verdict in the set    src/llm.py:234, unknown verdicts fall back MARKED
                        degraded

The third half — the judge cannot choose a SYMBOL, an ACTION, or a PRICE — was
enforced by nothing at all. `main.py` reads eight keys off the review dict and
simply never looks at anything else, so the property held by omission. No JSON
Schema, no TypedDict, no allowlist, and no test that puts `{"symbol": "TSLA"}`
into a verdict and checks it is ignored.

An omission is protective only while nobody adds anything. The deep-agent work
this file precedes is exactly the change that makes the verdict object richer,
which is exactly when "we happen not to read that" stops being a guarantee.

So: write the guard before the feature, not after.
"""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# The complete set main.py is allowed to consume. Deliberately spelled out
# rather than derived: this list is the reviewed decision, and the test below
# is what makes adding a ninth key a deliberate act instead of a silent one.
CONSUMED = {
    "degraded", "degraded_reason", "unavailable_block",
    "verdict", "scale", "reasoning", "cited_lessons", "confidence",
}

# Keys a model could plausibly emit that would REDIRECT a trade rather than
# shrink it. None of these may ever be read.
FORBIDDEN = {
    "symbol", "ticker", "action", "side", "direction",
    "qty", "quantity", "shares", "size",
    "price", "limit", "stop", "stop_price", "target", "take_profit",
    "strategy", "order_type",
}


def _keys_read_from_review(path: Path) -> set[str]:
    """Every literal key read off a local named `review`, by AST.

    A grep would match the word inside docstrings and prompt text — this file
    is full of both — so the scan has to understand the code."""
    keys: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name) and node.value.id == "review"
                and isinstance(node.slice, ast.Constant)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "review"
                and node.args and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


@pytest.mark.parametrize("module", ["main.py", "swing_scan.py"])
def test_the_consumed_key_set_is_closed(module):
    """THE GUARD. A ninth key becomes a deliberate, reviewed act.

    Both entry paths, because swing_scan.py mirrors _process_signal
    step-for-step and a change landed in only one of them is how the two
    diverge in silence — divergence #21 is already one of those."""
    got = _keys_read_from_review(SRC / module)
    assert got, f"found no review[...] reads in {module} — did the parser drift?"
    extra = got - CONSUMED
    assert not extra, (
        f"src/{module} now reads {sorted(extra)} off the judge's verdict. "
        f"If that is intended, add it to CONSUMED here and say why in the "
        f"commit — but check first that it cannot redirect a trade. The judge "
        f"is allowed to shrink an order, never to choose a different one.")


@pytest.mark.parametrize("module", ["main.py", "swing_scan.py"])
def test_the_judge_can_never_name_the_trade(module):
    """The narrower, louder half. `size` and `qty` are in FORBIDDEN on purpose:
    the judge shrinks through `scale`, a MULTIPLIER on a quantity the rails
    computed. A direct quantity would let it size upward past risk.size_order,
    which is a different power wearing a similar name."""
    got = _keys_read_from_review(SRC / module)
    named = got & FORBIDDEN
    assert not named, (
        f"src/{module} reads {sorted(named)} from the judge — that lets the "
        f"model redirect a trade rather than shrink it, and reverses "
        f"config.yaml:180 ('deterministic signals only, LLM never generates "
        f"them').")


def test_the_two_entry_paths_consume_the_same_surface():
    """If they ever disagree, one of them has grown a power the other lacks and
    the ensemble's behaviour depends on which job fired."""
    assert (_keys_read_from_review(SRC / "main.py")
            == _keys_read_from_review(SRC / "swing_scan.py")), (
        "main.py and swing_scan.py no longer read the same keys off the "
        "judge's verdict")


def test_the_forbidden_and_consumed_sets_do_not_overlap():
    """A guard whose own two lists contradicted each other would pass while
    meaning nothing."""
    assert not (CONSUMED & FORBIDDEN)


# --------------------------------------------------------------------------
# The behavioural half. The AST guard above catches the code READING a
# forbidden key; this catches the effect end-to-end, through a real cycle with
# a real order placed against a fake broker. Both are needed: a source scan
# cannot see an indirection, and a behavioural test cannot see a key that is
# read but happens to be ignored today.
# --------------------------------------------------------------------------

import main  # noqa: E402
from conftest import make_bars  # noqa: E402
from test_main_cycle import BUY_CLOSES, FakeCycleBroker, cycle_env  # noqa: E402,F401


def _submitted(broker):
    return [dict(o) for o in broker.submitted]


def test_a_verdict_naming_a_different_trade_changes_nothing(cycle_env, monkeypatch):
    """THE TEST THAT DID NOT EXIST.

    A judge returns a full-size approval AND tries to redirect the trade:
    different symbol, opposite action, its own quantity, its own stop. The
    order that reaches the broker must be identical to the one a bare approval
    produces — the extra keys are noise, not instructions.
    """
    _, install = cycle_env

    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "approve", "scale": 1.0,
                                         "reasoning": "clean"})
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    clean = _submitted(broker)
    assert clean, "the control placed no order — the fixture is not exercising the path"

    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "approve", "scale": 1.0,
                                         "reasoning": "clean",
                                         # everything a model might try
                                         "symbol": "TSLA", "ticker": "TSLA",
                                         "action": "sell", "side": "short",
                                         "qty": 9999, "quantity": 9999,
                                         "size": 9999, "shares": 9999,
                                         "price": 1.0, "limit": 1.0,
                                         "stop": 1.0, "stop_price": 1.0,
                                         "target": 9999.0, "strategy": "other",
                                         "order_type": "limit"})
    broker2 = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    hijacked = _submitted(broker2)

    assert hijacked == clean, (
        "the judge redirected the trade. Its verdict may shrink an order and "
        "nothing else; these keys must be inert.")


def test_a_verdict_cannot_size_up_through_an_extra_key(cycle_env, monkeypatch):
    """`scale` is clamped to <=1. A quantity field would be the way around
    that clamp, so it gets its own test rather than relying on the sweep."""
    _, install = cycle_env

    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "downsize", "scale": 0.5,
                                         "reasoning": "half"})
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    halved = _submitted(broker)

    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "downsize", "scale": 0.5,
                                         "reasoning": "half",
                                         "qty": 100000, "size": 100000})
    broker2 = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    assert _submitted(broker2) == halved, (
        "an extra quantity key moved the order size past the clamped scale")
