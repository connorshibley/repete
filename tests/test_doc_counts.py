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
"""
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DIVERGENCES = ROOT / "docs" / "divergences.md"

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

# --------------------------------------------------------------------------
# The EDGE tally must agree with the register that owns it.
#
# §72 took K from 15 to 16 on 2026-08-12 and the correction reached some
# statements and not others — leaving README.md contradicting ITSELF inside one
# paragraph: a bolded "One EDGE claim in fifteen has passed" directly above
# "the tally is 2 passes in 16". Exactly the shape this file was written for
# (see the header: the prose said eighteen, the table listed fourteen).
#
# README.md:132 claims backtest_candidates.md "carries the running count and is
# the only place it is kept". That is not true — README, GLOSSARY and the
# skills all restate it. Where a restatement could be deleted it was; what
# remains is checked here.
#
# The derivation lives in conftest, shared with test_skills_are_current.py, so
# the two guards cannot drift apart on what counts as stating the budget.
# --------------------------------------------------------------------------

from conftest import (edge_pass_ceiling, live_bonferroni_k,  # noqa: E402
                      stated_tallies, UNIQUE_PASS_RE)

# CLASSIFY, don't enumerate — and don't glob shallowly either.
#
# The first version of this guard globbed `ROOT/*.md` + `docs/*.md`,
# non-recursively, on the argument that the frozen material all sits a level
# deeper. True today, and the wrong shape: it is the same move that failed in
# the first place. `test_skills_are_current.py` globbed
# `.claude/skills/*/SKILL.md` and the stale number was somewhere else, so a fix
# for a stale-number bug was itself scoped to a directory.
#
# So the list is inverted. EVERY markdown file in the repo that states a K must
# be classified, and an unclassified one is a failure rather than a silent pass.
# A new doc quoting the tally costs one line: CHECKED_DOCS if it describes the
# project now, EXEMPT_DOCS with a reason if its number is not a current-state
# claim. What it cannot do is arrive unexamined.
#
# (Design salvaged from #120, which reached it independently and better.)

CHECKED_DOCS = [
    "README.md",
    "GLOSSARY.md",
    "docs/data_vendors.md",
    "docs/divergences.md",
]

# The docs that must be caught STATING the tally, not merely not-contradicting
# it. A subset of CHECKED_DOCS: the other two are checked if they mention it and
# are equally correct saying nothing.
OWNING_DOCS = ["README.md", "GLOSSARY.md"]

# A path here is a claim that the number in it is NOT a current-state claim.
# Prefix match, so a directory covers everything under it.
EXEMPT_DOCS = {
    ".claude/skills/":
        "owned by tests/test_skills_are_current.py, which asserts the same "
        "thing against the same register",
    "knowledge/":
        "the append-only record. Each `## §N` states the K that claim was "
        "registered at; rewriting them to the current K would falsify what the "
        "record said at the time, which is the whole point of it",
    "research/":
        "dated reports, hash-frozen specs (gatespec hashes the parsed spec, so "
        "an edit breaks its registration), the generated INDEX.md, and "
        "candidates_2026-08.md — a single-commit dossier that describes the "
        "K=16 spend as a FUTURE possibility",
    "docs/superpowers/specs/":
        "date-prefixed design docs, marked 'Not implemented'. Snapshots of the "
        "record as it stood on the date in the filename",
}

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".site",
              "build", "dist", "backups"}


def all_markdown() -> list[pathlib.Path]:
    return sorted(p for p in ROOT.rglob("*.md")
                  if not _SKIP_DIRS & set(p.relative_to(ROOT).parts))


def _classified(rel: str) -> bool:
    return (rel in CHECKED_DOCS
            or any(rel.startswith(e) for e in EXEMPT_DOCS))


def test_every_doc_that_states_a_k_is_either_checked_or_deliberately_exempt():
    """The scoping bug, guarded.

    `GLOSSARY.md` was not missed because a check was wrong. It was missed
    because no check looked there. Listing the files to check cannot catch the
    file nobody thought to list, so this catches the complement instead.
    """
    unclassified = [p.relative_to(ROOT).as_posix() for p in all_markdown()
                    if not _classified(p.relative_to(ROOT).as_posix())
                    and any(stated_tallies(p.read_text()))]
    assert not unclassified, (
        f"these files state an EDGE budget and are in neither classification: "
        f"{unclassified}\nAdd each to CHECKED_DOCS (it describes the project as "
        f"it is) or to EXEMPT_DOCS with the reason its number is not a "
        f"current-state claim.")


@pytest.mark.parametrize("bucket", ["CHECKED_DOCS", "EXEMPT_DOCS"])
def test_the_classifications_still_point_at_something(bucket):
    """A classification for a file that no longer exists is a rule nobody reads
    protecting nothing — and it hides the day a checked doc is moved under an
    exempt prefix."""
    entries = {"CHECKED_DOCS": CHECKED_DOCS, "EXEMPT_DOCS": EXEMPT_DOCS}[bucket]
    known = {p.relative_to(ROOT).as_posix() for p in all_markdown()}
    dead = [e for e in entries
            if not (ROOT / e).exists()
            and not any(k.startswith(e) for k in known)]
    assert not dead, f"{bucket} entries match no file: {dead}"


def test_no_doc_is_classified_twice():
    """Checked AND exempt is not a state. It reads as covered and behaves as
    exempt, which is the worst of the two."""
    both = [d for d in CHECKED_DOCS if any(d.startswith(e) for e in EXEMPT_DOCS)]
    assert not both, f"classified as both checked and exempt: {both}"


def test_no_checked_doc_states_a_stale_bonferroni_k():
    k = live_bonferroni_k()
    bad = {}
    for rel in CHECKED_DOCS:
        wrong = sorted(stated_tallies((ROOT / rel).read_text())[0] - {k})
        if wrong:
            bad[rel] = wrong
    assert not bad, (
        f"{bad}\nresearch/registrations.jsonl says K={k}. The register owns "
        f"this number; a doc quoting a stale one is how a reader learns the "
        f"tally is decorative.")


def test_no_doc_claims_more_edge_passes_than_the_verdicts_support():
    """Applies to EXEMPT docs too, and that asymmetry is deliberate: a frozen
    doc may legitimately state an out-of-date K — that is what the category is
    for — but nothing in this repo, frozen or not, may claim more passes than
    the verdicts support. Staleness is a fact about a date; overclaiming is not.

    A bound, not an equality — see `conftest.edge_pass_ceiling`."""
    ceiling = edge_pass_ceiling()
    bad = {}
    for p in all_markdown():
        over = sorted(stated_tallies(p.read_text())[1] - set(range(ceiling + 1)))
        if over:
            bad[p.relative_to(ROOT).as_posix()] = over
    assert not bad, (
        f"{bad}\nat most {ceiling} registered EDGE families have even one "
        f"passing arm in research/verdicts.jsonl.")


def test_no_checked_doc_calls_the_edge_record_unique():
    """"this project's only EDGE pass" states a tally with no number in it.

    A guard that reads only digits reads half the prose. `docs/data_vendors.md`
    carried this shape and every numeric assertion above was green on it."""
    if edge_pass_ceiling() <= 1:
        return
    bad = {rel: sorted({m.group(0) for m in
                        UNIQUE_PASS_RE.finditer((ROOT / rel).read_text())})
           for rel in CHECKED_DOCS
           if UNIQUE_PASS_RE.search((ROOT / rel).read_text())}
    assert not bad, (
        f"{bad}\n§72 was the second pass. Say 'the first of two' rather than "
        f"'the only'.")


def test_the_owning_docs_still_state_the_tally_in_a_shape_the_guard_reads():
    """Every test above cannot tell *correct* from *silent*.

    Reword README to "the tally is two in sixteen" and all of them pass by
    matching nothing at all. The docs that own the number must therefore be
    caught STATING it, which is this file's founding argument (a check that
    cannot fail is worse than no check) applied to the extractor rather than to
    the corpus."""
    k, ceiling = live_bonferroni_k(), edge_pass_ceiling()
    for rel in OWNING_DOCS:
        ks, ms = stated_tallies((ROOT / rel).read_text())
        assert k in ks, (
            f"{rel} no longer states K={k} in any shape this guard reads. "
            f"Either it stopped carrying the tally, or the wording drifted "
            f"out of `conftest._K_ONLY_RE` / `_TALLY_RE`.")
        assert ms, f"{rel} states K but no pass count"
        assert max(ms) <= ceiling, f"{rel} claims {max(ms)} passes, ceiling {ceiling}"


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
# Operator-facing CODE must not state the tally at all.
#
# A stricter rule than the one above, and deliberately so. A doc that states
# the count correctly is fine; a BANNER that states it correctly today is a
# copy that will be wrong later and will be read as authoritative in the
# meantime, because it arrives in the operator's terminal at the moment of a
# decision.
#
# `tests/test_fmp_lookahead_probe.py:299-306` established exactly this for ONE
# probe, and recorded why: "a count duplicated into a banner drifts from the
# ledger that owns it and then gets quoted as if it were checked." Nothing
# generalized it, so its three siblings printed "EDGE stands at 1 pass in 15
# and K stays 15" through §72 and both waves after it. Neither guard scanned a
# .py file.
#
# `tests/` is exempt wholesale: test files quote stale text on purpose, as
# fixtures and as the motivating examples in these very docstrings, and
# tests/data/significance_golden.json carries a frozen `Bonferroni K=15`.
# --------------------------------------------------------------------------

CODE_DIRS = (ROOT / "scripts", ROOT / "src")

# Files where a bare `K = N` is an ARM COUNT in a bootstrap comparison, not the
# project's budget — a genuinely different quantity that happens to share the
# letter. `gate_s35_momentum.py` is the third case: its K=8 is §35's frozen
# context, correct on the day it was written.
EXEMPT_CODE = {
    "scripts/gate_cross_asset.py": "K=3 is the arm count of that comparison",
    "scripts/gate_wide_universe.py": "K=6 is the arm count of that comparison",
    "scripts/gate_s35_momentum.py": "K=8 is §35's frozen registration context",
}

# The probes whose verdict banners must name the file that owns the count.
PROBES_THAT_DEFER = [
    "scripts/probe_fmp_lookahead.py",
    "scripts/probe_alfred_vintages.py",
    "scripts/probe_edgar_pit.py",
    "scripts/probe_delisted_coverage.py",
]


def code_files() -> list[pathlib.Path]:
    return sorted(p for d in CODE_DIRS for p in d.glob("*.py"))


def test_no_operator_facing_code_states_the_edge_tally():
    """Not "states it correctly" — states it at all."""
    bad = {}
    for p in code_files():
        rel = p.relative_to(ROOT).as_posix()
        if rel in EXEMPT_CODE:
            continue
        text = p.read_text()
        ks, ms = stated_tallies(text)
        unique = sorted({m.group(0) for m in UNIQUE_PASS_RE.finditer(text)})
        if ks or ms or unique:
            bad[rel] = {"K": sorted(ks), "passes": sorted(ms),
                        "uniqueness": unique}
    assert not bad, (
        f"{bad}\nCode must not restate the EDGE tally — not even correctly. "
        f"Say it is unchanged and name knowledge/backtest_candidates.md, the "
        f"way scripts/probe_fmp_lookahead.py does.")


def test_every_exempt_code_file_still_exists():
    """An exemption for a deleted file silently re-scopes this guard."""
    dead = sorted(r for r in EXEMPT_CODE if not (ROOT / r).exists())
    assert not dead, f"EXEMPT_CODE names files that do not exist: {dead}"


@pytest.mark.parametrize("rel", PROBES_THAT_DEFER)
def test_a_probe_that_says_nothing_still_points_somewhere(rel):
    """The counterpart to the negative test, and the reason it is not enough.

    "State no tally" is satisfied perfectly by deleting the sentence and
    pointing nowhere, which would leave an operator with a verdict and no route
    to the count it should be read against. Silence is not deference."""
    assert "knowledge/backtest_candidates.md" in (ROOT / rel).read_text(), (
        f"{rel} no longer names the file that owns the EDGE count. Saying "
        f"nothing about the tally is only honest while the reader is told "
        f"where it lives.")
