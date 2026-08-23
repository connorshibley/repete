"""Skills must not state facts that have drifted from the thing that owns them.

WHY THIS EXISTS
---------------
Written 2026-08-23 after an audit of the six authored skills found FIVE stale
facts in one sitting, in the highest-quality part of this setup:

  * `bot-research-recall` said "K stays 15" while `bot-pre-registration` said
    "K is 16" and research/specs/*.yaml said 16. Three sources, two answers.
  * `bot-pre-registration` told the agent to work in `~/bots/repete-tv-dev`,
    a directory that does not exist (a bad find-and-replace in the 2026-08-23
    rename).
  * `bot-candidate-intake`'s DESCRIPTION asserted "the project is hands-off",
    reversed 2026-08-10. Descriptions load in every session, every project —
    it is the worst place for a wrong fact to live.
  * Two skills claimed "64 sections" against an actual 67.

A skill is procedural memory: it changes what the agent does in a future
session. A skill with a stale fact is therefore worse than no skill, because
it is confidently wrong at exactly the moment someone is relying on it.

This is the repo's own rule turned on the repo's own memory:

    "A divergence is CLOSED only when a test would fail if it reopened."
        -- .claude/skills/bot-divergence-check/SKILL.md

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
Only facts with a SINGLE OBVIOUS OWNER are checked. If a claim needs judgement
to verify, it does not belong here — a staleness test that needs maintenance
is just more surface to rot.

KILL CRITERION (pre-registered)
-------------------------------
If this test has not caught a real drift by 2026-10-22 (60 days), it is noise
and should be deleted rather than carried. Check `git log` on this file: if
the only commits are its creation and mechanical churn, that is the answer.
"""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")


def _skill_files() -> list[str]:
    if not os.path.isdir(SKILLS):
        return []
    out = []
    for name in sorted(os.listdir(SKILLS)):
        p = os.path.join(SKILLS, name, "SKILL.md")
        if os.path.isfile(p):
            out.append(p)
    return out


SKILL_FILES = _skill_files()
IDS = [os.path.basename(os.path.dirname(p)) for p in SKILL_FILES]


def _read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_there_are_skills_to_check() -> None:
    """The negative control for this whole file. If the skills move and the
    glob silently matches nothing, every test below passes vacuously — which
    is the failure mode that started this."""
    assert SKILL_FILES, f"no SKILL.md found under {SKILLS}"


# ---- owner: research/specs/*.yaml -------------------------------------------

def _register_k() -> int:
    ks = []
    specs = os.path.join(ROOT, "research", "specs")
    for name in os.listdir(specs):
        if not name.endswith((".yaml", ".yml")):
            continue
        for m in re.finditer(r"^\s*bonferroni_k:\s*(\d+)", _read(os.path.join(specs, name)), re.M):
            ks.append(int(m.group(1)))
    assert ks, "no bonferroni_k found in research/specs — the owner moved"
    return max(ks)


@pytest.mark.parametrize("path", SKILL_FILES, ids=IDS)
def test_stated_K_matches_the_register(path: str) -> None:
    """K is owned by research/specs/*.yaml. A skill may restate it; it may not
    disagree with it."""
    owner = _register_k()
    claims = [(int(m.group(2)), m.group(0))
              for m in re.finditer(r"\bK (is|stays)(?: at)? (\d+)", _read(path))]
    bad = [(v, txt) for v, txt in claims if v != owner]
    assert not bad, (
        f"{os.path.basename(os.path.dirname(path))} states {bad} but "
        f"research/specs/*.yaml says bonferroni_k={owner}")


# ---- owner: the filesystem ---------------------------------------------------

@pytest.mark.parametrize("path", SKILL_FILES, ids=IDS)
def test_every_bots_path_a_skill_names_exists(path: str) -> None:
    """A skill that sends the agent to a directory that does not exist wastes
    a session and may silently land work in the wrong checkout."""
    body = _read(path)
    missing = []
    for m in re.finditer(r"~/bots/[A-Za-z0-9._-]+", body):
        rel = m.group(0)
        if not os.path.isdir(os.path.expanduser(rel)):
            missing.append(rel)
    assert not missing, (
        f"{os.path.basename(os.path.dirname(path))} names directories that do "
        f"not exist: {sorted(set(missing))}")


# ---- owner: docs/divergences.md ----------------------------------------------

def _max_divergence() -> int:
    nums = [int(n) for n in re.findall(
        r"^##\s+#(\d+)", _read(os.path.join(ROOT, "docs", "divergences.md")), re.M)]
    assert nums, "no '## #N' entries in docs/divergences.md — the owner moved"
    return max(nums)


@pytest.mark.parametrize("path", SKILL_FILES, ids=IDS)
def test_stated_divergence_number_matches_the_register(path: str) -> None:
    owner = _max_divergence()
    bad = [m.group(0) for m in re.finditer(r"through #(\d+)", _read(path))
           if int(m.group(1)) != owner]
    assert not bad, (
        f"{os.path.basename(os.path.dirname(path))} says {bad} but "
        f"docs/divergences.md ends at #{owner}")


# ---- owner: knowledge/backtest_candidates.md ---------------------------------

def _section_count() -> int:
    n = len(re.findall(r"^##\s+§\d+", _read(
        os.path.join(ROOT, "knowledge", "backtest_candidates.md")), re.M))
    assert n, "no '## §N' headings found — the owner moved"
    return n


@pytest.mark.parametrize("path", SKILL_FILES, ids=IDS)
def test_stated_section_count_matches_the_record(path: str) -> None:
    owner = _section_count()
    bad = [m.group(0) for m in re.finditer(r"(\d+) sections", _read(path))
           if int(m.group(1)) != owner]
    assert not bad, (
        f"{os.path.basename(os.path.dirname(path))} says {bad} but the record "
        f"has {owner} sections")


# ---- owner: the 2026-08-10 reversal ------------------------------------------

@pytest.mark.parametrize("path", SKILL_FILES, ids=IDS)
def test_hands_off_is_never_asserted_without_its_reversal(path: str) -> None:
    """'Hands-off' was reversed on 2026-08-10. A skill may describe the
    history; it may not assert the state. The worst instance was in a
    DESCRIPTION, which loads in every session of every project."""
    body = _read(path)
    for m in re.finditer(r"[Hh]ands-off", body):
        window = body[max(0, m.start() - 400): m.end() + 400]
        if not re.search(r"REVERSED|reversed|2026-08-10", window):
            line = body[:m.start()].count("\n") + 1
            where = " (IN THE DESCRIPTION — loads every session)" if line < 10 else ""
            pytest.fail(
                f"{os.path.basename(os.path.dirname(path))}:{line} asserts "
                f"'hands-off' with no mention of the 2026-08-10 reversal "
                f"within 400 chars{where}")
