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
