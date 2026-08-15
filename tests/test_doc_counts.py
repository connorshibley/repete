"""The numbers in the front-door docs are regenerated and checked, never typed.

WHY (Phase 8, 2026-08-06)
-------------------------
`README.md` is the artifact. It is also maintained by hand, and by 2026-08-06 it
was wrong in four measured places at once:

    tests               claimed 1,881      actual 1,967 (2,006 with #103)
    tests, again        `# 1881` in the quick-start block
    divergences         claimed 17, four open     actual 18, five open
    closed round-trips  claimed 8                 actual 10

That is the third time the test count has gone stale. A repo whose entire pitch
is "every number carries its benchmark and its sample size" cannot have a front
page that is wrong about its own sample sizes.

`docs/divergences.md` was worse, and this file found it. The prose said
**eighteen** and named **five** open; the summary table listed **fourteen** rows
and showed **one** open, because rows 13-16 had never been added. The table is
the fastest thing in the file to read and therefore the thing most likely to be
read alone — so the most-read summary undercounted the open divergences five to
one. Nothing compared the three statements, so nothing caught it.

WHAT IS DELIBERATELY *NOT* CHECKED HERE
---------------------------------------
The live round-trip count. `memory/ledger.jsonl` is gitignored — it does not
exist in a worktree and it does not exist on a CI runner. A test asserting it
would be skipped everywhere it ever ran, which is the "check that cannot fail"
this repo keeps catching (`ci.yml:55-69` applies the same reasoning to
`check_secret_exposure.py`). So `test_the_live_trade_count_is_checked_only_where
_a_ledger_exists` runs the check when a ledger is present and says **SKIPPED**
out loud when it is not, exactly as the backup drill's off-host check does.

THE EDGE TALLY (added 2026-08-15)
--------------------------------
The bottom half of this file pins the Bonferroni budget the front-door docs
state against `research/registrations.jsonl`, which owns it. It is here because
the *skills* got the same guard first and the docs did not: after §72 took K to
16, `GLOSSARY.md` went on saying **"K = 15 here, and the tally is 1 pass in
15"** in two places and `research/candidates_2026-08.md` quoted it as the
project's base rate. `README.md` had been updated. Nothing compared them.

That is the same defect this file already exists for — a count copied into
prose drifts from the ledger that owns it — with one extra lesson. The guard
that *should* have caught it globbed `.claude/skills/*/SKILL.md`, so the fix
for a stale-number class of bug was itself scoped to a directory. Hence
`test_every_doc_that_states_a_k_is_either_checked_or_deliberately_exempt`
below: a new markdown file that states a K has to be classified before it can
sit in the repo unchecked.
"""
import collections
import json
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
GLOSSARY = ROOT / "GLOSSARY.md"
DIVERGENCES = ROOT / "docs" / "divergences.md"
REGISTRATIONS = ROOT / "research" / "registrations.jsonl"
VERDICTS = ROOT / "research" / "verdicts.jsonl"

WORDS = {14: "Fourteen", 15: "Fifteen", 16: "Sixteen", 17: "Seventeen",
         18: "Eighteen", 19: "Nineteen", 20: "Twenty", 21: "Twenty-one",
         22: "Twenty-two", 23: "Twenty-three", 24: "Twenty-four",
         25: "Twenty-five"}


# --------------------------------------------------------------------------
# Regenerators. Each computes the figure the same way the claim means it.
# --------------------------------------------------------------------------

