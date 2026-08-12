"""The one rule, enforced: this tool quotes the record and never summarises it.

`scripts/gen_research_index.py` states why — *"an index that formed its own
opinion of the record would be a second source of truth"* — and a rule stated
only in a docstring is the shape of thing this repo has twice found rotting.

So every string `recall` emits must be one of

  (a) a byte-exact span of the source file it names,
  (b) an identifier, count, path, ratio or ISO date,
  (c) a member of `recall.LABELS`.

These tests walk real output and fail on a fourth. The mutation that proves
they can fail is adding a single composed f-string to any payload.
"""
import re

import pytest

import recall

# (b): identifiers, counts, paths, ratios, dates, shas, arm and clause names.
SCALAR = re.compile(
    r"^(?:[a-z0-9_.\-/§]+|[A-Z_]+|\(\w\)|\d+/\d+|—|"
    r"\d{4}-\d{2}-\d{2}[T0-9:.+\-]*|[0-9a-f]{6,64}|"
    r"[\w.\-]+\.(?:md|yaml|jsonl|json|gz|py))$")


def _is_locator_part(part: str, haystack: str) -> bool:
    """A locator part is a quoted span, or a run of scalars.

    `### The result` is the first. `failure_mode 0` and `(a) min_trades` are
    the second — every whitespace-separated token is itself an identifier or a
    number, so the part names a place without asserting anything about it.
    """
    if part in haystack:
        return True
    return bool(part) and all(SCALAR.match(t) for t in part.split())


def _haystack():
    """Every span a quote may legitimately have come from.

    The markdown files contribute their raw bytes. The JSONL files contribute
    their PARSED values, not their raw bytes: a prior is stored JSON-escaped
    and its YAML block scalar folded newlines into spaces, so the frozen value
    is what `spec["prior"]` returns, not what a grep of the file would find.
    That parsed value is also the thing `gatespec.canonical_sha256` hashed, so
    it is the goalpost in the sense that matters.
    """
    parts = [recall._read(recall.RECORD), recall._read(recall.DIVERGENCES)]
    regs = recall.gatespec.registrations(
        recall._path(recall.gatespec.REGISTRATIONS))
    for reg in regs.values():
        parts.append(reg["spec"]["prior"])
        parts.append(reg["spec"]["title"])
        parts.extend(reg["spec"].get("failure_modes") or [])
    for row in recall.verdicts():
        for c in recall.clause_lines(row):
            if c.get("detail"):
                parts.append(c["detail"])
    return "\n".join(parts)


def _walk(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _walk(v, path + (k,))
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, path)
    elif isinstance(node, str):
        yield path[-1] if path else "", node


def _payloads():
    return {
        "search": recall.search(["drawdown", "latch"], limit=5),
        "search-priors": recall.search(["survivorship"], corpus="priors",
                                       limit=5),
        "priors": recall.priors(),
        "calibrate": recall.calibrate(),
        "anchors": recall.build_anchors(),
    }


@pytest.mark.parametrize("name", sorted(_payloads()))
def test_no_synthesised_string_leaks_into_output(name):
    """The fourth category does not exist. Add one composed sentence to any
    payload and this goes red, which is the whole point of it."""
    haystack = _haystack()
    offenders = []
    for key, value in _walk(_payloads()[name]):
        if not value:
            continue
        if key in recall.VERBATIM_FIELDS:
            continue
        if key in recall.LOCATOR_FIELDS:
            for part in value.split(recall.LOCATOR_SEP):
                if not _is_locator_part(part, haystack):
                    offenders.append((key, part[:90]))
            continue
        if value in recall.LABELS or SCALAR.match(value):
            continue
        offenders.append((key, value[:90]))
    assert not offenders, (
        f"{name}: strings that are neither a quoted span, a locator built from "
        f"quoted spans, a scalar, nor a member of recall.LABELS: {offenders}")


@pytest.mark.parametrize("name", sorted(_payloads()))
def test_every_verbatim_field_appears_byte_exact_in_its_source(name):
    """Not 'close to'. An excerpt that normalised an em-dash, trimmed a space
    or lower-cased a heading would read as a quote while being a paraphrase."""
    haystack = _haystack()
    bad = []
    for key, value in _walk(_payloads()[name]):
        if key not in recall.VERBATIM_FIELDS or not value:
            continue
        if value not in haystack:
            bad.append((key, value[:90]))
    assert not bad, f"{name}: fields that are not byte-exact in any source: {bad}"


def test_every_label_is_actually_reachable():
    """A LABELS set that accumulates strings nothing emits stops being a
    whitelist and becomes a place to hide one.

    Checked by reading the two source files rather than by running fixtures:
    several labels (`no verdict recorded`, `id-only`, the four prior
    directions) are only reachable in states the shipped record does not
    currently contain, and a test that demanded those states would push
    somebody to fabricate them.
    """
    # Whitespace and quote characters are stripped from both sides: the long
    # labels are written as adjacent string literals across several lines, so a
    # naive substring search would report them dead while they are emitted.
    def squash(t):
        return re.sub(r"[\s\"'\\]+", "", t)

    source = squash(recall._read("src/recall.py")
                    + recall._read("scripts/recall.py")
                    + recall._read("scripts/gen_research_index.py"))
    dead = [lab for lab in recall.LABELS if squash(lab) not in source]
    assert not dead, f"LABELS entries no code path emits: {sorted(dead)}"


def test_search_does_not_stem_or_expand():
    """A stemmer is a small inference and it makes a hit unexplainable. The
    cost is real: 'contraction' must not find 'contract'."""
    hits = recall.search(["contracting"], corpus="priors", limit=50)
    assert hits["results"] == [], (
        "search matched a token the query did not contain — something is "
        "stemming or expanding, and a reader can no longer tell why a result "
        "ranked")


def test_every_hit_reports_why_it_matched():
    out = recall.search(["survivorship", "benchmark"], limit=8)
    assert out["results"]
    for r in out["results"]:
        assert r["matched_terms"], "a hit with no matched terms is unexplainable"
        for term in r["matched_terms"]:
            assert term in r["excerpt"].lower()


def test_search_states_the_failure_mode_it_cannot_bound():
    out = recall.search(["anything"], limit=1)
    assert out["note"] == "LEXICAL_ONLY"
    assert "different words will not appear" in out["footer"], (
        "false negatives are the expensive direction and this tool cannot "
        "bound them; saying so on every search is the only honest option")


def test_ranking_is_deterministic():
    a = recall.search(["drawdown", "latch"], limit=20)
    b = recall.search(["drawdown", "latch"], limit=20)
    assert [r["anchor"] for r in a["results"]] == [r["anchor"] for r in b["results"]]
