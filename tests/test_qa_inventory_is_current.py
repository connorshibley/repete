"""docs/qa_inventory.md must describe the criteria that actually run.

Pinned in BOTH directions, which is the only version worth having:

  - every registered criterion appears in the doc, so deleting a check does
    not quietly shrink the coverage the doc advertises;
  - every criterion ID in the doc maps to something that runs, so a criterion
    cannot be documented and never executed. That failure mode is worse than
    an admitted gap, because it reads as coverage.

Built on the technique in tests/test_runbook_accuracy.py and the prose-vs-table
discipline in tests/test_doc_counts.py — this project's own answer to documents
that drift away from the code they describe. The divergence register's summary
table undercounted open entries five-to-one until 2026-08-06 for exactly this
reason.

Importing a sweep module REGISTERS its criteria without executing any of them,
so this needs no fixture, no rendered site and no network.
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, os.path.join(REPO, "src"))

import qa_site_sweep  # noqa: F401,E402  (import registers the criteria)
from qa_criteria import REGISTRY  # noqa: E402

DOC = os.path.join(REPO, "docs", "qa_inventory.md")
SWEEP = os.path.join(REPO, "scripts", "qa_sweep.py")
FINDINGS = os.path.join(REPO, "docs", "qa_findings.md")


def _doc():
    with open(DOC) as f:
        return f.read()


def _doc_ids():
    """Every `BACKTICKED-ID` in the doc that looks like a criterion."""
    # Lowercase suffixes are real IDs (TIER-LEAK-free, AUTHZ-unsub), so the
    # character class has to admit them.
    return set(re.findall(r"`((?:SITE|PUB|LEG|AUTH|AUTHZ|TIER|TIME|GATE|"
                          r"WEBHOOK|UNSUB|RO|TOK|NORM|RL|SCALE|XSURF|DIGEST)"
                          r"[A-Za-z0-9-]*)`", _doc()))


def test_every_registered_criterion_is_documented():
    missing = sorted({c["id"] for c in REGISTRY} - _doc_ids())
    assert not missing, (
        f"these criteria run but are not in docs/qa_inventory.md: {missing}")


def test_every_documented_site_criterion_actually_runs():
    """The direction that matters. A criterion in the doc with no
    implementation is a claim of coverage that nothing backs."""
    registered = {c["id"] for c in REGISTRY}
    documented = {i for i in _doc_ids() if i.startswith("SITE-")}
    # SITE-BROWSER-* are browser criteria by design; they are listed in their
    # own table with recorded evidence and must NOT be in the python registry.
    browser = {i for i in documented if i.startswith("SITE-BROWSER-")}
    orphans = sorted(documented - browser - registered)
    assert not orphans, (
        f"documented but not implemented: {orphans}. Either implement them or "
        f"move them to the browser table with evidence.")


def test_browser_criteria_are_declared_as_browser_not_silently_skipped():
    doc = _doc()
    assert "## Static site — requires a real browser" in doc
    for i in sorted({x for x in _doc_ids() if x.startswith("SITE-BROWSER-")}):
        assert i in doc
    assert "## Known coverage gaps" in doc, (
        "the gaps section is where honest coverage lives; do not delete it")


def test_no_criterion_id_is_used_twice():
    ids = [c["id"] for c in REGISTRY]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate criterion ids: {dupes}"


def test_the_publisher_ids_are_reused_verbatim():
    """Renaming a publisher criterion would orphan every reference in
    scripts/qa_sweep.py and tests/test_publisher.py. There is no importable
    registry for those 35 — run_all() uses a local rec() with inline strings —
    so this greps the source, deliberately."""
    with open(SWEEP) as f:
        in_sweep = set(re.findall(r'rec\("([^"]+)"', f.read()))
    assert in_sweep, "no publisher criteria found — did rec() get renamed?"
    missing = sorted(in_sweep - _doc_ids())
    assert not missing, (
        f"publisher criteria absent from the inventory: {missing}")


def test_every_criterion_states_a_property_not_a_mechanism():
    """A criterion phrased as 'calls function X' passes when the function is
    called and fails when it is renamed, which measures the code rather than
    the product. Cheap heuristic, but it caught the first draft of
    SITE-DASH-BIND-01, which asserted where elements sat in the DOM rather
    than that the handlers survived."""
    for c in REGISTRY:
        s = c["statement"]
        assert not s.startswith(("calls ", "uses ", "contains ")), (
            f"{c['id']} describes a mechanism, not an acceptance property: {s}")
        assert len(s) > 25, f"{c['id']} statement is too vague to falsify: {s}"


def test_every_finding_in_the_register_has_a_status_and_a_closer():
    """docs/qa_findings.md's own rule: CLOSED means a test would fail if it
    reopened, not 'fixed in code'. Same discipline as docs/divergences.md,
    whose summary table was wrong about its own open count until 2026-08-06."""
    with open(FINDINGS) as f:
        body = f.read()
    rows = re.findall(r"^\| (F-\d+) \|(.+)$", body, re.M)
    assert len(rows) >= 10, f"only {len(rows)} findings parsed — table moved?"
    for fid, rest in rows:
        cells = [c.strip() for c in rest.split("|")]
        status = next((c for c in cells if c in ("OPEN", "CLOSED")), None)
        assert status, f"{fid} has no OPEN/CLOSED status"
        if status == "CLOSED":
            closer = cells[-1] if cells[-1] else cells[-2]
            assert closer and closer not in ("—", "-"), (
                f"{fid} is CLOSED with no named test; 'fixed in code' is not "
                f"closed in this repo")


def test_the_open_count_in_the_prose_matches_the_table():
    with open(FINDINGS) as f:
        body = f.read()
    actual = len(re.findall(r"^\| F-\d+ \|[^|]*\|[^|]*\|[^|]*\| OPEN \|",
                            body, re.M))
    claimed = re.search(r"Open: \*\*(\d+)\*\*", body)
    assert claimed, "the findings register no longer states an open count"
    assert int(claimed.group(1)) == actual, (
        f"register says {claimed.group(1)} open, table has {actual}")
