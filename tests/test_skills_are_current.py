"""The skills in `.claude/skills/` must describe THIS repo, not a past one.

Why this file exists
--------------------
A skill is documentation that an agent acts on without re-reading the code. That
is its whole value and its whole danger: a stale skill does not look stale, it
looks authoritative. If `scripts/survivorship_check.py` is renamed tomorrow,
`bot-survivorship-audit` keeps confidently telling the next session to run it,
and the session believes it.

This is §36's argument applied to documentation — *"a discipline that is only a
habit gets forgotten by the next session"* — and the same argument
`register_gate.py` makes about the EDGE freeze: a rule that lives only in prose
is a rule somebody eventually walks past. So every repo path, every gate rule
name and every claim type a skill mentions is asserted here. Rename the file and
CI goes red instead of the skill quietly lying.

What it deliberately does NOT check: whether the prose is *right*. No test can
do that. It checks that everything the prose NAMES still exists, which is the
failure mode that actually happens.

The one exception is the Bonferroni budget at the bottom of this file, and it
is here because "everything the prose names still exists" turned out not to be
the whole failure mode. `bot-pre-registration` read *"K is 15. The EDGE record
is 1 pass in 15"* for two full waves after §72 took it to 16 — every path it
named existed, every rule it named was real, and the number at the centre of it
was wrong. A count copied into prose drifts from the ledger that owns it and
then gets quoted as if it had been checked. `test_fmp_lookahead_probe.py` makes
the same argument about the same tally in a banner.
"""
from __future__ import annotations

import os
import re
import sys

import pytest

sys.path.insert(0, "src")
import gatespec as gs                                        # noqa: E402

SKILL_DIR = ".claude/skills"

# Anchored at a word boundary that is not itself part of a longer path, so
# `.claude/skills/bot-prove-it/scripts/mutate.py` is not mistaken for a
# top-level `scripts/mutate.py`.
PATH_RE = re.compile(
    r"(?<![\w./-])((?:src|scripts|tests|docs|knowledge|research)"
    # `jsonl` BEFORE `json`, or the alternation matches the shorter one first
    # and reports `research/registrations.json` — a file that does not exist —
    # as missing. Found by this test on its first run.
    r"/[\w./-]+\.(?:py|md|ya?ml|sh|jsonl|json))")

# `data/` is excluded on purpose: snapshots are large and not always present on
# a fresh checkout, so asserting them here would make the test a download check.


def skills() -> list[tuple[str, str]]:
    if not os.path.isdir(SKILL_DIR):
        return []
    out = []
    for name in sorted(os.listdir(SKILL_DIR)):
        p = os.path.join(SKILL_DIR, name, "SKILL.md")
        if os.path.isfile(p):
            out.append((name, p))
    return out


ALL = skills()


def test_there_are_skills_to_check():
    """If the directory empties, every other test here passes vacuously. That is
    the classic way a guard stops guarding."""
    assert ALL, f"no SKILL.md found under {SKILL_DIR}/"


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_frontmatter_is_well_formed(name, path):
    """The `description` is what decides whether a skill fires at all. A skill
    with a missing or lazy description is a skill that never loads, which is
    indistinguishable from not writing it."""
    text = open(path).read()
    assert text.startswith("---\n"), f"{path}: no YAML frontmatter"
    fm = text.split("---", 2)[1]
    assert re.search(rf"^name:\s*{re.escape(name)}\s*$", fm, re.M), \
        f"{path}: frontmatter `name` must match the directory name {name!r}"
    desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    assert desc, f"{path}: no `description`"
    assert len(desc.group(1)) > 80, \
        f"{path}: description is too short to route on"


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_every_repo_path_a_skill_names_exists(name, path):
    missing = sorted({m for m in PATH_RE.findall(open(path).read())
                      if not os.path.exists(m)})
    assert not missing, (
        f"{path} names files that no longer exist: {missing}\n"
        f"Either restore them or update the skill — a skill that cites a "
        f"missing path is worse than no skill, because it reads as authority.")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_every_gate_rule_a_skill_names_is_real(name, path):
    """Skills recommend rules by name (`beats_benchmark_symbol`) and warn against
    others (`beats_exposure_matched`). Both directions have to stay valid: a
    recommendation for a deleted rule sends the next session to a crash, and a
    warning about a deleted rule is noise that erodes trust in the rest."""
    text = open(path).read()
    named = {r for r in re.findall(r"`(beats_\w+|min_trades|deployment_\w+"
                                   r"|enablement_gate)`", text)}
    unknown = sorted(named - set(gs.CLAUSE_RULES))
    assert not unknown, (
        f"{path} names gate rules that are not in gatespec.CLAUSE_RULES: "
        f"{unknown}")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_claim_types_match_gatespec(name, path):
    text = open(path).read()
    named = {c for c in re.findall(r"\b(EDGE|CAPACITY|METHOD|DIAGNOSTIC)\b", text)}
    unknown = sorted(named - set(gs.CLAIM_TYPES))
    assert not unknown, f"{path} names unknown claim types: {unknown}"


