"""The judge's context budget — and the eviction that ran silently for months.

Measured 2026-08-11 against the live memory files: `context_for_llm` assembled
5,613 chars against `learning.max_context_chars: 4000`, and the bare
`ctx[:4000]` at the end dropped NEWS MEMORY, YOUR LAST RESOLVED CALLS, YOUR
RECENT CALIBRATION and CURRENT REGIME entirely, cutting TODAY'S MARKET CONTEXT
mid-word. Every judge call. Nothing marked the prompt, nothing logged, and no
test could fail.

The cause was arithmetic nobody had summed. Three budgets were DERIVED SHARES
of the total — lessons `// 2`, market context `// 4`, scoreboard `// 4` — which
is exactly 100% of the cap between them, before knowledge, news memory, the
book, the trades, calibration or the regime label got anything. Doubling the
total left it at exactly 100%, so the oversubscription was scale-invariant.

Two of the guards that should have caught it could not. Both
`test_principles_do_not_shrink_the_lesson_block` and
`test_unset_knowledge_path_gives_byte_identical_context` build a `Memory` on an
EMPTY `tmp_path` with news memory disabled, so the assembled context is a few
hundred chars against a 4,000 cap — eviction is impossible in that fixture at
any budget. Every test here that claims the cap bit carries a vacuity guard,
the shape `tests/test_news_memory.py:229` already established. §61.
"""
import json

import yaml

import memory as memory_mod
import preflight
from ledger import Ledger
from memory import Memory

SHIPPED = "config.yaml"


def _shipped_cfg():
    with open(SHIPPED) as f:
        return yaml.safe_load(f)


def _copy(cfg):
    return json.loads(json.dumps(cfg))


