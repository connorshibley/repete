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


def test_every_section_of_the_record_appears():
    import re
    source = (ROOT / "knowledge" / "backtest_candidates.md").read_text()
    sections = re.findall(r"^## (§[\d–—\-]+[a-z]?)", source, re.M)
    index = INDEX.read_text()
    missing = [s for s in sections if f"| {s} |" not in index]
    assert not missing, f"sections absent from the index: {missing}"


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
