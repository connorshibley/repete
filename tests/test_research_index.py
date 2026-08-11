"""`research/INDEX.md` is generated, and this proves the committed copy is current.

A hand-maintained index of a 46-section, 5,674-line file is the divergence-table
bug waiting to happen again: on 2026-08-06 that table was found missing four of
eighteen entries — three of them OPEN — because nothing regenerated it and
nothing compared it to its source.

So the index is generated and checked. If someone edits `INDEX.md` by hand, or
adds a `## §` section without regenerating, this goes red.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEN = ROOT / "scripts" / "gen_research_index.py"

# The content tests below assert against `_gen().build()`, NOT against the
# committed INDEX.md. Reading the committed file makes each of them pass no
# matter what the generator does — they would fail only via
# `test_the_committed_index_is_current`, which is one check wearing seven hats.
# Three mutations survived exactly that way before this was changed.
INDEX = ROOT / "research" / "INDEX.md"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_research_index", GEN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_committed_index_is_current():
    assert INDEX.exists(), "research/INDEX.md is missing — run the generator"
    assert INDEX.read_text() == _gen().build(), (
        "research/INDEX.md is stale. Run "
        "`.venv/bin/python scripts/gen_research_index.py`. Do not hand-edit it.")


def test_generation_is_deterministic():
    """Two runs must agree byte-for-byte, or the check above would flap and be
    disabled by whoever got tired of it first."""
    mod = _gen()
    assert mod.build() == mod.build()


def test_every_heading_in_the_record_appears():
    """Enumerated from scratch, NOT with the generator's own regex.

    The check this replaces did exactly that — `re.findall(r"^## (§...)")`,
    the generator's pattern copied verbatim — so it could only ever look for
    sections the generator had already found. It stayed green for the entire
    time the index was missing thirteen of sixty-four sections, one of whose
    verdicts was inverted. `ci.yml:55-69`: a check that cannot fail is worse
    than no check at all.
    """
    import re
    source = (ROOT / "knowledge" / "backtest_candidates.md").read_text()
    index = _gen().build()

    headings = re.findall(r"^(#{2,3}) (.+)$", source, re.M)
    h2_numbers = {re.match(r"^§(\d+)", t).group(1)
                  for lvl, t in headings
                  if lvl == "##" and re.match(r"^§(\d+)", t)}

    missing = []
    for lvl, text in headings:
        m = re.match(r"^§(\d+)", text)
        if lvl == "###":
            # An H3 is a section only when its base number has no H2 anywhere.
            if not m or m.group(1) in h2_numbers:
                continue
        key = text.split(" — ")[0].split(" – ")[0].split(" (")[0].strip()
        if key.startswith("§") or re.match(r"^\d+\.", key):
            key = key.split()[0]
        if f"| {key} |" not in index:
            missing.append(key)
    assert not missing, f"headings absent from the index: {missing}"


def test_the_previously_invisible_sections_are_all_present():
    """The thirteen the old regex could not see, named so a regression reads
    as a name rather than as a count that moved."""
    index = _gen().build()
    absent = [k for k in ("METHOD NOTE", "METHOD NOTE 2", "METHOD NOTE 3",
                          "METHOD NOTE 4", "METHOD NOTE 5", "PHASE 0",
                          "ALREADY-SEEN OBSERVATION", "1.", "2.", "3.", "4.",
                          "§30", "§14–§17")
              if f"| {k} |" not in index]
    assert not absent, f"regressed, no longer indexed: {absent}"


def test_the_section_range_heading_is_not_mangled():
    """`## §14–§17` used to emit `| §14– | §17 | — | pre-registered | — |` —
    the range split across the key and subject columns."""
    index = _gen().build()
    assert "| §14–§17 |" in index
    assert "| §14– |" not in index


def test_the_gate_result_column_holds_only_counts():
    """A ratio is arithmetic over verdict rows. A word there would be the
    index forming an opinion, which is the one thing it must not do."""
    import re
    for line in _gen().build().splitlines():
        if not line.startswith("| ") or line.startswith("| section |") \
                or line.startswith("|---"):
            continue
        cell = line.split("|")[5].strip()
        assert re.fullmatch(r"\d+/\d+|—", cell), \
            f"gate result is not a count: {cell!r} in {line}"


def test_the_heading_column_still_reads_pre_registered_for_57_58_59():
    """THE ANTI-INFERENCE TEST. §57, §58 and §59 were rejected 0/4, 0/8 and
    0/8, and their `heading says` column must go on saying `pre-registered`.

    Making `classify()` consult verdicts.jsonl to 'fix' that would be the
    helpful change that turns this index into a second source of truth. The
    two columns sit side by side and are allowed to disagree; the disagreement
    is the signal.
    """
    index = _gen().build()
    for section, gate in (("§57", "0/4"), ("§58", "0/8"), ("§59", "0/8")):
        row = next(ln for ln in index.splitlines()
                   if ln.startswith(f"| {section} |"))
        assert "| pre-registered |" in row, (
            f"{section}'s heading column stopped quoting its heading — "
            f"something started inferring")
        assert f"| {gate} |" in row


def test_a_negated_verdict_is_not_read_as_its_opposite():
    """`### §30 CANDIDATE — tighter down-regime gross cap. NOT ADOPTED.`
    scored `adopted` for as long as this generator existed. Nobody saw it
    because §30 is an H3 and the regex never reached it."""
    mod = _gen()
    assert mod.classify("§99 — thing. NOT ADOPTED.")[1] == "not adopted"
    assert mod.classify("§99 — thing: NOT REGISTERED.")[1] == "not registered"
    assert mod.classify("§99 — **NOT VALIDATED.**")[1] == "not validated"
    row = next(ln for ln in _gen().build().splitlines()
               if ln.startswith("| §30 |"))
    assert "| not adopted |" in row and "| adopted |" not in row


def test_an_adoption_that_was_reverted_reports_both():
    """§3 is `ADOPTED 2026-07-16, REVERTED 2026-07-17`. Reporting only the
    adoption would describe a configuration the bot has not run since July."""
    mod = _gen()
    assert mod.classify("§99 — x — ADOPTED 2026-07-16, REVERTED 2026-07-17")[1] \
        == "adopted, reverted"
    assert "| adopted, reverted |" in _gen().build()


def test_the_index_does_not_invent_verdicts():
    """The verdict column is read verbatim off each heading. An index that
    formed its own opinion of the record would be a second source of truth, and
    the whole point of the append-only log is that there is only one."""
    mod = _gen()
    assert mod.classify("§99 — something (2026-01-01)") == ("—", "—")
    assert mod.classify("§99 — thing — ADOPTED")[1] == "adopted"
    assert mod.classify("§99 — thing — PRE-REGISTERED, NOT YET RUN")[1] \
        == "not yet run", "'NOT YET RUN' must not be read as a run result"
    assert mod.classify("§99 — ADOPTED for a, REJECTED for b")[1] == "split", \
        "a heading naming two outcomes is both, not the first one"


@pytest.mark.parametrize("claim", ["EDGE", "CAPACITY", "METHOD", "DIAGNOSTIC"])
def test_claim_types_are_recognised(claim):
    assert _gen().classify(f"§99 — thing ({claim}, K=15)")[0] == claim