def _mem(tmp_path, cfg, n_trades=0, n_lessons=0, n_judgments=0):
    """A Memory whose stores can be made as full as a test needs.

    The point of the counts is to get the assembled context ANYWHERE NEAR the
    cap. A guard against eviction that runs on an empty store is asserting
    something that could not have been false — which is exactly the state
    `test_principles_do_not_shrink_the_lesson_block` was in while four blocks
    went missing in production.

    Live proportions, measured 2026-08-11: lessons 2,184 chars and the
    scoreboard 922 were the two biggest blocks after the book, so a fixture
    with trades but no lessons or judgments cannot reproduce the failure.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "judgments.jsonl")
    cfg.setdefault("news", {})["context_path"] = str(tmp_path / "nocontext.json")
    cfg["news"]["memory"] = {"enabled": False}
    led = Ledger(cfg["memory"]["ledger_path"])
    for i in range(n_trades):
        tid = led.log_decision(
            symbol="SPY", action="buy",
            reason="63-bar momentum +3.9% with price above SMA200 " + "x" * 40,
            indicators={"mom": 3.9}, llm_review=None, executed=True,
            entry_price=100.0, qty=10, regime="up/low", strategy="tsmom")
        pnl = -1.5 if i % 3 == 0 else 2.5
        led.close_trade(tid, exit_price=100.0 + pnl, pnl=pnl * 10,
                        pnl_pct=pnl, exit_reason="strategy_sell")

    mem = Memory(cfg, led)
    for i in range(n_lessons):
        mem.lessons.add_lesson(
            f"possible pattern (n={i}): momentum entries into a fading tape "
            f"give back more than they keep, and the stop is inside the noise",
            {"symbols": ["SPY"], "regime": "up/low", "strategy": "tsmom"},
            source=f"seed{i}")
    for i in range(n_judgments):
        mem.judgments.log_judgment(
            trade_id=f"j{i:03d}", symbol="SPY", action="buy",
            verdict="approve" if i % 2 else "downsize", scale=1.0 if i % 2 else 0.5,
            price=100.0, regime="up/low", kind="entry", executed=True,
            reasoning="setup is clean " + "y" * 30, strategy="tsmom")
    return mem


# ------------------------------------------------- the arithmetic that failed

def test_the_shipped_budgets_fit_their_total():
    """THE ONE THAT WOULD HAVE CAUGHT IT. Sum the named budgets, compare to
    the total. This was 6,966 chars over before §61 and nothing said so."""
    cfg = _shipped_cfg()
    over = memory_mod.budget_overage(cfg)
    budgets = memory_mod.context_budgets(cfg)
    assert over == 0, (
        f"learning context budgets oversubscribe their total by {over} chars: "
        f"{sum(v for v in budgets.values() if v is not None)} budgeted against "
        f"max_context_chars {cfg['learning']['max_context_chars']}. "
        f"context_for_llm slices the TAIL, so the judge loses the LAST blocks: "
        f"{list(memory_mod.CONTEXT_BLOCKS[-4:])}.")


def test_the_shipped_config_leaves_no_block_unbounded():
    """An unbudgeted block cannot be counted, so the sum above is only a lower
    bound while any exist. The book was the biggest one — `max_open_positions:
    0` means UNCAPPED (§29), so it can list every name in the universe."""
    unbounded = [k for k, v in memory_mod.context_budgets(_shipped_cfg()).items()
                 if v is None]
    assert not unbounded, f"unbudgeted context blocks: {unbounded}"


def test_every_block_the_assembler_writes_has_a_budget_entry():
    """A block added to `context_for_llm` without a line in `context_budgets`
    is the original bug in its next costume."""
    assert set(memory_mod.context_budgets(_shipped_cfg())) \
        == set(memory_mod.CONTEXT_BLOCKS)


def test_the_historical_derived_shares_summed_to_the_whole_cap():
    """The bug, reproduced as arithmetic so the write-up cannot drift from it.

    `// 2 + // 4 + // 4` is 100% of the total at ANY total — which is why
    raising `max_context_chars` alone would not have fixed anything.
    """
    for total in (4000, 8000, 40000):
        assert total // 2 + total // 4 + total // 4 == total


def test_raising_the_total_alone_would_not_have_fixed_it():
    """Scale-invariance, through the real function rather than by hand."""
    cfg = _shipped_cfg()
    cfg["learning"].pop("context_budgets")          # back to derived shares
    for total in (4000, 12000, 40000):
        cfg["learning"]["max_context_chars"] = total
        assert memory_mod.budget_overage(cfg) > 0, (
            f"at a {total}-char total the derived shares no longer "
            f"oversubscribe — the premise of §61 has changed")


# ------------------------------------------------------ preflight, non-blocking

def test_preflight_warns_when_the_budgets_oversubscribe():
    cfg = _shipped_cfg()
    cfg["learning"]["max_context_chars"] = 4000     # the state that shipped
    warns = preflight.warnings(cfg)
    assert any("oversubscribe" in w for w in warns), warns
    assert any("6966" in w for w in warns), (
        f"the warning must state the size of the overage, not merely that one "
        f"exists: {warns}")


def test_preflight_names_the_blocks_the_judge_would_lose():
    """A warning that says 'budgets are wrong' sends someone reading code. One
    that names CURRENT REGIME sends them to the line that matters."""
    cfg = _shipped_cfg()
    cfg["learning"]["max_context_chars"] = 4000
    text = " ".join(preflight.warnings(cfg))
    for block in ("news_memory", "scoreboard", "calibration", "regime"):
        assert block in text, f"{block} not named in the warning: {text}"


def test_the_shipped_config_warns_about_nothing():
    assert preflight.warnings(_shipped_cfg()) == []


def test_a_budget_warning_never_blocks_trading():
    """THE POLARITY. `preflight.run()` is fail-safe — main.py treats every
    entry it returns as fatal. A context budget makes the bot WORSE, not
    unsafe, and a config edit must not be able to stop it trading at 09:25.
    """
    cfg = _shipped_cfg()
    cfg["learning"]["max_context_chars"] = 4000
    assert preflight.warnings(cfg), "the fixture must actually warn"
    assert not [f for f in preflight.run(cfg) if "budget" in f.lower()
                or "context" in f.lower()], \
        "a context budget fault reached the blocking channel"


def test_the_warning_channel_never_raises():
    """It sits on the startup path. Garbage in must produce a string, not a
    traceback."""
    for junk in ({}, {"learning": None}, {"learning": {"context_budgets": "no"}}):
        assert isinstance(preflight.warnings(junk), list)


# ------------------------------------------------------- the eviction event

def test_the_cap_biting_is_recorded(tmp_path):
    """The signal whose absence let four blocks go missing. `ctx[:cap]`
    returned happily; the only symptom was worse judgments."""
    cfg = _copy(_shipped_cfg())
    cfg["learning"]["max_context_chars"] = 900      # force the cap to bind
    mem = _mem(tmp_path / "evict", cfg, n_trades=40, n_lessons=8, n_judgments=25)
    ctx = mem.context_for_llm(symbol="SPY")

    # Vacuity guard, TWO parts. The cap binding is not enough on its own: at a
    # 900-char cap the knowledge block alone (1,066) overflows it, so this test
    # would pass on a completely EMPTY store — precisely the hole the old
    # guards fell through. So the fixture is asserted to be full as well, and
    # emptying it is a mutation that must go red.
    assert len(mem.ledger.closed_trades()) >= 40, \
        "the fixture is not populated; this test would pass on an empty store"
    assert len(mem.lessons.replay()) >= 8, "no lessons seeded"
    assert len(ctx) == 900, f"the cap did not bind; context is {len(ctx)}"

    events = [json.loads(ln) for ln in
              open(cfg["memory"]["ledger_path"]) if ln.strip()]
    eviction = [e for e in events if e.get("event") == "context_evicted"]
    assert eviction, "the cap bit and nothing was recorded"
    rec = eviction[-1]
    assert rec["cap"] == 900
    assert rec["assembled_chars"] > 900
    assert rec["dropped_chars"] == rec["assembled_chars"] - 900


def test_the_eviction_names_which_blocks_were_lost(tmp_path):
    cfg = _copy(_shipped_cfg())
    cfg["learning"]["max_context_chars"] = 900
    mem = _mem(tmp_path / "named", cfg, n_trades=40, n_lessons=8, n_judgments=25)
    mem.context_for_llm(symbol="SPY")
    events = [json.loads(ln) for ln in
              open(cfg["memory"]["ledger_path"]) if ln.strip()]
    lost = [e for e in events
            if e.get("event") == "context_evicted"][-1]["blocks_lost"]
    assert lost, "an eviction that names nothing is a number nobody can act on"
    assert any("regime" in b for b in lost), (
        f"CURRENT REGIME is assembled LAST and must be among the first lost: "
        f"{lost}")


def test_nothing_is_recorded_when_everything_fits(tmp_path):
    """The control. A signal that fires on the ordinary case gets muted."""
    cfg = _copy(_shipped_cfg())
    mem = _mem(tmp_path / "fits", cfg, n_trades=3)
    ctx = mem.context_for_llm(symbol="SPY")
    assert len(ctx) < cfg["learning"]["max_context_chars"]
    events = [json.loads(ln) for ln in
              open(cfg["memory"]["ledger_path"]) if ln.strip()]
    assert not [e for e in events if e.get("event") == "context_evicted"]


def test_a_block_that_was_never_there_is_not_reported_as_lost(tmp_path):
    """A flat book has no CURRENT BOOK block. Reporting its absence as an
    eviction would cry wolf on the ordinary case and train people to ignore
    the event."""
    cfg = _copy(_shipped_cfg())
    cfg["learning"]["max_context_chars"] = 900
    mem = _mem(tmp_path / "flat", cfg, n_trades=40, n_lessons=8, n_judgments=25)
    mem.context_for_llm(symbol="SPY")          # positions=None -> no book block
    events = [json.loads(ln) for ln in
              open(cfg["memory"]["ledger_path"]) if ln.strip()]
    lost = [e for e in events
            if e.get("event") == "context_evicted"][-1]["blocks_lost"]
    assert not any(b.startswith("book") for b in lost), lost


def test_recording_an_eviction_can_never_break_a_review(tmp_path):
    """It sits on the path to every judge call. `alerting.py`'s rule is that
    observability must not be able to stop trading."""
    cfg = _copy(_shipped_cfg())
    cfg["learning"]["max_context_chars"] = 900
    mem = _mem(tmp_path / "boom", cfg, n_trades=40, n_lessons=8, n_judgments=25)

    real = mem.ledger

    class ExplodingOnEvictionOnly:
        """Reads still work; only the eviction write fails. Isolating it that
        way is the point — a fake that breaks everything would pass this test
        for the wrong reason."""
        def __getattr__(self, name):
            return getattr(real, name)

        def log_context_eviction(self, *a, **k):
            raise RuntimeError("ledger is down")

    mem.ledger = ExplodingOnEvictionOnly()
    ctx = mem.context_for_llm(symbol="SPY")      # must not raise
    assert len(ctx) == 900


# --------------------------------------------------------- the rollback path

def test_an_unset_config_is_byte_identical(tmp_path):
    """THE ROLLBACK. Naming a budget must not CHANGE it: a config without
    `learning.context_budgets` has to reproduce the old behaviour exactly, or
    'we only made the constraint visible' is a story rather than a fact.

    Same contract `knowledge_budget()` has kept since 2026-08-04.
    """
    import random
    old_style = _copy(_shipped_cfg())
    old_style["learning"].pop("context_budgets")
    old_style["learning"]["max_context_chars"] = 4000
    old_style["llm"]["knowledge_max_context_chars"] = 1000

    b = memory_mod.context_budgets(old_style)
    assert b["lessons"] == 2000, "lessons must fall back to // 2"
    assert b["market_context"] == 1000, "market context must fall back to // 4"
    assert b["scoreboard"] == 1000, "the scoreboard must fall back to // 4"
    assert b["book"] is None and b["trades"] is None, \
        "blocks that had no budget must fall back to unbounded, not to a number"
    assert b["calibration"] is None and b["regime"] is None

    random.seed(99)
    a = _mem(tmp_path / "r1", _copy(old_style), n_trades=12).context_for_llm(
        symbol="SPY")
    random.seed(99)
    c = _mem(tmp_path / "r2", _copy(old_style), n_trades=12).context_for_llm(
        symbol="SPY")
    assert a == c


def test_removing_the_new_keys_restores_the_old_eviction(tmp_path):
    """Proof the fix is the CONFIG, not a code path that quietly compensates.

    If this ever stops evicting, something in `context_for_llm` started
    protecting the tail on its own and the rollback no longer rolls back.
    """
    # The pre-§61 state exactly: derived shares, 4,000 total, knowledge at 1000.
    cfg = _copy(_shipped_cfg())
    cfg["learning"].pop("context_budgets")
    cfg["learning"]["max_context_chars"] = 4000
    cfg["llm"]["knowledge_max_context_chars"] = 1000
    mem = _mem(tmp_path / "old", cfg, n_trades=40, n_lessons=8, n_judgments=25)
    ctx = mem.context_for_llm(symbol="SPY")

    # Vacuity guard: the cap must actually have bound, or everything below is
    # asserting something that could not have been false.
    assert len(ctx) == 4000, \
        "the old config no longer hits its cap; the premise of §61 has moved"

    events = [json.loads(ln) for ln in
              open(cfg["memory"]["ledger_path"]) if ln.strip()]
    evictions = [e for e in events if e.get("event") == "context_evicted"]
    assert evictions, "the cap bit under the old config and nothing recorded it"
    rec = evictions[-1]
    assert rec["assembled_chars"] > 4000
    assert rec["blocks_lost"], "an eviction that names nothing is unactionable"
    # The overage preflight would have reported at startup, had it existed:
    # lessons 2000 + knowledge 1066 + market 1000 + news 600 + scoreboard 1000
    # = 5666 against a 4000 total. A LOWER bound — the book, the trades,
    # calibration and the regime label were unbudgeted and contribute nothing
    # to it, which is why the runtime event exists beside the static check.
    assert rec["budget_overage"] == 1666
    assert set(rec["unbounded_blocks"]) == {"book", "trades", "calibration",
                                            "regime"}


def test_the_shipped_config_keeps_the_regime_label(tmp_path):
    """The other side of the same coin, and the reason this phase exists."""
    cfg = _copy(_shipped_cfg())
    mem = _mem(tmp_path / "new", cfg, n_trades=40, n_lessons=8, n_judgments=25)
    ctx = mem.context_for_llm(symbol="SPY")
    assert "CURRENT REGIME" in ctx
    assert "YOUR RECENT CALIBRATION" in ctx


# ------------------------------------------------------------- the knowledge

def test_knowledge_can_actually_grow_now():
    """The owner's actual request. Before §61 the file sat at exactly 1000 of
    1000 chars — it FITTED, with zero room, so the next principle anyone wrote
    was the one that would vanish."""
    cfg = _shipped_cfg()
    budget = cfg["llm"]["knowledge_max_context_chars"]
    text = open("knowledge/principles.md").read().strip()
    assert budget - len(text) >= 300, (
        f"only {budget - len(text)} chars of headroom; knowledge still cannot "
        f"grow")


def test_the_knowledge_budget_counts_its_label(tmp_path):
    """`knowledge_block()` concatenates a 66-char label OUTSIDE the slice, so
    the block's real footprint is budget + label. A budget sum that ignored it
    would under-count the block by exactly that much."""
    cfg = _shipped_cfg()
    assert memory_mod.context_budgets(cfg)["knowledge"] == \
        cfg["llm"]["knowledge_max_context_chars"] + memory_mod.KNOWLEDGE_LABEL_CHARS
    mem = _mem(tmp_path / "kb", _copy(cfg))
    assert len(mem.knowledge_block()) <= memory_mod.context_budgets(cfg)["knowledge"]