def test_the_freeze_skill_matches_the_freeze_code():
    """`bot-pre-registration` tells the next session that EDGE claims on
    data/snapshots/ are refused. If someone lifts the freeze in code and forgets
    the skill, the skill starts enforcing a rule the repo no longer has."""
    import importlib
    sys.path.insert(0, "scripts")
    rgate = importlib.import_module("register_gate")
    text = open(f"{SKILL_DIR}/bot-pre-registration/SKILL.md").read()
    assert rgate.FROZEN_SNAPSHOT_DIR in text
    for claim in rgate.FROZEN_CLAIMS:
        assert claim in text
    assert "--override-freeze" in text


def test_the_survivorship_skill_still_points_at_its_own_evidence():
    """Its numbers come from two scripts. Both must exist, or the table becomes
    an unfalsifiable claim in a markdown file."""
    text = open(f"{SKILL_DIR}/bot-survivorship-audit/SKILL.md").read()
    for script in ("scripts/survivorship_check.py",
                   "scripts/probe_delisted_coverage.py"):
        assert script in text and os.path.exists(script)


def test_the_divergence_skill_does_not_undercount():
    """It states how many divergences are recorded. That number drifts the
    moment anyone adds one, and a skill claiming 'fifteen' next to a register of
    twenty is how a reader learns to stop trusting skills."""
    reg = open("docs/divergences.md").read()
    # Numbered two ways in that file: early ones only as rows in the summary
    # table, later ones as `## #N` sections. Take the highest either way.
    n = max([int(x) for x in re.findall(r"^## #(\d+)", reg, re.M)]
            + [int(x) for x in re.findall(r"^\| (\d+) \|", reg, re.M)])
    text = open(f"{SKILL_DIR}/bot-divergence-check/SKILL.md").read()
    words = {14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen",
             18: "Eighteen", 19: "Nineteen", 20: "Twenty"}
    assert words.get(n, str(n)) in text, (
        f"docs/divergences.md goes up to #{n}; the skill does not say so")


# ------------------------------------------------------------ Bonferroni budget
#
# `edge_families` / `live_bonferroni_k` / `edge_pass_ceiling` live in conftest
# because `test_doc_counts.py` asserts the same tally against README, GLOSSARY
# and docs/. One derivation, two callers — a second copy here would be the
# drift these guards exist to catch.

from conftest import (edge_pass_ceiling, live_bonferroni_k,  # noqa: E402
                      stated_tallies, REGISTRATIONS, UNIQUE_PASS_RE, VERDICTS)


def test_the_register_can_still_answer_what_k_is():
    """Every assertion below is vacuous if the parse silently yields nothing —
    a renamed key or a restructured row would turn this whole section green."""
    assert live_bonferroni_k() > 0


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_the_bonferroni_budget_a_skill_states_matches_the_register(name, path):
    """No skill may state a K that `research/registrations.jsonl` disagrees with.

    This is the §52-freeze test's argument moved from a rule to a number. A
    stale rule sends a session to a crash; a stale K is quieter and worse — it
    reads as authority, it is the denominator the whole record is judged
    against, and a session that believes K is 15 registers the sixteenth claim
    at the fifteenth claim's significance threshold.

    Reads the WHOLE file, frontmatter included. `bot-survivorship-audit` had a
    corrected body and a stale `description:` at the same time, and the
    description is the half that decides whether the skill loads at all.
    """
    k = live_bonferroni_k()
    stated, _ = stated_tallies(open(path).read())
    wrong = sorted(stated - {k})
    assert not wrong, (
        f"{path} states the EDGE budget as {wrong}; {REGISTRATIONS} says "
        f"K={k}.\nUpdate the skill — the register owns this number, and a "
        f"skill quoting a stale one is how the sixteenth claim gets scored at "
        f"the fifteenth's threshold.")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_no_skill_claims_more_edge_passes_than_the_verdicts_support(name, path):
    """An upper bound on the `M` of "M passes in N", not an equality.

    See `conftest.edge_pass_ceiling` for why equality is not available: the
    per-family reading rules (§44's conjunction, §72's 3-of-4) were frozen in
    prose before their runs, and neither is a field a test can read.
    """
    ceiling = edge_pass_ceiling()
    _, claimed = stated_tallies(open(path).read())
    over = sorted(claimed - set(range(ceiling + 1)))
    assert not over, (
        f"{path} claims {over} EDGE passes; at most {ceiling} of the "
        f"registered EDGE families have even one passing arm in "
        f"{VERDICTS}.")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_no_skill_calls_the_edge_record_unique(name, path):
    """A tally does not need a digit to be stated, or to be wrong.

    Three of the four stale statements §72 left behind carried a number and
    were caught above. The other two said *"this project's only EDGE pass"* —
    no number, nothing for a numeric guard to compare, and false since
    2026-08-12. A guard that only reads digits is a guard that reads half the
    prose.

    Gated on the ceiling rather than asserted flatly, so the test carries its
    own justification: while two or more registered families have a passing
    arm, a bare "only" is at minimum unsupported. §51 can still be described —
    as *the first of two*, or as the one this audit broke — just not as the
    only one.
    """
    ceiling = edge_pass_ceiling()
    if ceiling <= 1:
        return
    hits = sorted({m.group(0) for m in UNIQUE_PASS_RE.finditer(open(path).read())})
    assert not hits, (
        f"{path} calls the EDGE record unique: {hits}. {VERDICTS} shows "
        f"{ceiling} registered families with a passing arm, and §72 is one of "
        f"them.\nSay 'the first of two' rather than 'the only' — and check the "
        f"frontmatter `description:`, not just the body.")
