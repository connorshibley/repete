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
import subprocess
import sys


def digest(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def pytest(*args: str) -> tuple[int, str]:
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", *args],
                       capture_output=True, text=True)
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
    args = p.parse_args()

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
    code, _ = pytest(args.suite)
    if code != 0:
        print("REFUSING: baseline suite is not green. Fix that first.")
        return 2

    try:
        with open(args.file, "w") as f:
            f.write(original.replace(args.find, args.replace))
        named_code, named_out = pytest(args.expect)
        suite_code, _ = pytest(args.suite)
    finally:
        with open(args.file, "w") as f:
            f.write(original)

    after = digest(args.file)
    if after != before:
        print(f"RESTORE FAILED: {before} -> {after}")
        return 2

    print(f"restore verified byte-identical (md5 {after[:12]}…)")
    if named_code != 0:
        print(f"CAUGHT — {args.expect} went red, as required")
        return 0

    print(f"SURVIVED — {args.expect} still passes with the code broken.")
    if suite_code != 0:
        print("  (something ELSE in the suite failed — that is not the same "
              "thing, and does not count)")
    print(named_out[-1500:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
