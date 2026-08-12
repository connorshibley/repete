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

def _shipped_budget() -> int:
    """The budget `config.yaml` actually ships, not the test fixture's.

    This distinction is the whole point. Until 2026-08-11 the test below built
    a `Memory` from `tests/conftest.py`'s fixture, whose `llm` block carries no
    `knowledge_max_context_chars` — so `knowledge_budget()` fell through to
    `learning.max_context_chars // 4` and returned 1000 no matter what the
    shipped config said. A guard on the shipped file that cannot see the
    shipped budget is checking a number nobody deploys, and the only way to
    make it pass would have been to edit the fixture: exactly the
    "make the test agree with the code" move `bot-prove-it` exists to catch.
    """
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    return int(cfg["llm"]["knowledge_max_context_chars"])


def test_the_shipped_principles_file_fits_its_budget():
    """THE LOAD-BEARING ONE. Truncation here is silent: `text[:cap]` drops the
    tail and returns happily, so the judge simply stops seeing the last
    principle and nothing anywhere says so."""
    text = open(PRINCIPLES).read().strip()
    budget = _shipped_budget()
    assert len(text) <= budget, (
        f"{PRINCIPLES} is {len(text)} chars against a {budget}-char budget — "
        f"the last {len(text) - budget} would be silently dropped. Tighten the "
        f"wording, or raise llm.knowledge_max_context_chars AND check "
        f"learning.context_budgets still fits its total (§61).")


def test_the_shipped_principles_file_has_room_to_grow():
    """'Fits' is not enough — it fitted at exactly 1000 of 1000 for weeks.

    Zero headroom means the file is full and nobody is told; the next
    principle anyone writes is the one that vanishes. §61 raised the budget
    precisely because the owner asked for knowledge to be able to grow, so the
    headroom is the thing worth asserting.
    """
    text = open(PRINCIPLES).read().strip()
    headroom = _shipped_budget() - len(text)
    assert headroom >= 300, (
        f"{PRINCIPLES} has {headroom} chars of headroom against its shipped "
        f"budget — knowledge cannot meaningfully grow. Raise "
        f"llm.knowledge_max_context_chars and re-check the budget sum.")


def test_the_fixture_and_the_shipped_config_really_do_differ(tmp_path, cfg):
    """The bug this file shipped with, pinned so it cannot return silently.

    `tests/conftest.py` carries a minimal `llm` block with no
    `knowledge_max_context_chars`, so a `Memory` built from it derives
    `learning.max_context_chars // 4`. That is fine for behaviour tests and
    wrong for a claim about what is deployed. If these two numbers ever
    coincide again, the guard above stops being able to tell which one it read
    — and that is the state it spent weeks in.
    """
    fixture_budget = _mem(tmp_path, _copy(cfg)).knowledge_budget()
    assert fixture_budget != _shipped_budget(), (
        f"the conftest fixture and config.yaml both say {fixture_budget}; the "
        f"shipped-file guard can no longer distinguish them, which is how it "
        f"came to be checking a number nobody deploys")


def test_every_principle_reaches_the_judge_intact(tmp_path, cfg):
    """Fits-in-budget is necessary, not sufficient — the assembled block is
    what the judge reads. Assert the LAST line survives, since that is the one
    truncation takes first.

    Built on the SHIPPED budget, for the same reason as the guard above: this
    is a claim about the deployed file, and the conftest fixture derives
    `learning.max_context_chars // 4` = 1000 regardless of what config.yaml
    says. Caught by §61's mutation control — adding an eleventh principle left
    every other test green and turned this one red, which was the file
    reporting that knowledge still could not grow.
    """
    c = _copy(cfg)
    c["llm"]["knowledge_max_context_chars"] = _shipped_budget()
    mem = _mem(tmp_path, c)
    lines = [ln for ln in open(PRINCIPLES).read().strip().splitlines()
             if ln.strip()]
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
