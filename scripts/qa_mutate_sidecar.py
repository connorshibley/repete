#!/usr/bin/env python3
"""Mutate a QA render's SERVED sidecar so the live page swaps — data-level.

Why this exists. The F-04 class (controls dying when a poll swaps their
region) can only be observed by making the page actually swap, and the first
poll is a no-op by construction (the server-rendered hash matches the
sidecar). qa_render's principle is that state is forced through the DATA the
page consumes — --age-hours, --break-sidecar — never by patching the emitted
page, which would test a page nobody ships. This is the third data lever: an
injected decisions row (filterable, class f-exec) and a changed hero figure,
with the hash recomputed so the next poll swaps both regions.

Used by the 2026-08-30 browser audit (docs/qa_findings.md, browser evidence
lines). Committed so the next pass drives the same lever instead of
reinventing the hash arithmetic inline — and mis-reinventing it, which is
Method note 1's false-pass shape: an instrument that shares no arithmetic
with the thing it measures cannot agree with it by accident.

    python scripts/qa_mutate_sidecar.py --sidecar /tmp/site/full/dashboard_data.json
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

INJECTED_ROW = (
    '<tr class="f-exec r-exec"><td>{date}</td><td><b>QAINJ</b></td>'
    '<td>buy</td><td>tsmom</td><td><span class=badge>approve</span></td>'
    '<td>1.0</td><td>INJECTED-BY-QA</td></tr>')


def _guard_not_live(path: str) -> None:
    """Refuse to touch a sidecar that lives anywhere near live state. Same
    posture as qa_fixture._guard_not_live: a QA lever pointed at production
    artifacts is how the 2026-07-28 overwrite happened."""
    ab = os.path.abspath(path)
    for marker in (os.sep + ".site" + os.sep, os.sep + "memory" + os.sep):
        if marker in ab:
            raise SystemExit(f"refusing: {ab} is inside a live directory")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.dirname(ab) == repo:
        raise SystemExit(f"refusing: {ab} is a repo-root artifact")


def recompute_hash(regions: dict) -> str:
    """MUST match dashboard.render's sidecar stamp byte-for-byte.

    tests/test_qa_mutate_sidecar.py asserts this against a REAL render's
    output, not against this function's own arithmetic (Method note 1)."""
    return hashlib.sha256(
        json.dumps(regions, sort_keys=True).encode()).hexdigest()[:16]


def mutate(path: str) -> dict:
    _guard_not_live(path)
    with open(path) as f:
        d = json.load(f)
    regions = d["regions"]
    today = datetime.date.today().isoformat()
    m = re.search(r"(<tbody[^>]*>)", regions["decisions"])
    if not m:
        raise SystemExit("no <tbody> in the decisions region")
    regions["decisions"] = regions["decisions"].replace(
        m.group(1), m.group(1) + INJECTED_ROW.format(date=today), 1)
    d["hash"] = recompute_hash(regions)
    d["generated_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(d, f)
    return {"hash": d["hash"], "injected": "QAINJ"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sidecar", required=True)
    args = ap.parse_args()
    print(json.dumps(mutate(args.sidecar)), file=sys.stdout)
