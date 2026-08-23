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
import pathlib
import re
import sys

import pytest

sys.path.insert(0, "src")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gatespec as gs                                        # noqa: E402
from edge_tally import (  # noqa: E402
    REGISTRATIONS,
    ROOT,
    VERDICTS,
    current_k,
    edge_families,
    passing_family_ceiling,
    stated_ks,
    stated_ms,
)

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

# `edge_families`, the K/M matchers and `stated_ks` used to live here, and a
# second copy of them lived in `test_doc_counts.py`. Two definitions that must
# agree, maintained separately, is the exact failure both guards exist to
# prevent, so they now live once in `tests/edge_tally.py`.
#
# The surviving copy is the stricter one: it also matches the tally spelled out
# in words, and normalises whitespace first. Both mattered — the digit-only
# version walked past *"the single EDGE pass in fifteen claims"*, and matching
# raw text made coverage depend on where a hard-wrapped line happened to break.


def test_the_register_can_still_answer_what_k_is():
    """Every assertion below is vacuous if the parse silently yields nothing —
    a renamed key or a restructured row would turn this whole section green."""
    fams = edge_families()
    assert fams, (
        f"no rows in {REGISTRATIONS} parse as EDGE claims with a "
        f"`bonferroni_k`; the budget guard below is measuring nothing")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_the_bonferroni_budget_a_skill_states_matches_the_register(name, path):
    """No skill may state a K that `research/registrations.jsonl` disagrees with.

    This is the §52-freeze test's argument moved from a rule to a number. A
    stale rule sends a session to a crash; a stale K is quieter and worse — it
    reads as authority, it is the denominator the whole record is judged
    against, and a session that believes K is 15 registers the sixteenth claim
    at the fifteenth claim's significance threshold.
    """
    k = current_k()
    stated = stated_ks(open(path).read())
    wrong = sorted(stated - {k})
    assert not wrong, (
        f"{path} states the EDGE budget as {wrong}; {REGISTRATIONS} says "
        f"K={k}.\nUpdate the skill — the register owns this number, and a "
        f"skill quoting a stale one is how the sixteenth claim gets scored at "
        f"the fifteenth's threshold.")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_no_skill_claims_more_edge_passes_than_the_verdicts_support(name, path):
    """An upper bound on the `M` of "M passes in N", not an equality.

    Whether a *family* passed is not mechanically derivable, and pretending
    otherwise would put a false precision in a test file. §44 (K=13) had three
    of four arms pass and is REJECTED, because its pre-registered reading rule
    was a conjunction — "one clause failure in one period sinks the whole
    claim". §72 (K=16) also had three of four and is CONFIRMED, because §68 had
    frozen a 3-of-4 confirmation rule for it beforehand. Both rules were
    written down before the runs, which is what makes them binding; neither
    lives in the spec as a field a test could read.

    So what is checkable is the ceiling: a family with no passing arm under any
    reading rule did not pass. M may be below this (it is — 2 against a ceiling
    of 3, because §44's conjunction sank it) and never above it. That is the
    overclaim direction, which is the one this project guards hardest.
    """
    ceiling = passing_family_ceiling()
    over = sorted(stated_ms(open(path).read()) - set(range(ceiling + 1)))
    assert not over, (
        f"{path} claims {over} EDGE passes; at most {ceiling} of the "
        f"registered EDGE families have even one passing arm in "
        f"{VERDICTS}.")


# ---------------------------------------------------------------------------
# Added 2026-08-23, after an audit found three drifts this file did not cover.
#
# The K guard above DID exist for exactly the drift that got through — but its
# matcher knew "K is 15" and "K = 15" and not "K stays 15", which is what
# `bot-research-recall` wrote. That hole is closed in edge_tally.K_STATED_RE.
# The three below are the drifts with no guard at all.
# ---------------------------------------------------------------------------

HOME_PATH_RE = re.compile(r"~/bots/[A-Za-z0-9._-]+")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_every_home_relative_path_a_skill_names_exists(name, path):
    """`test_every_repo_path_a_skill_names_exists` covers paths INSIDE this
    repo. It does not cover `~/bots/...`, and that is where the drift landed:
    the 2026-08-23 rename left `bot-pre-registration` sending sessions to
    `~/bots/repete-tv-dev`, which has never existed. A session that follows it
    either stops or, worse, creates the directory and works in the wrong one.
    """
    missing = sorted({m.group(0) for m in HOME_PATH_RE.finditer(open(path).read())
                      if not os.path.isdir(os.path.expanduser(m.group(0)))})
    assert not missing, (
        f"{path} sends the agent to directories that do not exist: {missing}")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_stated_section_count_matches_the_record(name, path):
    """Two skills read "64 sections" against an actual 67. Unlike K this is
    not load-bearing arithmetic — nothing is scored against it — but it is the
    number a session uses to judge whether `recall.py` returned everything,
    and a low one makes a complete answer look truncated."""
    record = ROOT / "knowledge" / "backtest_candidates.md"
    actual = len(re.findall(r"^##\s+§\d+", record.read_text(), re.M))
    assert actual, f"no '## §N' headings in {record} — this guard is vacuous"
    wrong = sorted({int(m.group(1)) for m in
                    re.finditer(r"(\d+) sections", open(path).read())} - {actual})
    assert not wrong, (
        f"{path} states {wrong} sections; {record.name} has {actual}")


@pytest.mark.parametrize("name,path", ALL, ids=[n for n, _ in ALL])
def test_hands_off_is_never_asserted_without_its_reversal(name, path):
    """Hands-off held 2026-08-02 to 2026-08-10 and was REVERSED. A skill may
    describe that history; it may not assert the state.

    The instance that prompted this was in a DESCRIPTION — the ~70 tokens that
    load in every session of every project, including ones with no bots in
    them. A wrong fact there is the most expensive kind: it is always present
    and never re-read.
    """
    body = open(path).read()
    for m in re.finditer(r"[Hh]ands-off", body):
        window = body[max(0, m.start() - 400): m.end() + 400]
        if re.search(r"REVERSED|reversed|2026-08-10", window):
            continue
        line = body[:m.start()].count("\n") + 1
        where = " — IN THE FRONTMATTER DESCRIPTION" if line < 10 else ""
        pytest.fail(f"{path}:{line} asserts 'hands-off' with no mention of the "
                    f"2026-08-10 reversal within 400 chars{where}")
