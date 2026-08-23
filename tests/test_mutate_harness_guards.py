"""The mutation harness's own failure modes, as tests.

Why this file exists
--------------------
`mutate.py` is load-bearing: every "this test would catch that bug" claim in
the enterprise programme rests on it. It has produced a confident WRONG answer
three separate times, each in a different way:

  * 2026-08-06  a mutation that removes a timeout makes its test HANG rather
                than fail. With no deadline the harness ran 45 minutes and
                reported nothing at all.
  * 2026-08-07  a same-size mutation restored inside one second leaves the
                MUTANT in `__pycache__` while md5 says "restored". Every later
                run then measured mutated bytecode.
  * 2026-08-23  `--expect` naming a file that did not exist printed CAUGHT.

All three are fixed in `mutate.py`. Until this file existed, none of the fixes
was tested — the fix for the third one was verified by hand once, in a
scratch directory, and then trusted. That is the same standard as prose.

So: each guard below is asserted by REPRODUCING the failure it prevents, not
by grepping for the line that prevents it. A test that checks `"timeout=" in
source` would pass against a `timeout=None`.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from edge_tally import ROOT                                  # noqa: E402

MUTATE = ROOT / ".claude" / "skills" / "bot-prove-it" / "scripts" / "mutate.py"


def _load():
    spec = importlib.util.spec_from_file_location("_mutate", MUTATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mutate():
    assert MUTATE.exists(), f"the harness moved: {MUTATE}"
    return _load()


# ---- the 2026-08-07 failure: stale bytecode outlives the restore -----------

def _limit_seen_by_a_fresh_interpreter(cwd: pathlib.Path) -> str:
    r = subprocess.run([sys.executable, "-c", "import bound; print(bound.LIMIT)"],
                       capture_output=True, text=True, cwd=cwd, timeout=60)
    return r.stdout.strip()


def test_purge_pycache_defeats_the_same_size_mtime_collision(mutate, tmp_path):
    """The exact M10 collision, staged deterministically.

    CPython invalidates a `.pyc` on (source mtime, source size). A same-size
    edit restored within one second matches BOTH fields, so the interpreter
    keeps running the mutant while md5 of the source says "restored".
    """
    src = tmp_path / "bound.py"
    src.write_text("LIMIT = 3\n")
    assert _limit_seen_by_a_fresh_interpreter(tmp_path) == "3"
    st = os.stat(src)

    # same byte count, and mtime pinned back to the original
    src.write_text("LIMIT = 2\n")
    os.utime(src, (st.st_atime, st.st_mtime))
    assert len(src.read_text()) == len("LIMIT = 3\n"), "must be the same size"

    stale = _limit_seen_by_a_fresh_interpreter(tmp_path)
    assert stale == "3", (
        "this test no longer reproduces the failure it guards — CPython's "
        "invalidation rule may have changed, or the sizes stopped matching")

    mutate.purge_pycache(tmp_path)
    assert _limit_seen_by_a_fresh_interpreter(tmp_path) == "2", (
        "purge_pycache did not make the mutant reach the interpreter")


def test_the_HARNESS_purges_around_the_mutation_not_just_somewhere(mutate,
                                                                    tmp_path,
                                                                    monkeypatch):
    """The function working is not the same as the flow calling it.

    Found while negative-controlling this file: deleting EVERY
    `purge_pycache(root)` call site from `mutate.py` left all the other tests
    here green, because they exercise the function directly. A guard that is
    present, green and blind is worse than no guard.

    So this asserts POSITION. It records what was on disk at each purge, and
    the sequence must be original -> mutant -> original: once before the
    baseline, once after the mutant is written (or the interpreter never sees
    it), and once after the restore (or the mutant outlives it).
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "bound.py"
    src.write_text("LIMIT = 3\n")

    seen: list[str] = []
    monkeypatch.setattr(mutate, "purge_pycache", lambda root: seen.append(src.read_text()))
    # A faithful stub: the named test is red exactly when the file is mutated,
    # so the control run passes and the mutated run fails, as in a real run.
    def fake_pytest(*a, **k):
        red = a[0] == "tests/x.py" and "LIMIT = 2" in src.read_text()
        return (1 if red else 0), ""
    monkeypatch.setattr(mutate, "pytest", fake_pytest)
    monkeypatch.setattr(sys, "argv", [
        "mutate.py", "--file", "bound.py", "--find", "LIMIT = 3",
        "--replace", "LIMIT = 2", "--expect", "tests/x.py", "--suite", "tests/"])

    assert mutate.main() == 0, "the stubbed run should report CAUGHT"
    assert seen == ["LIMIT = 3\n", "LIMIT = 2\n", "LIMIT = 3\n"], (
        f"purged at the wrong points: {seen}")


