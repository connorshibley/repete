"""The EDGE tally, read from the register that owns it.

Two test files assert that prose in this repo agrees with
`research/registrations.jsonl` about the Bonferroni budget:
`test_skills_are_current.py` for `.claude/skills/*/SKILL.md`, and
`test_doc_counts.py` for the front-door docs. They were written on branches
that were open at the same time and each carried its own copy of
`edge_families`, `K_STATED_RE` and `M_STATED_RE`.

Two definitions that must agree, maintained separately, is the exact failure
both of those guards exist to prevent — a number copied out of the thing that
owns it and left to drift. So they live here once, and the copy that survived
is the stricter one: it matches spelled-out numbers and normalises whitespace,
both of which were found the hard way (see `K_STATED_RE` and `_norm`).
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRATIONS = ROOT / "research" / "registrations.jsonl"
VERDICTS = ROOT / "research" / "verdicts.jsonl"


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def edge_families() -> dict[int, list[str]]:
    """EDGE spec ids grouped by the K they were registered against.

    K is NOT the number of EDGE rows in the register, and asserting that it is
    would be wrong in two directions at once:

    * **Down**: a claim is registered once per *arm*. §43, §44, §50 and §72 are
      four period arms each, all sharing one `bonferroni_k`, because they are
      one hypothesis scored four ways — not four chances at a false positive.
    * **Up**: `bonferroni_k` was already 8 on the first row in the file (s35),
      whose own `prior` reads *"EDGE claims stand at 0 for 7 entering this
      test"*. Seven spends predate the register.

    So K is `max(bonferroni_k)`, which is also the only operator consistent
    with what the register means by it: it only goes up.
    """
    fams: dict[int, list[str]] = collections.defaultdict(list)
    for row in _rows(REGISTRATIONS):
        spec = row.get("spec", {})
        if spec.get("claim") == "EDGE":
            fams[int(spec["bonferroni_k"])].append(row["id"])
    return dict(fams)


def current_k() -> int:
    return max(edge_families())


def passing_family_ceiling() -> int:
    """An upper bound on the `M` of "M passes in N", not an equality.

    Whether a *family* passed is not mechanically derivable, and pretending
    otherwise would put a false precision in a test file. §44 (K=13) had three
    of four arms pass and is REJECTED, because its pre-registered reading rule
    was a conjunction. §72 (K=16) also had three of four and is CONFIRMED,
    because §68 had frozen a 3-of-4 confirmation rule for it beforehand. Both
    rules were written down before the runs, which is what binds them; neither
    lives in the spec as a field a test could read.

    What is checkable is the ceiling: a family with no passing arm under any
    reading rule did not pass. M may sit below this (it does — 2 against a
    ceiling of 3) and never above it. That is the overclaim direction.
    """
    latest = {v["id"]: v for v in _rows(VERDICTS)}
    return sum(any(latest.get(sid, {}).get("passed") for sid in ids)
               for ids in edge_families().values())


_NUM_WORD = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen "
    "twenty".split())}
_W = "|".join(_NUM_WORD)

# `K is 16`, `K = 16`, `2 passes in 16`, `only EDGE pass in 15 claims` — every
# shape the prose in this repo uses to state the budget, in digits AND spelled
# out. The word half is anchored to tally phrasings only and never to a bare
# `in <word>`: several files say "in three of four periods", and a rule loose
# enough to read that as a budget would fire on prose unrelated to K.
#
# The word half is not optional politeness. `docs/superpowers/specs/2026-08-04
# -130-30-long-short-design.md` stated the stale tally as *"the single EDGE pass
# in fifteen claims"* — spelled out, in the sentence carrying the design's whole
# premise — and a digit-only regex walked straight past it while catching the
# `1 pass in 15` forty lines below.
K_STATED_RE = re.compile(
    # `stays`/`remains`/`is now` added 2026-08-23: the guard matched "K is 15"
    # and "K = 15" but NOT "K stays 15", which is what bot-research-recall
    # actually wrote — so it sat at 15 against a register of 16, past a guard
    # built for exactly that drift. A matcher blind to one verb is a guard with
    # a hole shaped like whoever phrased it differently.
    r"\bK (?:is|stays|remains|is now)(?: at)? (?P<a>\d+)\b"
    r"|\bK\s*=\s*(?P<b>\d+)\b"
    r"|(?:\d+ )?pass(?:es)? in (?P<c>\d+)\b"
    r"|\bin (?P<d>\d+) claims\b"
    rf"|\bpass(?:es)? in (?P<e>{_W})\b"
    rf"|\bin (?P<f>{_W}) claims\b"
    rf"|\b(?P<g>{_W}) pre-registered EDGE attempts\b"
    rf"|\bEDGE claims? in (?P<h>{_W})\b", re.I)

_K_GROUPS = ("a", "b", "c", "d", "e", "f", "g", "h")

# The `M` half of "M passes in N".
M_STATED_RE = re.compile(r"\b(?P<m>\d+) pass(?:es)? in \d+\b")


def _norm(text: str) -> str:
    """Collapse whitespace before matching.

    Every doc here is hard-wrapped at ~79 columns, so a tally sentence breaks
    across lines wherever it happens to land. Matching raw text made the guard's
    coverage a function of line width: `1 pass in 15` was caught and
    `1 pass in\\n15` was invisible. That is worse than an uneven guard — it is
    one whose gaps move every time somebody reflows a paragraph.
    """
    return re.sub(r"\s+", " ", text)


def stated_ks(text: str) -> set[int]:
    out = set()
    for m in K_STATED_RE.finditer(_norm(text)):
        for g in _K_GROUPS:
            v = m.group(g)
            if v is not None:
                out.add(int(v) if v.isdigit() else _NUM_WORD[v.lower()])
    return out


def stated_ms(text: str) -> set[int]:
    return {int(m.group("m")) for m in M_STATED_RE.finditer(_norm(text))}


def tracked_markdown() -> list[pathlib.Path]:
    """Every markdown file GIT TRACKS. Not every markdown file on disk.

    This asks git rather than walking the filesystem, and the difference is not
    cosmetic. The first version globbed `ROOT.rglob("*.md")` behind a hardcoded
    skip-list of build directories, which walked straight into `.worktrees/` —
    gitignored (`.gitignore:32`) and untracked, but very much present on any
    machine doing worktree-isolated work. A `qa-sweep` worktree pinned at an
    older commit contributed **seven** unclassified files still stating the old
    K, so the guard failed on that laptop and passed in CI, which does a clean
    checkout with no worktrees.

    A guard that disagrees with itself depending on where it runs is worse than
    no guard: green in CI is the reading people trust. And the fix is not to add
    `.worktrees` to the skip-list — a hand-maintained list of places to ignore
    is the same shape as the bug this exists to prevent, one level up.
    `git ls-files` cannot drift from what the repo contains, because it IS what
    the repo contains.
    """
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "*.md"],
                         capture_output=True, text=True, check=True).stdout
    return sorted(ROOT / rel for rel in out.split("\0") if rel)
