#!/usr/bin/env python3
"""One registry for acceptance criteria, so a criterion cannot be documented
and quietly never run.

scripts/qa_sweep.py grew a `check()` decorator and a module-level RESULTS list
that nothing ever called — `run_all()` uses a local `rec()` with inline string
IDs instead. The effect was that there was no importable list of the publisher's
35 criteria: anything wanting to enumerate them had to parse the source, and
nothing could tell the difference between "this criterion passed" and "this
criterion was deleted".

This is that decorator, made real. Importing a sweep module registers its
criteria without executing any of them, so `tests/test_qa_inventory_is_current.py`
can compare the registry against docs/qa_inventory.md with no fixture, no
network and no rendered site.

    from qa_criteria import criterion, REGISTRY

    @criterion("SITE-DASH-CHIP-01", "dashboard", "visitor",
               "every filter chip matches at least one row",
               profiles=("full",))
    def _chips(site):
        return ok, evidence
"""
from __future__ import annotations

REGISTRY: list[dict] = []


def criterion(cid: str, surface: str, role: str, statement: str, *,
              profiles: tuple[str, ...] = ("full",),
              edge: str = ""):
    """Register one acceptance criterion.

    The body takes the parsed site and returns (ok: bool, evidence: str). The
    evidence string is not decoration — a failure with no evidence cannot be
    reproduced, and this whole harness exists to produce reproducible bugs.
    """
    def deco(fn):
        REGISTRY.append({"id": cid, "surface": surface, "role": role,
                         "statement": statement, "profiles": profiles,
                         "edge": edge, "fn": fn})
        return fn
    return deco


def for_profile(profile: str) -> list[dict]:
    return [c for c in REGISTRY if profile in c["profiles"]]


def ids() -> list[str]:
    return [c["id"] for c in REGISTRY]