def test_purge_pycache_leaves_vendored_bytecode_alone(mutate, tmp_path):
    """Purging `.venv` would cost minutes and buys nothing — that bytecode is
    not ours and is not what any mutation touched."""
    ours = tmp_path / "pkg" / "__pycache__"
    theirs = tmp_path / ".venv" / "lib" / "__pycache__"
    for d in (ours, theirs):
        d.mkdir(parents=True)
        (d / "x.pyc").write_bytes(b"\x00")

    mutate.purge_pycache(tmp_path)
    assert not ours.exists(), "our own bytecode must go"
    assert theirs.exists(), "vendored bytecode must be skipped"


# ---- the 2026-08-06 failure: a hang is not a verdict -----------------------

def test_the_timeout_sentinel_cannot_be_mistaken_for_a_pytest_exit_code(mutate):
    """pytest's exit codes are 0-5. A timeout is not an exit code, and the
    whole bug was treating "non-zero" as "the test failed"."""
    assert mutate.TIMED_OUT < 0
    assert mutate.TIMED_OUT not in range(0, 6)


def test_pytest_helper_reports_a_deadline_breach_instead_of_blocking(mutate,
                                                                    tmp_path,
                                                                    monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_hang.py").write_text(
        "import time\n\n\ndef test_hangs():\n    time.sleep(600)\n")
    code, _ = mutate.pytest("tests/", timeout=4)
    assert code == mutate.TIMED_OUT


def test_a_mutation_that_removes_a_bound_is_WEDGED_not_caught(tmp_path):
    """End to end. The mutation makes the test loop forever; the harness must
    say so in its own word and must NOT claim the mutation was caught."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "bound.py").write_text(
        "LIMIT = 3\n\n\ndef countdown() -> int:\n"
        "    n = 0\n    while n < LIMIT:\n        n += 1\n    return n\n")
    (tmp_path / "tests" / "test_bound.py").write_text(
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from bound import countdown\n\n\n"
        "def test_countdown_reaches_the_limit():\n    assert countdown() == 3\n")
    before = (tmp_path / "bound.py").read_text()

    r = subprocess.run(
        [sys.executable, str(MUTATE), "--file", "bound.py",
         "--find", "while n < LIMIT:", "--replace", "while n < LIMIT or True:",
         "--expect", "tests/test_bound.py", "--suite", "tests/",
         "--timeout", "4"],
        capture_output=True, text=True, cwd=tmp_path, timeout=180)

    assert "WEDGED" in r.stdout, r.stdout[-2000:]
    assert "CAUGHT" not in r.stdout, "a hang reported as a catch is a lie"
    assert "SURVIVED" not in r.stdout, "a hang reported as a survival is also a lie"
    assert r.returncode == 2, "WEDGED is not a pass and not a fail — it is a refusal"

    # Found 2026-08-23 while proving the above: the OLD harness, killed while
    # wedged, left the source MUTATED, because SIGKILL skips `finally`. The
    # new one bounds the CHILD, so its own restore still runs.
    assert (tmp_path / "bound.py").read_text() == before, (
        "a wedged run must still restore the file")
