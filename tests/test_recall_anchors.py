"""`research/anchors.json` is the first mechanical enforcement of append-only.

The record's whole evidentiary value is that a claim written before its result,
in a file that only ever grows, cannot be quietly reworded afterwards. Until
this manifest existed, nothing in the repo would have noticed a retroactive
edit to §20 — the chronology was an honour system with a git history nobody
diffs section by section.

The pair of tests that matter are the last two: editing a closed section's body
must go red, and appending a whole new section must NOT. A guard that fired on
every append would be turned off within a week.
"""
import json

import pytest

import recall


def test_the_committed_manifest_is_current():
    assert recall.render_anchors(recall.build_anchors()) == recall._read(
        recall.ANCHORS), (
        "research/anchors.json is stale. Regenerate with "
        "`.venv/bin/python scripts/recall.py anchors`.")


def test_generation_is_deterministic():
    assert recall.build_anchors() == recall.build_anchors()


def test_the_manifest_covers_every_section_of_the_record():
    keys = [s["key"] for s in recall.sections()]
    manifest = [e["key"] for e in recall.load_anchors()["sections"]]
    assert manifest == keys


def test_identity_is_the_hash_and_not_the_line_number():
    """`start_line` is a courtesy and shifts whenever anything above a section
    grows. If it were identity, every append would invalidate every anchor."""
    a = recall.build_anchors()["sections"]
    assert all(e["body_sha256"] for e in a)
    assert len({e["body_sha256"] for e in a}) == len(a), (
        "two sections hash the same — the body slice is wrong")


def test_duplicate_keys_are_kept_apart_by_their_hashes():
    """The record carries two `## METHOD NOTE` headings. Nothing invents a
    disambiguator for them; they are distinguished by what they say."""
    notes = [e for e in recall.build_anchors()["sections"]
             if e["key"] == "METHOD NOTE"]
    assert len(notes) == 2
    assert notes[0]["body_sha256"] != notes[1]["body_sha256"]


def _sections_from(text, monkeypatch):
    monkeypatch.setattr(recall, "_read",
                        lambda rel, _t=text, _o=recall._read:
                        _t if rel == recall.RECORD else _o(rel))
    return {s["key"] + str(s["start_line"]): s["body_sha256"]
            for s in recall.sections()}


def test_editing_a_closed_sections_body_changes_its_hash(monkeypatch):
    """The mutation this manifest exists to catch."""
    original = recall._read(recall.RECORD)
    before = _sections_from(original, monkeypatch)

    marker = "## §20 — RE-GATE UNDER THE ENSEMBLE SIMULATOR"
    i = original.index(marker)
    j = original.index("\n", original.index("\n", i) + 1)
    tampered = original[:j] + "\nA sentence nobody wrote at the time." + original[j:]
    after = _sections_from(tampered, monkeypatch)

    changed = [k for k in before if before[k] != after.get(k)]
    assert any(k.startswith("§20") for k in changed), (
        "a word inserted into §20's body left its hash unmoved — the manifest "
        "would not notice the record being reworded after the fact")


def test_appending_a_new_section_leaves_every_existing_hash_alone(monkeypatch):
    """The CONTROL. This must survive, or the guard is one that fires on
    ordinary work and gets disabled by whoever tires of it first."""
    original = recall._read(recall.RECORD)
    before = _sections_from(original, monkeypatch)

    appended = original + (
        "\n## §99 — A LATER SECTION (DIAGNOSTIC, K=15, 2026-12-01)\n\n"
        "Body text.\n\n<!-- recall: section=§99 specs= -->\n")
    after = _sections_from(appended, monkeypatch)

    moved = [k for k in before if before[k] != after.get(k)]
    assert not moved, (
        f"appending a section moved the hash of {moved} — the manifest would "
        f"cry wolf on every ordinary append")


def test_a_trailer_that_contradicts_the_registrations_is_refused(monkeypatch):
    """The trailer is a third derivation of the join, not a decoration."""
    original = recall._read(recall.RECORD)
    tampered = original + (
        "\n## §99 — A LATER SECTION (2026-12-01)\n\n"
        "<!-- recall: section=§99 specs=s99a,s99b -->\n")
    monkeypatch.setattr(recall, "_read",
                        lambda rel, _t=tampered, _o=recall._read:
                        _t if rel == recall.RECORD else _o(rel))
    with pytest.raises(recall.RecallError, match="trailer declares"):
        recall.build_anchors()


def test_a_new_section_without_a_trailer_is_caught_by_audit(monkeypatch):
    """Required forwards from §60, never back-filled into §1-§59: back-filling
    would edit the append-only record, which is the one thing it must not
    permit."""
    original = recall._read(recall.RECORD)
    tampered = original + "\n## §60 — A LATER SECTION (2026-12-01)\n\nBody.\n"
    monkeypatch.setattr(recall, "_read",
                        lambda rel, _t=tampered, _o=recall._read:
                        _t if rel == recall.RECORD else _o(rel))
    assert any("trailer" in p and "§60" in p for p in recall.audit())


def test_sections_before_the_cutoff_need_no_trailer():
    """§1-§59 carry none, and the shipped record audits clean."""
    assert recall.TRAILER_REQUIRED_FROM == 60
    assert all(s["trailer"] is None for s in recall.sections()
               if (recall._leading_number(s["key"]) or 0) < 60)
    assert recall.audit() == []


def test_the_manifest_is_valid_json_with_a_stable_shape():
    doc = json.loads(recall._read(recall.ANCHORS))
    assert doc["source"] == recall.RECORD
    for e in doc["sections"]:
        assert set(e) == {"key", "heading", "start_line", "body_sha256",
                          "subheadings", "specs", "spec_source"}
