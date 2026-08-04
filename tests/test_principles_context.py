"""`knowledge/principles.md` reaches the live judge and nothing else (#15).

Why this file exists
--------------------
`memory.knowledge_block()` has fed curated principles to every live judge call
since the initial commit on 2026-07-16, and `judge_model` — a distribution
stand-in with no prompt — has never been able to see them. That is divergence
#15, and it sat unregistered for nineteen days, through the creation of the
divergence register itself.

Registering it is not enough. `docs/divergences.md` says a divergence is CLOSED
only when a test would fail if it reopened; the same standard applies to one
that stays open by construction, because the two properties that make it *safe*
have to be pinned or the safety is a story:

  * **it is suppressible.** Unsetting `llm.knowledge_path` must restore
    byte-identical judge context. A block that cannot be switched off cleanly
    cannot be withdrawn when it turns out to be wrong — and this one can never
    be tested by a gate, so withdrawal is the only correction available.
  * **it does not evict.** The whole memory block is capped, knowledge is
    assembled BEFORE the scoreboard, calibration and CURRENT REGIME, and a block
    without its own budget quietly eats the tail. The damage would show up as
    worse judgments, never as a failing diff.

And one property that makes it *usable*:

  * **the shipped file fits its budget.** It was at 906 of 1000 chars with the
    limit derived rather than named, so the next principle anyone added would
    have been silently dropped. That is now a red build.
"""
from __future__ import annotations

import json

import pytest
from ledger import Ledger
from memory import Memory

PRINCIPLES = "knowledge/principles.md"


def _mem(tmp_path, cfg, knowledge_path=PRINCIPLES, budget=None):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "memory" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "memory" / "judgments.jsonl")
    cfg.setdefault("news", {})["context_path"] = str(tmp_path / "no_context.json")
    cfg["news"]["memory"] = {"enabled": False}
    if knowledge_path is None:
        cfg["llm"].pop("knowledge_path", None)
    else:
        cfg["llm"]["knowledge_path"] = knowledge_path
    if budget is not None:
        cfg["llm"]["knowledge_max_context_chars"] = budget
    return Memory(cfg, Ledger(cfg["memory"]["ledger_path"]))


def _copy(cfg):
    return json.loads(json.dumps(cfg))


# ------------------------------------------------------- the shipped file

def test_the_shipped_principles_file_fits_its_budget(tmp_path, cfg):
    """THE LOAD-BEARING ONE. Truncation here is silent: `text[:cap]` drops the
    tail and returns happily, so the judge simply stops seeing the last
    principle and nothing anywhere says so."""
    mem = _mem(tmp_path, _copy(cfg))
    text = open(PRINCIPLES).read().strip()
    budget = mem.knowledge_budget()
    assert len(text) <= budget, (
        f"{PRINCIPLES} is {len(text)} chars against a {budget}-char budget — "
        f"the last {len(text) - budget} would be silently dropped. Tighten the "
        f"wording; do NOT raise the budget without reading the note in "
        f"config.yaml about what comes off the tail.")


def test_every_principle_reaches_the_judge_intact(tmp_path, cfg):
    """Fits-in-budget is necessary, not sufficient — the assembled block is
    what the judge reads. Assert the LAST line survives, since that is the one
    truncation takes first."""
    mem = _mem(tmp_path, _copy(cfg))
    lines = [l for l in open(PRINCIPLES).read().strip().splitlines() if l.strip()]
    block = mem.knowledge_block()
    for line in lines:
        assert line in block, f"principle missing from the judge block: {line!r}"


def test_the_principles_are_labelled_unverified(tmp_path, cfg):
    """They are not evidence. The judge has realized trade outcomes and
    validated lessons with n= counts; this block has neither, and the label is
    the only thing that ranks it below them."""
    block = _mem(tmp_path, _copy(cfg)).knowledge_block()
    assert "unverified" in block
    assert "weigh below realized" in block


# ------------------------------------------------------- suppressibility

def test_unset_knowledge_path_gives_byte_identical_context(tmp_path, cfg):
    """The rollback property, and the ONLY correction available for #15 — this
    block can never be gated, so it can never be rejected by evidence either."""
    on = _mem(tmp_path / "on", _copy(cfg)).context_for_llm(symbol="SPY")
    off = _mem(tmp_path / "off", _copy(cfg),
               knowledge_path=None).context_for_llm(symbol="SPY")
    assert "KNOWLEDGE" in on
    assert "KNOWLEDGE" not in off
    assert on.replace(_mem(tmp_path / "k", _copy(cfg)).knowledge_block() + "\n\n",
                      "") == off


@pytest.mark.parametrize("path", [None, "", "knowledge/does_not_exist.md"])
def test_a_missing_or_unset_file_degrades_to_empty(tmp_path, cfg, path):
    """Fail-soft, like the rest of the memory layer. A deleted principles file
    must not raise into a trading path."""
    assert _mem(tmp_path, _copy(cfg), knowledge_path=path).knowledge_block() == ""


def test_an_unreadable_path_never_raises(tmp_path, cfg):
    assert _mem(tmp_path, _copy(cfg),
                knowledge_path=str(tmp_path)).knowledge_block() == ""


# ------------------------------------------------------- eviction guard

def test_principles_do_not_shrink_the_lesson_block(tmp_path, cfg):
    """The silent-eviction guard, same shape as the news-memory one. Knowledge
    is assembled before the scoreboard, calibration and CURRENT REGIME, so if it
    ever outgrows its share those are what disappear."""
    off = _mem(tmp_path / "a", _copy(cfg),
               knowledge_path=None).context_for_llm(symbol="SPY")
    on = _mem(tmp_path / "b", _copy(cfg)).context_for_llm(symbol="SPY")
    for tail in ("CURRENT REGIME:", "VALIDATED LESSONS"):
        assert tail in off
        assert tail in on, f"{tail} was evicted once principles were present"


# ------------------------------------------------------- the budget itself

def test_the_budget_defaults_to_the_historical_derived_value(tmp_path, cfg):
    """Naming the budget must not CHANGE it. Before 2026-08-04 it was
    `learning.max_context_chars // 4`; a config that does not set the new key
    has to land on exactly that, or making the constraint visible would have
    silently moved it."""
    c = _copy(cfg)
    c["learning"]["max_context_chars"] = 4000
    mem = _mem(tmp_path, c)
    mem.llm_cfg.pop("knowledge_max_context_chars", None)
    assert mem.knowledge_budget() == 1000


def test_an_explicit_budget_wins(tmp_path, cfg):
    mem = _mem(tmp_path, _copy(cfg), budget=120)
    assert mem.knowledge_budget() == 120
    assert len(mem.knowledge_block()) <= 120 + 80   # + the label line
