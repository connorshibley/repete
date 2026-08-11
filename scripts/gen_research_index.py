"""Generate `research/INDEX.md` — one line per § of the research record.

WHY (Phase 8, 2026-08-06)
-------------------------
`knowledge/backtest_candidates.md` is 5,674 chronological, append-only lines
across 46 sections. To an outsider it is unreadable, and **that is not a reason
to split it**: the chronology is the single strongest piece of evidence in the
repo that nothing was retrofitted. A claim written before its result, in a file
that only ever grows, cannot be quietly reworded after the fact.

So the log is left exactly as it is and gets a table of contents instead.

GENERATED, NEVER HAND-EDITED. A hand-maintained index of a 46-section file is
the divergence-table bug waiting to happen again — on 2026-08-06 that table was
found to be missing four of eighteen entries, three of them open, because
nothing regenerated it. `tests/test_research_index.py` regenerates this file and
fails if the committed copy differs, so it cannot drift.

Run: `.venv/bin/python scripts/gen_research_index.py`
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import recall  # noqa: E402

SOURCE = ROOT / "knowledge" / "backtest_candidates.md"
OUT = ROOT / "research" / "INDEX.md"

# Sections are parsed by `recall.sections()`, not by a regex here. The regex
# this file used to carry matched `^## (§...)` only, which silently dropped
# thirteen of sixty-four sections — §1-§4 (written `## 1.`), the five METHOD
# NOTEs, PHASE 0, ALREADY-SEEN OBSERVATION, and §30 (an H3) — and mangled
# `## §14–§17` into a row whose subject column read `§17`. Worse, the guard
# `tests/test_research_index.py` re-implemented this same regex, so it could
# not fail for any of them: a check that cannot fail, which `ci.yml:55-69`
# names as worse than no check at all.

FOOTNOTE_TAIL = ("several sections register a conjunction where one failure "
                 "sinks the claim, so §44 reads `3/4` and the claim failed.")

# Verdict words, checked longest-first so "NOT YET RUN" is not read as "RUN"
# and "PRE-REGISTERED, NOT YET RUN" does not score as ADOPTED.
#
# NEGATIONS COME FIRST, and they are not decoration. `### §30 CANDIDATE —
# tighter down-regime gross cap. NOT ADOPTED.` scored `adopted` for as long as
# this generator has existed; nobody saw it because §30 is an H3 and the old
# regex never reached it. Dropping a negation is not literalism, it is a false
# statement about the record.
VERDICTS = [
    ("NOT YET RUN", "not yet run"),
    ("NOT REGISTERED", "not registered"),
    ("NOT VALIDATED", "not validated"),
    ("NOT ADOPTED", "not adopted"),
    ("SPLIT VERDICT", "split"),
    ("PRE-REGISTERED", "pre-registered"),
    ("REJECTED", "rejected"),
    ("ADOPTED", "adopted"),
    ("BLOCKED", "blocked"),
    ("FROZEN", "frozen"),
    ("VALIDATED", "validated"),
    ("REFUTED", "refuted"),
    ("CLOSED", "closed"),
]

CLAIM_TYPES = ["EDGE", "CAPACITY", "METHOD", "DIAGNOSTIC"]


def classify(title: str) -> tuple[str, str]:
    """Return (claim_type, verdict) read off the heading itself.

    Deliberately literal. Inferring a verdict the heading does not state would
    make this index an opinion about the record rather than a view of it.
    """
    upper = title.upper()
    claim = next((c for c in CLAIM_TYPES if c in upper), "—")
    verdict = "—"
    # Case-insensitive, deliberately. A case-sensitive read was tried and was
    # wrong: fifteen headings write `(pre-registered 2026-08-09)` in lowercase,
    # and requiring capitals blanked every one of them. The record has no
    # typographic convention to lean on.
    #
    # The cost of matching prose is real and is left in rather than special-
    # cased away — `## METHOD NOTE — frozen snapshots required` reads `frozen`.
    # The column is named `heading says` for that reason, and `gate result`
    # sits beside it as the arithmetic answer.
    for needle, label in VERDICTS:
        if needle in upper:
            verdict = label
            break
    # A heading naming two outcomes ("ADOPTED for meanrev, REJECTED for tsmom")
    # is genuinely both; say so rather than picking the first.
    if "ADOPTED" in upper and "REJECTED" in upper:
        verdict = "split"
    # And one naming an outcome that was later undone states both facts. §3 is
    # `ADOPTED 2026-07-16, REVERTED 2026-07-17`; reporting only the adoption
    # would describe a configuration the bot has not run since July.
    if "ADOPTED" in upper and "REVERTED" in upper and "NOT ADOPTED" not in upper:
        verdict = "adopted, reverted"
    return claim, verdict


def date_of(title: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", title)
    return m.group(1) if m else "—"


def short(title: str, limit: int = 78) -> str:
    """Strip the date and verdict noise; keep the subject."""
    # Any parenthetical carrying a date — the date has its own column, and the
    # claim type has another, so repeating both in the subject wastes the width
    # that the actual subject needs.
    t = re.sub(r"\([^)]*\d{4}-\d{2}-\d{2}[^)]*\)", "", title)
    t = re.sub(r"\s*[—-]\s*(ADOPTED|REJECTED|PRE-REGISTERED|NOT YET RUN|"
               r"BLOCKED|FROZEN|VALIDATED|REFUTED|SPLIT VERDICT|"
               r"NEW STANDING DIAGNOSTIC).*$", "", t, flags=re.I)
    t = " ".join(t.split()).strip(" —-,")
    t = t.replace("|", "\\|")
    return (t[:limit - 1] + "…") if len(t) > limit else t


def build() -> str:
    rows = []
    for s in recall.sections():
        claim, verdict = classify(s["title"])
        rows.append((s["key"], short(s["title"]), claim, verdict,
                     recall.gate_result(s["key"]), date_of(s["title"])))

    lines = [
        "# Research index",
        "",
        "**Generated by `scripts/gen_research_index.py`. Do not edit by hand.**",
        "`tests/test_research_index.py` regenerates this file and fails if the",
        "committed copy differs.",
        "",
        f"One line per section of [`../knowledge/backtest_candidates.md`]"
        f"(../knowledge/backtest_candidates.md) — **{len(rows)} sections, "
        f"{len(SOURCE.read_text().splitlines()):,} lines**.",
        "",
        "That file is chronological and append-only, and it is deliberately NOT",
        "split. The chronology is the evidence that nothing was retrofitted: a",
        "claim written before its result, in a file that only ever grows, cannot",
        "be quietly reworded afterwards. This index is navigation, not a summary.",
        "",
        "**The last two columns are allowed to disagree, and the disagreement is",
        "the signal.** `heading says` is read verbatim off each heading — an",
        "index that formed its own opinion of the record would be a second",
        "source of truth. `gate result` counts verdict rows in",
        "[`verdicts.jsonl`](verdicts.jsonl) for the specs registered against",
        "that section. §57, §58 and §59 read `pre-registered` because that",
        "phrase is in their headings; they scored 0/4, 0/8 and 0/8. Neither",
        "column has been corrected — they are placed side by side.",
        "",
        "| section | subject | claim | heading says | gate result | date |",
        "|---|---|---|---|---|---|",
    ]
    for section, title, claim, verdict, gate, date in rows:
        lines.append(f"| {section} | {title} | {claim} | {verdict} | "
                     f"{gate} | {date} |")
    lines += [
        "",
        "A `gate result` of `—` means no spec was registered against that",
        f"section; the first registered section is "
        f"§{recall.first_registered_section()}. And a ratio is not a section",
        "verdict: " + FOOTNOTE_TAIL,
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    content = build()
    if "--check" in sys.argv:
        current = OUT.read_text() if OUT.exists() else ""
        if current != content:
            print("research/INDEX.md is STALE — run "
                  "`.venv/bin/python scripts/gen_research_index.py`")
            sys.exit(1)
        print("research/INDEX.md is current")
    else:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(content)
        print(f"wrote {OUT.relative_to(ROOT)} "
              f"({len(content.splitlines())} lines)")
