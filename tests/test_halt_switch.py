"""The operator's stop button: does it stop, and does it stop ONLY what it says?

Why this file exists (2026-08-02)
---------------------------------
`risk.check_halt` has always been consulted per-order, so the HALT file was
always a real brake. What did not exist was any way for a human to pull it:
`docs/runbooks.md` documented HALT as something the bot does TO the operator,
and the only written instruction was "delete this file to re-enable" — the
second half of a procedure whose first half was missing.

The tests that matter here are the NEGATIVE ones. A stop button is defined as
much by what it does not touch as by what it does:

  * it must not sell anything (the owner chose halt-only over flatten, so a
    surprise liquidation hiding in here would be the opposite of the decision)
  * it must not be defeated by a ledger write or an alert webhook failing —
    those are the exact things that are also broken during a real incident
  * it must not overwrite the reason on a second call, or the record of WHY
    trading stopped is lost at the moment a second operator needs it

Every test runs in tmp_path. None can touch the repo's real HALT file.
"""
import ast
import importlib.util
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "halt_cli", os.path.join(ROOT, "scripts", "halt.py"))
halt_cli = importlib.util.module_from_spec(_spec)
sys.modules["halt_cli"] = halt_cli
_spec.loader.exec_module(halt_cli)

sys.path.insert(0, os.path.join(ROOT, "src"))
import risk                                                  # noqa: E402


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Run the CLI against a throwaway root. halt.py chdir's to the real repo
    at import time, so every test must move itself back out."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "config.yaml").write_text(
        "memory:\n  ledger_path: memory/ledger.jsonl\n")
    # Neutralise the outward-facing side effects; they are separately tested.
    monkeypatch.setattr(halt_cli, "_load_env", lambda: None)
    monkeypatch.setitem(os.environ, "ALERT_WEBHOOK_URL", "")
    return tmp_path


# ---- it stops -------------------------------------------------------------

def test_engaging_creates_a_halt_the_risk_rail_can_see(sandbox):
    assert risk.check_halt() is False
    assert halt_cli.main(["market", "is", "broken"]) == 0
    assert risk.check_halt() is True, "the rail must see what the CLI wrote"
    assert "MANUAL" in (sandbox / risk.HALT_FILE).read_text()
    assert "market is broken" in (sandbox / risk.HALT_FILE).read_text()


def test_clearing_removes_it(sandbox):
    halt_cli.main(["something"])
    assert risk.check_halt() is True
    assert halt_cli.main(["--clear"]) == 0
    assert risk.check_halt() is False


def test_clearing_when_not_halted_is_a_harmless_noop(sandbox):
    assert halt_cli.main(["--clear"]) == 0
    assert risk.check_halt() is False


def test_status_never_changes_state(sandbox):
    assert halt_cli.main(["--status"]) == 0
    assert risk.check_halt() is False
    halt_cli.main(["reason"])
    assert halt_cli.main(["--status"]) == 0
    assert risk.check_halt() is True, "--status must not clear the halt"


# ---- it refuses to be ambiguous -------------------------------------------

def test_a_halt_with_no_reason_is_refused(sandbox):
    """A halt nobody can explain is one nobody can safely clear: the next
    operator cannot tell a deliberate stop from a stray file."""
    assert halt_cli.main([]) == 2
    assert risk.check_halt() is False, "a refused halt must not half-apply"


def test_a_second_halt_does_not_overwrite_the_first_reason(sandbox):
    """The record of WHY trading stopped is what a second operator needs most,
    and is exactly what a careless re-run would destroy."""
    halt_cli.main(["the", "original", "reason"])
    first = (sandbox / risk.HALT_FILE).read_text()
    assert halt_cli.main(["a", "later", "less", "informed", "reason"]) == 0
    assert (sandbox / risk.HALT_FILE).read_text() == first


def test_status_and_clear_together_are_refused(sandbox):
    assert halt_cli.main(["--status", "--clear"]) == 2


# ---- it is not defeated by the things that break during an incident --------

def test_a_failing_ledger_does_not_prevent_the_halt(sandbox, monkeypatch):
    """A stop button that needs a successful disk write to memory/ is one that
    fails in precisely the conditions it exists for."""
    monkeypatch.delitem(sys.modules, "ledger", raising=False)
    (sandbox / "config.yaml").write_text("not: valid: yaml: {{{")
    assert halt_cli.main(["disk", "is", "unhappy"]) == 0
    assert risk.check_halt() is True


def test_a_failing_alert_does_not_prevent_the_halt(sandbox, monkeypatch):
    import alerting
    monkeypatch.setattr(alerting, "send",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("webhook down")))
    assert halt_cli.main(["alerting", "is", "down"]) == 0
    assert risk.check_halt() is True


# ---- THE LOAD-BEARING NEGATIVE: it must not sell ---------------------------

def test_the_halt_switch_cannot_place_or_cancel_any_order():
    """The owner chose halt-only over flatten (2026-08-02): blocking entries
    stops the bleeding from widening without crystallising every open loss at
    the worst minute of the day. A liquidation hiding inside this script would
    invert that decision silently.

    Walks the AST rather than grepping, so a rename or an aliased import cannot
    smuggle a broker call past the check — the same technique
    test_decaycheck.py uses to prove the decay monitor cannot halt trading.
    """
    tree = ast.parse(open(os.path.join(ROOT, "scripts", "halt.py")).read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "broker" not in imported, (
        "halt.py must not import broker — it blocks entries, it does not trade")

    banned = {"flatten_all", "close_position", "submit_order", "place_order",
              "cancel_order", "liquidate"}
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    called |= {node.func.id for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    leaked = banned & called
    assert not leaked, f"halt.py must never trade; found {leaked}"


def test_the_halt_switch_reuses_risk_engage_halt_rather_than_reimplementing():
    """Two definitions of the file format is how `max_order_value_usd: 0` came
    to mean two different things in two files for a day (§29). The CLI must go
    through risk.engage_halt, not write the file itself."""
    src = open(os.path.join(ROOT, "scripts", "halt.py")).read()
    assert "risk.engage_halt" in src
    tree = ast.parse(src)
    # No `open(HALT..., "w")` anywhere — the only writer is risk.engage_halt.
    writes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "open"
              and any(isinstance(a, ast.Constant) and a.value == "w"
                      for a in n.args)]
    assert not writes, "halt.py must not write the HALT file itself"
