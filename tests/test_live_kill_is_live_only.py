"""The live kill is a LIVE rail. The simulator has never modelled it — #23.

This file does not close divergence #23. It makes it falsifiable, which is a
different thing and the register is explicit about the difference: "a divergence
is CLOSED only when a test would fail if it reopened."

What would close #23 is the simulator applying the rule, or a measurement of
what applying it would cost. What this file does is stop the register drifting
from the code in silence — in BOTH directions, because a one-sided assertion
would pass just as happily if `live_kill_blocked` were deleted outright.
"""
import ast
import re
from pathlib import Path

import risk

SRC = Path(risk.__file__).parent
REGISTER = SRC.parent / "docs" / "divergences.md"


def _calls(path: Path) -> set[str]:
    """Every function name called anywhere in a module."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (fn.attr if isinstance(fn, ast.Attribute)
                    else fn.id if isinstance(fn, ast.Name) else None)
            if name:
                out.add(name)
    return out


def test_the_simulator_does_not_apply_the_live_kill():
    """One half of #23. An AST scan, not a grep, so a call inside a comment or
    a string cannot satisfy it and a real call cannot hide from it."""
    assert "live_kill_blocked" not in _calls(SRC / "backtest.py"), (
        "src/backtest.py now calls live_kill_blocked. Divergence #23 says it "
        "does not — if that is deliberate, CLOSE #23 in docs/divergences.md "
        "rather than deleting this test, and re-run the frozen gates: tsmom is "
        "one of three enabled strategies in every ensemble baseline arm, so "
        "the baseline of essentially every registration moves.")


def test_the_live_path_does_apply_it():
    """The other half. Without this, deleting `live_kill_blocked` entirely
    would make the test above pass — a green suite reporting that a divergence
    is intact when the rail on the live side of it no longer exists."""
    for module in ("main.py", "swing_scan.py"):
        assert "live_kill_blocked" in _calls(SRC / module), (
            f"src/{module} no longer calls live_kill_blocked. The rail that "
            f"retired tsmom's entries on 2026-08-20 is gone from the live "
            f"path, and divergence #23 describes a gap that no longer has two "
            f"sides.")


def test_the_kill_is_named_on_both_live_raise_sites():
    """It logged fifteen production rejections as `unattributed` before
    2026-08-23 (the BIZON's ledger — this repo's copy froze at the 2026-08-20
    cutover and greps zero). `tests/test_block_census.py` forbids bare raises
    generally; this pins THIS rail's name, which the register entry cites."""
    for module in ("main.py", "swing_scan.py"):
        src = (SRC / module).read_text()
        assert 'rail="live_kill"' in src, (
            f"src/{module} raises the live kill without naming the rail")
    assert "live_kill" in risk.NON_PURE_RAILS


def test_the_register_still_lists_23_as_open():
    """Ties the code fact to the document. #21 was registered with a full
    section and no table row, and every count in the repo agreed with every
    other count for days — the failure this half exists to prevent."""
    text = REGISTER.read_text()
    assert re.search(r"^## #23", text, re.M), "#23 has no section in the register"
    assert re.search(r"^\| 23 \| ", text, re.M), "#23 has no summary-table row"
    m = re.search(r"\*\*Open as of [^:]+: ([^*]+)\*\*", text)
    assert m, "the `Open as of` line is gone from docs/divergences.md"
    assert "#23" in m.group(1), (
        "#23 is no longer listed open. If it was closed, this test should have "
        "been rewritten to assert the closing test — not left asserting a "
        "status the file no longer claims.")
