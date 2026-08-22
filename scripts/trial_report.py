#!/usr/bin/env python3
"""How many things has this project tried? Printed from the register, not typed.

    python scripts/trial_report.py

Why this exists
---------------
The audit (2026-08-21) could not compute a Deflated Sharpe or a Probability of
Backtest Overfitting for this project, and the reason was not the statistics.
Both need N — the number of configurations tried — and the project could not
say what N was. Declared K was 16; the defensible floor was ~480; the trial
log had stopped on 2026-07-27 because scripts/run_gate.py never wrote to it.

Bonferroni on a pre-declared K is the weakest multiple-testing control there
is, and it only binds if K is honest. Every number here is the input that
makes a stronger control possible.

What it prints, and why each line is there
------------------------------------------
* arm runs from verdicts  — the ground truth, read from research/verdicts.jsonl
* by claim type           — DIAGNOSTIC spends no alpha but IS selection;
                            that is where the declared-K-vs-real-N gap lives
* K in force              — max bonferroni_k over EDGE, from edge_tally
* the delta              — arm runs in verdicts minus rows in the trial log.
                            Zero means the runner logged every trial. Anything
                            else is a runner that forgot, caught the same way
                            the census's must-balance assertion catches a
                            dropped signal.

WHAT THIS CANNOT COUNT, stated rather than hidden
--------------------------------------------------
The register starts at §35. Seven EDGE spends predate it (s35's own prior:
"EDGE claims stand at 0 for 7 entering this test") and every §1–§34 grid arm
was logged — if at all — to memory/backtest_trials.jsonl, which is gitignored,
stopped on 2026-07-27, and whose §1–§13 snapshot is already gone. So the
figure here is a FLOOR on N, not N. The audit's ~480 estimate layered the
untracked log on top of this; this report deliberately does not, because a
count that mixes a tracked source with an untracked one cannot be re-derived
by anyone else.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import edge_tally  # noqa: E402
import trial_tally as tt  # noqa: E402


def main() -> int:
    runs = tt.arm_runs_from_verdicts()
    total = len(runs)
    by_claim = tt.trials_by_claim()
    by_sec = tt.trials_by_section()
    logged = tt.logged_trials()
    k = edge_tally.current_k()

    print(f"arm runs from verdicts : {total}")
    print(f"sections covered       : {len(by_sec)} (§{min(by_sec)}–§{max(by_sec)})")
    print("by claim type          : " + ", ".join(
        f"{c} {n}" for c, n in by_claim.items()))
    print(f"K in force (EDGE only) : {k}")
    print(f"logged in trials.jsonl : {logged}")
    delta = total - logged
    flag = "" if delta == 0 else "   <-- runs the log does not know about"
    print(f"delta (verdicts - log) : {delta}{flag}")
    print()

    edge = by_claim.get("EDGE", 0)
    print(f"Declared K is {k}. Arm runs that could have produced a false positive")
    print(f"are {total}, of which only {edge} are EDGE. The other "
          f"{total - edge} performed selection — choosing which EDGE claim to")
    print("spend budget on — while spending none. That gap is what Deflated")
    print("Sharpe needs to know about and Bonferroni-on-K cannot see.")
    print()
    print("FLOOR, not N: the register starts at §35. Seven EDGE spends and every")
    print("§1–§34 grid arm predate it and live only in an untracked log.")
    print()
    print("by section:")
    line = []
    for sec, n in by_sec.items():
        line.append(f"§{sec}:{n}")
    for i in range(0, len(line), 9):
        print("  " + "  ".join(line[i:i + 9]))

    stated = tt.stated_in_prose()
    print()
    if stated is None:
        print("knowledge/backtest_candidates.md does not state a current count.")
        print(f'Expected a line: "Trial count through §{tt.latest_section()}: '
              f'{total:,} arm runs"')
    else:
        sec, n = stated
        ok = (sec == tt.latest_section() and n == tt.trials_through(sec))
        print(f"prose states: through §{sec}: {n:,}  "
              f"{'OK' if ok else 'STALE — tests/test_trial_count.py will fail'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
