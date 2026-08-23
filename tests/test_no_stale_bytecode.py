"""Cached bytecode must describe THIS repo.

Why this file exists
--------------------
On 2026-08-23 `~/bots/trading-agent` was renamed to `~/bots/repete`. Hours
later a pytest traceback still named a file under the OLD directory — a path
that no longer existed. 186 of the first 200 cached `.pyc` files in the repo
still recorded it.

Nothing invalidated them. CPython validates a `.pyc` on the source's
(mtime, size), and **renaming a directory changes neither**. That is the same
rule behind the same-size mutation collision in
`.claude/skills/bot-prove-it/scripts/mutate.py`, arriving here at repo scale
instead of one file.

Why it matters more than a cosmetically wrong path: a traceback is the thing
you follow when something is already broken. One naming a directory that does
not exist invites exactly the wrong conclusion — that the file was deleted —
and it arrives at the moment nobody has spare attention to question it. Same
family as the capture hook that logged 171 healthy-looking no-ops: an
instrument that misreports its own location is not evidence.

A rename is rare. Bytecode surviving one is silent. So this is a test rather
than a habit.

Note it reads `co_filename` out of the marshalled code object rather than
scanning the file for path-shaped bytes. The first draft did scan, and failed
on its own docstring: every path named in this prose is a string constant in
this module's own `.pyc`. What the bytecode CLAIMS about its origin and what
it merely CONTAINS are different questions.
"""
from __future__ import annotations

import marshal
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from edge_tally import ROOT                                  # noqa: E402

SKIP = {".venv", "venv", "node_modules", ".git", ".worktrees"}
HEADER = 16          # magic + flags + mtime + size, CPython 3.7+


def co_filenames(blob: bytes) -> set[str]:
    """Every source path this bytecode claims for itself, nested code included."""
    found: set[str] = set()
    stack = [marshal.loads(blob[HEADER:])]
    while stack:
        code = stack.pop()
        found.add(code.co_filename)
        stack.extend(c for c in code.co_consts if hasattr(c, "co_filename"))
    return found


def cached_bytecode() -> list[pathlib.Path]:
    return [p for p in ROOT.rglob("*.pyc") if not SKIP & set(p.parts)]


def test_no_cached_bytecode_claims_to_come_from_outside_this_repo():
    root = str(ROOT)
    offenders: dict[str, set[str]] = {}
    for pyc in cached_bytecode():
        try:
            claimed = co_filenames(pyc.read_bytes())
        except (ValueError, EOFError, TypeError):
            continue          # written by another interpreter; not ours to judge
        foreign = {s for s in claimed if s.startswith("/") and not s.startswith(root)}
        if foreign:
            offenders[str(pyc.relative_to(ROOT))] = foreign
    assert not offenders, (
        "cached bytecode claims to come from a directory this repo is not in. "
        "The usual cause is a rename or move, which changes no source mtime or "
        "size and so invalidates nothing — tracebacks will point at a path that "
        "does not exist. Fix: find . -type d -name __pycache__ "
        f"-not -path './.venv/*' -exec rm -rf {{}} +\n{offenders}")


def test_the_scanner_catches_a_genuinely_stale_pyc(tmp_path):
    """Negative control, and not an optional one: without it the assertion
    above is green on a repo with no `.pyc` at all, on a scanner that returns
    the empty set, and on a read that silently fails."""
    dead = "/Users/connorshibley/bots/a-directory-that-was-renamed/src/main.py"
    code = compile("def f():\n    return 1\n", dead, "exec")
    pyc = tmp_path / "stale.pyc"
    pyc.write_bytes(b"\x00" * HEADER + marshal.dumps(code))

    claimed = co_filenames(pyc.read_bytes())
    assert dead in claimed
    assert not any(s.startswith(str(ROOT)) for s in claimed), (
        "the fabricated path must not look local, or the control proves nothing")


def test_the_scanner_is_looking_at_something(tmp_path):
    """The repo must actually HAVE cached bytecode for the main assertion to
    mean anything — otherwise a clean checkout passes it vacuously."""
    assert cached_bytecode(), (
        "no .pyc found under the repo; the guard above proved nothing this run")
