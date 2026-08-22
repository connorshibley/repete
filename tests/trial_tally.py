"""The trial count, read from the register that owns it.

Sibling of `edge_tally.py`, for the same reason that file exists: a number
copied out of the thing that owns it and left to drift is the exact failure
this repo's doc guards keep catching. `knowledge/backtest_candidates.md`
METHOD NOTE 2 makes recording the *running trial count* binding on every new
section, and the last time anyone did was §35 — "~52 registered comparisons
plus grid arms". The prose figure this module pins read "through §11: ~11"
when it was written, against several hundred actual trials.

WHAT COUNTS AS A TRIAL. One *arm run* of one registered spec. Not one spec:
a four-period family is one hypothesis scored four ways, which is how
`edge_tally` reasons about K — but for trial counting the direction is the
opposite. Deflated Sharpe and PBO (Bailey & López de Prado) ask how many
things were *tried*, and every arm was a configuration that got scored. A
spec with a `baseline` and a `candidate` is two trials even though only the
candidate is the claim, because the baseline was run, looked at, and could
have been swapped for a different baseline had it looked wrong.

WHERE THE COUNT COMES FROM. `research/verdicts.jsonl` — tracked in git, and
every row carries an `arms` dict with one summary per arm. That is the only
place the historical arm count exists: `memory/backtest_trials.jsonl` is
gitignored, stops on 2026-07-27, and was never written by `run_gate.py` at
all. So until `research/trials.jsonl` exists and is fed by the runner, the
verdict file IS the trial log, read sideways. When both exist, the delta
between them is the thing to watch — a runner that forgets to log shows up
there, the same way the census's must-balance assertion catches a dropped
signal.

A re-run is a trial. s35, s43d, s48d and s50d each carry two verdict rows at
the same spec hash; each row's arms are counted. Re-running and looking is
not free of selection just because the spec did not change.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
VERDICTS = ROOT / "research" / "verdicts.jsonl"
REGISTRATIONS = ROOT / "research" / "registrations.jsonl"
TRIALS = ROOT / "research" / "trials.jsonl"
CANDIDATES_MD = ROOT / "knowledge" / "backtest_candidates.md"


def _rows(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _section_of(spec_id: str) -> int:
    """s43d -> 43. The numeric section a spec id belongs to."""
    m = re.match(r"s(\d+)", spec_id)
    if not m:
        raise ValueError(f"spec id {spec_id!r} does not start with sNN")
    return int(m.group(1))


def arm_runs_from_verdicts() -> list[dict]:
    """One record per (verdict row, arm). The ground truth for history."""
    out = []
    claims = {r["id"]: (r.get("spec") or {}).get("claim")
              for r in _rows(REGISTRATIONS)}
    for v in _rows(VERDICTS):
        arms = v.get("arms") or {}
        for arm in arms:
            out.append({
                "spec_id": v["id"],
                "section": _section_of(v["id"]),
                "arm": arm,
                "claim": claims.get(v["id"], "UNKNOWN"),
                "ran_at": v.get("ran_at") or v.get("ts"),
            })
    return out


def total_trials() -> int:
    return len(arm_runs_from_verdicts())


def trials_by_section() -> dict[int, int]:
    c: collections.Counter = collections.Counter(
        r["section"] for r in arm_runs_from_verdicts())
    return dict(sorted(c.items()))


def trials_by_claim() -> dict[str, int]:
    c: collections.Counter = collections.Counter(
        r["claim"] for r in arm_runs_from_verdicts())
    return dict(sorted(c.items()))


def trials_through(section: int) -> int:
    """The running count METHOD NOTE 2 asks each section to record: every arm
    run in sections <= this one."""
    return sum(1 for r in arm_runs_from_verdicts() if r["section"] <= section)


def latest_section() -> int:
    return max((r["section"] for r in arm_runs_from_verdicts()), default=0)


def logged_trials() -> int:
    """Rows in research/trials.jsonl — 0 until the runner writes it."""
    return len(_rows(TRIALS))


# The prose line this module pins. It must quote the CURRENT count through
# the CURRENT latest section; anything else is the drift this exists to end.
PROSE_RE = re.compile(
    r"Trial count through §(\d+):\s*([\d,]+)\s+arm runs", re.IGNORECASE)


def stated_in_prose() -> tuple[int, int] | None:
    m = PROSE_RE.search(CANDIDATES_MD.read_text())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2).replace(",", ""))