def collected_test_count() -> int:
    """What `pytest -q` actually collects — definitions times parametrize, not
    a count of `def test_`. Comparing a definition count to a collected count
    is the specific error that produced a false 'the README is stale' finding
    on 2026-08-06; see the plan file."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True).stdout
    m = re.search(r"(\d+) tests? collected", out)
    assert m, f"could not read a collected count from pytest:\n{out[-500:]}"
    return int(m.group(1))


def divergence_table_ids() -> list[int]:
    rows = re.findall(r"^\| (\d+) \| ", DIVERGENCES.read_text(), re.M)
    return [int(n) for n in rows]


def divergence_section_ids() -> list[int]:
    """The `## #N` deep-dive headings — where a divergence is actually written
    up. The table is a summary OF these, so a section with no row is an entry
    that exists and is uncounted."""
    heads = re.findall(r"^## #(\d+)", DIVERGENCES.read_text(), re.M)
    return [int(n) for n in heads]


def divergence_open_ids() -> list[int]:
    m = re.search(r"\*\*Open as of [^:]+: ([^*]+)\*\*", DIVERGENCES.read_text())
    assert m, "the `Open as of ...` line is gone from docs/divergences.md"
    return sorted(int(n) for n in re.findall(r"#(\d+)", m.group(1)))


def divergence_prose_total() -> int:
    # [\w-], not \w: the twenty-first divergence made the total a HYPHENATED
    # word, and `\w+` cannot match one. The failure was not a wrong count — the
    # regex missed entirely and the assert below fired with "the sentence is
    # gone", which points at the wrong file. A guard that cannot express the
    # next value it will be handed is a guard with an expiry date.
    m = re.search(r"^([\w-]+) such differences have been found",
                  DIVERGENCES.read_text(), re.M)
    assert m, "the 'N such differences have been found' sentence is gone"
    word = m.group(1)
    for n, w in WORDS.items():
        if w.lower() == word.lower():
            return n
    pytest.fail(f"unrecognised number word {word!r} — add it to WORDS")


# --------------------------------------------------------------------------
# docs/divergences.md must agree with itself.
# --------------------------------------------------------------------------

def test_the_table_lists_every_divergence_with_no_gaps():
    """Rows 13-16 were missing until 2026-08-06. A gap here is not cosmetic:
    three of those four were OPEN."""
    ids = divergence_table_ids()
    assert ids == sorted(ids), f"table rows are out of order: {ids}"
    assert ids == list(range(1, max(ids) + 1)), \
        f"gaps in the divergence table: {sorted(set(range(1, max(ids)+1)) - set(ids))}"


def test_every_deep_dive_section_has_a_table_row():
    """The check that was missing on 2026-08-11, when #21 was registered with a
    full `## #21` section and no row.

    The prose, the table and the `Open as of` line all agreed with each other
    — which is precisely what the tests above assert — while a registered OPEN
    divergence sat a few hundred lines below, absent from every count in the
    repo including README's. Three artifacts were being compared and there were
    four.

    SUBSET, not equality, and the direction is load-bearing: #1-#7 and #9 have
    rows and no sections because they predate the deep-dive convention.
    Requiring a section for every row would fail on eight legitimate entries
    and would be quietly deleted the first time someone hit it.
    """
    sections, rows = divergence_section_ids(), divergence_table_ids()
    orphans = sorted(set(sections) - set(rows))
    assert not orphans, (
        f"divergence(s) {orphans} have a `## #N` section but no summary-table "
        f"row — documented in full and invisible to every count in the repo")


def test_the_prose_total_matches_the_table():
    assert divergence_prose_total() == len(divergence_table_ids())


def test_the_open_line_matches_the_rows_marked_open():
    text = DIVERGENCES.read_text()
    marked = sorted(int(n) for n, status in
                    re.findall(r"^\| (\d+) \| .*? \| (.*?) \|", text, re.M)
                    if "open" in status.lower())
    assert marked == divergence_open_ids(), (
        f"the table marks {marked} open but the `Open as of` line says "
        f"{divergence_open_ids()} — the two most-read statements in the file "
        f"disagree")


# --------------------------------------------------------------------------
# README.md must agree with reality.
# --------------------------------------------------------------------------

def test_the_readme_test_count_is_current():
    actual = collected_test_count()
    prose = re.search(r"\*\*([\d,]+) tests as of", README.read_text())
    assert prose, "the README no longer states a test count"
    claimed = int(prose.group(1).replace(",", ""))
    assert claimed == actual, (
        f"README says {claimed:,} tests, pytest collects {actual:,}. "
        f"Update BOTH the prose and the quick-start block.")


def test_the_quickstart_block_agrees_with_the_prose():
    """The count appears twice. It has been updated in one place only before."""
    text = README.read_text()
    prose = int(re.search(r"\*\*([\d,]+) tests as of",
                          text).group(1).replace(",", ""))
    block = re.search(r"pytest tests/ -q\s+#\s*([\d,]+)", text)
    assert block, "the quick-start block no longer carries the count"
    assert int(block.group(1).replace(",", "")) == prose


def test_the_readme_divergence_count_is_current():
    text = README.read_text()
    m = re.search(r"\*\*(\d+) as of [\d-]+\*\*", text)
    assert m, "the README no longer states a divergence count"
    assert int(m.group(1)) == len(divergence_table_ids())


def test_the_readme_open_closed_split_is_current():
    text = README.read_text()
    total = len(divergence_table_ids())
    n_open = len(divergence_open_ids())
    # Contiguous 1-20, not the handful of values that happened to be in use.
    # This dict skipped 7, so the day a seventh divergence went open the test
    # raised `KeyError: 7` instead of asserting anything — a guard that fails
    # by crashing tells you nothing about the thing it guards, and the crash
    # points at the test rather than at the README.
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
             7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven",
             12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen",
             16: "sixteen", 17: "seventeen", 18: "eighteen", 19: "nineteen",
             20: "twenty"}
    n_closed = total - n_open
    for n, label in ((n_closed, "closed"), (n_open, "open")):
        assert n in words, (
            f"no word for {n} {label} — extend `words` rather than letting "
            f"this raise KeyError and read as a test bug")
        assert f"{words[n]} {label}" in text.lower(), \
            f"README should say '{words[n]} {label}' ({total} total, {n_open} open)"


# --------------------------------------------------------------------------
# The one that cannot run everywhere, and says so.
# --------------------------------------------------------------------------

def test_the_live_trade_count_is_checked_only_where_a_ledger_exists(capsys):
    """`memory/ledger.jsonl` is gitignored: absent in a worktree, absent in CI.

    Asserting it unconditionally would make this a test that skips everywhere
    it runs. It is conditional and LOUD about it instead — the same shape as
    the backup drill's off-host check, and the same reasoning `ci.yml` gives
    for keeping `check_secret_exposure.py` out of CI.
    """
    ledger = ROOT / "memory" / "ledger.jsonl"
    if not ledger.exists():
        with capsys.disabled():
            print(f"\n  SKIPPED: no ledger at {ledger} — the README's live "
                  f"round-trip count is unverifiable here (worktree or CI). "
                  f"It IS verified when run from the live checkout.")
        return
    actual = sum(1 for line in ledger.read_text().splitlines()
                 if '"type": "outcome"' in line)
    m = re.search(r"Live record: (\d+) closed round-trips", README.read_text())
    assert m, "the README no longer states a live round-trip count"
    assert int(m.group(1)) == actual, (
        f"README says {m.group(1)} closed round-trips, the ledger has {actual}")


# --------------------------------------------------------------------------
# The EDGE tally: the docs must agree with the register that owns it.
# --------------------------------------------------------------------------
#
# `tests/test_skills_are_current.py` makes this same argument for
# `.claude/skills/*/SKILL.md` and carries its own copy of `edge_families`,
# `K_STATED_RE` and `M_STATED_RE`. The two copies exist because the skills
# guard and this one were written on branches that were open at the same time;
# whoever touches the second of them should lift the three shared pieces into
# one helper module. Duplicated definitions that must agree are exactly the
# failure this section is about.


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


# `K is 16`, `K = 16`, `2 passes in 16`, `only EDGE pass in 15 claims` — every
# shape the docs use to state the budget. Deliberately requires a digit, so
# prose that spells the number out ("one pass in sixteen") is not swept up: a
# word form cannot be confused with a per-claim `K=13` and is not worth the
# false positives.
K_STATED_RE = re.compile(
    r"\bK is (?P<a>\d+)\b"
    r"|\bK\s*=\s*(?P<b>\d+)\b"
    r"|(?:\d+ )?pass(?:es)? in (?P<c>\d+)\b"
    r"|\bin (?P<d>\d+) claims\b")

# The `M` half of "M passes in N".
M_STATED_RE = re.compile(r"\b(?P<m>\d+) pass(?:es)? in \d+\b")

# Docs that describe the project AS IT IS. Each must agree with the register.
CHECKED_DOCS = [
    "README.md",
    "GLOSSARY.md",
    "research/candidates_2026-08.md",
]

# Docs that legitimately state a K the register will disagree with, and why.
# A path here is a claim that the number in it is not a current-state claim.
EXEMPT_DOCS = {
    ".claude/skills/":
        "owned by tests/test_skills_are_current.py, which asserts the same "
        "thing against the same register",
    "knowledge/backtest_candidates.md":
        "the frozen record. Each `## §N` header states the K that claim was "
        "registered at; rewriting them to the current K would falsify what "
        "the record said at the time, which is the whole point of it",
    "research/beat_spy_report_2026-08.md":
        "same — a frozen statement of what the record said on its own date",
    "research/INDEX.md":
        "one row per section, each carrying the K of that registration. Not a "
        "statement about the current budget",
    "research/specs/":
        "spec drafts state the K a registration would spend, frozen when the "
        "spec was written",
    "docs/superpowers/specs/":
        "dated design docs ('design approved 2026-08-04. Not implemented.'), "
        "point-in-time like the frozen record",
}

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".site",
              "build", "dist"}


def all_markdown() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*.md")
                  if not _SKIP_DIRS & set(p.relative_to(ROOT).parts))


def test_the_register_can_still_answer_what_k_is():
    """Every assertion below is vacuous if the parse silently yields nothing —
    a renamed key or a restructured row would turn this whole section green."""
    fams = edge_families()
    assert fams, (
        f"no rows in {REGISTRATIONS} parse as EDGE claims with a "
        f"`bonferroni_k`; the tally guard below is measuring nothing")
    assert passing_family_ceiling() > 0, (
        f"no EDGE family in {VERDICTS} has a passing arm; the overclaim "
        f"ceiling below is 0 and would reject every honest number")


@pytest.mark.parametrize("rel", CHECKED_DOCS)
def test_the_bonferroni_budget_a_doc_states_matches_the_register(rel):
    """No current-state doc may state a K the register disagrees with.

    This is the skills guard's argument moved to the front door, and it is
    here because the front door is where it actually failed: `GLOSSARY.md`
    defined **Bonferroni K** as 15 for two waves after §72 made it 16, in the
    file whose job is to tell a reader what the project's words mean.
    """
    path = ROOT / rel
    assert path.exists(), f"{rel} is in CHECKED_DOCS and does not exist"
    k = max(edge_families())
    stated = {int(m.group(g)) for m in K_STATED_RE.finditer(path.read_text())
              for g in ("a", "b", "c", "d") if m.group(g) is not None}
    assert stated, (
        f"{rel} no longer states the EDGE budget anywhere. If that is "
        f"deliberate, drop it from CHECKED_DOCS; otherwise this test just "
        f"stopped checking the thing it was written for")
    wrong = sorted(stated - {k})
    assert not wrong, (
        f"{rel} states the EDGE budget as {wrong}; {REGISTRATIONS} says "
        f"K={k}.\nThe register owns this number. A doc quoting a stale one is "
        f"how the next claim gets read against the previous claim's "
        f"significance threshold.")


@pytest.mark.parametrize("rel", CHECKED_DOCS)
def test_no_doc_claims_more_edge_passes_than_the_verdicts_support(rel):
    ceiling = passing_family_ceiling()
    text = (ROOT / rel).read_text()
    over = sorted({int(m.group("m")) for m in M_STATED_RE.finditer(text)}
                  - set(range(ceiling + 1)))
    assert not over, (
        f"{rel} claims {over} EDGE passes; at most {ceiling} of the registered "
        f"EDGE families have even one passing arm in {VERDICTS}")


def test_every_doc_that_states_a_k_is_either_checked_or_deliberately_exempt():
    """The scoping bug, guarded.

    The stale `GLOSSARY.md` was not missed because the check was wrong — it
    was missed because the check globbed `.claude/skills/*/SKILL.md` and the
    stale number was somewhere else. Listing the files to check is the same
    move that failed, so the list is inverted here: every markdown file in the
    repo that states a K must be classified, and an unclassified one is a
    failure rather than a silent pass.

    A new doc quoting the tally therefore costs one line — in `CHECKED_DOCS`
    if it describes the project now, or in `EXEMPT_DOCS` with a reason if the
    number in it is frozen. What it cannot do is arrive unexamined.
    """
    unclassified = []
    for path in all_markdown():
        rel = path.relative_to(ROOT).as_posix()
        if rel in CHECKED_DOCS or any(rel.startswith(e) for e in EXEMPT_DOCS):
            continue
        if any(m.group(g) is not None
               for m in K_STATED_RE.finditer(path.read_text())
               for g in ("a", "b", "c", "d")):
            unclassified.append(rel)
    assert not unclassified, (
        "these files state an EDGE budget and are neither checked against "
        f"research/registrations.jsonl nor exempted: {unclassified}\n"
        "Add each to CHECKED_DOCS (if it describes the project as it is) or "
        "to EXEMPT_DOCS with the reason its number is frozen.")


def test_the_exemptions_still_point_at_something():
    """An exemption for a file that no longer exists is a rule nobody reads
    protecting nothing — and it hides the day a checked doc gets moved under
    an exempt prefix."""
    dead = [e for e in EXEMPT_DOCS
            if not (ROOT / e).exists()
            and not any(p.relative_to(ROOT).as_posix().startswith(e)
                        for p in all_markdown())]
    assert not dead, f"EXEMPT_DOCS entries match no file: {dead}"
