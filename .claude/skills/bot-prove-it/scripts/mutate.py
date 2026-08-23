#!/usr/bin/env python3
"""Run a mutation and report CAUGHT / SURVIVED by counting, not by scraping.

Why this is Python and not a shell loop
--------------------------------------
The bash version of this misreported twice in one session: it read a line
position out of pytest's tail and called CAUGHT mutations SURVIVED. A tool whose
whole job is to tell you your tests are weak must not fail in the direction of
"your tests are weak" — every result it gave had to be re-verified by hand.

So this counts outcomes explicitly, verifies the restore is byte-identical, and
refuses to report anything if the baseline was not green to begin with.

This tool has produced a confident wrong answer three times, each in a
different way, and each fix lives in the code below rather than in a note:

  * 2026-08-06  a mutation that removes a timeout makes its test HANG, not
                fail. With no deadline the harness sat 45 minutes and reported
                nothing. See `--timeout` and the WEDGED verdict.
  * 2026-08-07  a same-size mutation restored inside one second leaves the
                MUTANT in `__pycache__` while md5 says "restored". See
                `purge_pycache`.
  * 2026-08-23  `--expect` naming a file that did not exist printed CAUGHT.
                See the control run and the `named_code == 1` check.

Usage
-----
    python scripts/mutate.py --file src/health.py \\
        --find 'astimezone(ZoneInfo("America/New_York"))' \\
        --replace 'astimezone(timezone.utc)' \\
        --expect tests/test_cycle_completion.py

`--expect` is the test file (or -k expression) that MUST go red. Naming it is
the point: "some test failed" is not evidence that the right one did.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

# pytest's own exit codes are 0-5, so a negative sentinel cannot collide with
# one. A timeout is NOT an exit code and must never be compared as if it were.
TIMED_OUT = -99

# Directories whose bytecode is not ours and whose purge would cost minutes.
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", ".mypy_cache", ".ruff_cache"}


def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def purge_pycache(root: Path) -> int:
    """Delete every `__pycache__` under `root`, skipping vendored trees.

    2026-08-07, trading-agent PR #105: mutation M10 changed `>= 3` to `>= 2` —
    the SAME number of bytes. CPython invalidates a `.pyc` on (source mtime,
    source size); restoring within the same one-second tick with an unchanged
    size leaves both fields matching the mutant, so every later run executed
    MUTATED bytecode. It announced itself as the CONTROL coming back CAUGHT.

    The md5 proof reads the source. The interpreter does not have to agree.
    """
    removed = 0
    for d in root.rglob("__pycache__"):
        if SKIP_DIRS & set(d.parts):
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed += 1
    return removed


def pytest(*args: str, timeout: float | None = None) -> tuple[int, str]:
    """Run pytest. Returns (TIMED_OUT, partial output) if it blew the deadline."""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", *args],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        def _text(v: bytes | str | None) -> str:
            if v is None:
                return ""
            return v if isinstance(v, str) else v.decode("utf-8", "replace")
        return TIMED_OUT, _text(e.stdout) + _text(e.stderr)
    return r.returncode, r.stdout + r.stderr


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", required=True)
    p.add_argument("--find", required=True)
    p.add_argument("--replace", required=True)
    p.add_argument("--expect", required=True,
                   help="test path or -k expression that must fail")
    p.add_argument("--suite", default="tests/")
    p.add_argument("--timeout", type=float, default=None,
                   help="seconds before a mutated run is called WEDGED. "
                        "Default: derived from the measured baseline, so a "
                        "merely slow suite cannot trip it.")
    p.add_argument("--timeout-factor", type=float, default=4.0,
                   help="deadline = factor x measured baseline (default 4)")
    p.add_argument("--min-timeout", type=float, default=120.0,
                   help="floor for the derived deadline, seconds (default 120)")
    args = p.parse_args()

    root = Path.cwd()
    before = digest(args.file)
    with open(args.file) as f:
        original = f.read()

    if args.find not in original:
        print(f"REFUSING: {args.find!r} not found in {args.file}")
        return 2
    if original.count(args.find) > 1:
        print(f"REFUSING: {args.find!r} appears "
              f"{original.count(args.find)} times — make it unique")
        return 2

    # A mutation result is meaningless if the suite was already red.
    #
    # Time it, too: the deadline for the MUTATED runs is derived from this
    # number rather than hard-coded, so a slow suite cannot produce a false
    # WEDGED and a fast one still gets a tight deadline.
    purge_pycache(root)
    t0 = time.monotonic()
    code, _ = pytest(args.suite)
    baseline_secs = time.monotonic() - t0
    if code != 0:
        print("REFUSING: baseline suite is not green. Fix that first.")
        return 2

    deadline = args.timeout or max(args.min_timeout,
                                   args.timeout_factor * baseline_secs)
    print(f"baseline green in {baseline_secs:.1f}s — "
          f"mutated runs get {deadline:.0f}s before WEDGED")

    # ...and equally meaningless if --expect names something that cannot RUN.
    #
    # 2026-08-23: this tool reported "CAUGHT — tests/test_backup_mirror.py went
    # red, as required" for a file that DID NOT EXIST. pytest exits non-zero
    # for a missing path (4) and for collecting nothing (5) exactly as it does
    # for a real failure (1), and the only check was `!= 0`. A typo in
    # --expect therefore produced a proof of nothing, phrased as a proof.
    #
    # This tool's whole docstring is about not failing in the direction of
    # "your tests are weak". This was the opposite and worse: failing in the
    # direction of false confidence, which is the one no one re-checks.
    control_code, control_out = pytest(args.expect, timeout=deadline)
    if control_code != 0:
        why = ("timed out on the UNMUTATED code" if control_code == TIMED_OUT
               else f"pytest exit {control_code}")
        print(f"REFUSING: --expect {args.expect!r} does not pass on the "
              f"UNMUTATED code ({why}). A target that "
              f"cannot run cannot go red for the right reason.")
        print(control_out[-800:])
        return 2

    try:
        with open(args.file, "w") as f:
            f.write(original.replace(args.find, args.replace))
        purge_pycache(root)          # the mutant must reach the interpreter
        named_code, named_out = pytest(args.expect, timeout=deadline)
        suite_code, _ = pytest(args.suite, timeout=deadline)
    finally:
        with open(args.file, "w") as f:
            f.write(original)
        purge_pycache(root)          # ...and must not outlive the restore

    after = digest(args.file)
    if after != before:
        print(f"RESTORE FAILED: {before} -> {after}")
        return 2

    print(f"restore verified byte-identical (md5 {after[:12]}…)")

    # A HANG IS ITS OWN VERDICT. The mutation that removes a timeout does not
    # make its test fail — it makes it never return, and the assertion never
    # runs. Reported as CAUGHT that is a lie; reported as SURVIVED it is also
    # a lie. A harness with no deadline cannot tell CAUGHT from WEDGED.
    if named_code == TIMED_OUT:
        print(f"WEDGED — {args.expect} did not finish within {deadline:.0f}s. "
              f"This is NOT a caught mutation and NOT a surviving one. The "
              f"usual cause is that the mutation removed a bound (a timeout, "
              f"a retry cap, a loop guard), so the test now blocks forever "
              f"instead of failing. Assert the call ENDED, off-thread, rather "
              f"than calling it directly.")
        print(named_out[-1200:])
        return 2

    # EXIT 1 SPECIFICALLY — "tests ran and failed". Not merely non-zero:
    # 4 is a bad path and 5 is an empty collection, and both used to print
    # CAUGHT. The control run above should already have excluded them, but a
    # mutation can itself break collection (a syntax error in the target
    # module), and that is a broken mutation, not a caught one.
    if named_code == 1:
        print(f"CAUGHT — {args.expect} went red, as required")
        return 0
    if named_code != 0:
        print(f"INCONCLUSIVE: {args.expect} exited {named_code} — it did not "
              f"run and fail, it failed to run. Usually the mutation broke "
              f"collection (syntax error) rather than tripping an assertion.")
        print(named_out[-1200:])
        return 2

    print(f"SURVIVED — {args.expect} still passes with the code broken.")
    if suite_code == TIMED_OUT:
        print("  (the rest of the suite WEDGED — treat this SURVIVED as "
              "unproven until that is explained)")
    elif suite_code != 0:
        print("  (something ELSE in the suite failed — that is not the same "
              "thing, and does not count)")
    print(named_out[-1500:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
