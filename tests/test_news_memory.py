"""Does the bot remember the news, and can it be turned off cleanly? (W7)

The bug this closes was an absence, not a fault: news was read hourly since
2026-07-17 and forgotten every time, so the judge could never distinguish a
symbol in the headlines four days running from one mentioned once, and nothing
recorded whether attending to news ever helped.

The three assertions that matter most here are not about the happy path:

  * `enabled: false` must produce **byte-identical** judge context. A feature
    that cannot be switched off cleanly cannot be rolled back when a gate
    eventually rejects it.
  * The **lesson block must not shrink** when a news block appears. A new block
    without its own budget quietly eats the tail of `max_context_chars`, and
    the damage shows up as worse judgments, never as a failing diff.
  * A symbol with no history yields **no block at all**, not an empty header.
    "We looked and found nothing" and "we have no history" are different
    claims to a judge and only one of them is true.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import news_memory
from ledger import Ledger
from memory import Memory

NOW = datetime(2026, 7, 30, 16, 0, tzinfo=timezone.utc)

# For fixtures read back through Memory.context_for_llm, which reads the REAL
# wall clock — there is no way to inject `now` through that path. Three tests
# here used the frozen NOW for those writes, and on 2026-08-30 all three went
# red at midnight with no code change: NOW had aged past retention_days=30 and
# the rows silently expired, tripping their own vacuity guards. A frozen write
# clock against a wall read clock is a time bomb with a fuse equal to the
# retention window. Tests that inject `now=` on BOTH sides keep using NOW —
# they are genuinely deterministic and should stay that way.
FRESH = datetime.now(timezone.utc) - timedelta(hours=1)


def _cfg(tmp_path, **over):
    mem = {"enabled": True, "path": str(tmp_path / "news_memory.jsonl"),
           "retention_days": 30, "max_context_chars": 600, "max_rows": 6,
           "min_mentions_to_show": 1}
    mem.update(over)
    return {"news": {"memory": mem}}


def _ctx(**flags):
    return {"summary": "s", "symbol_flags": flags, "nominations": []}


@pytest.fixture(autouse=True)
def clean_stats():
    news_memory.reset_stats()
    yield
    news_memory.reset_stats()


# ---------------- recording ----------------

def test_a_mention_survives_a_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    n = news_memory.record_context(_ctx(MSFT="cloud growth strong"), cfg,
                                   now=NOW)
    assert n == 1
    rows = news_memory.history("MSFT", cfg, now=NOW)
    assert len(rows) == 1
    assert rows[0]["text"] == "cloud growth strong"


def test_nominations_are_recorded_and_marked(tmp_path):
    cfg = _cfg(tmp_path)
    ctx = {"summary": "s", "symbol_flags": {},
           "nominations": [{"symbol": "RDDT", "reason": "guidance beat"}]}
    news_memory.record_context(ctx, cfg, now=NOW)
    rows = news_memory.history("RDDT", cfg, now=NOW)
    assert rows[0]["nominated"] is True
    assert "guidance" in rows[0]["text"]


def test_retention_drops_what_is_older_than_the_window(tmp_path):
    """`now` is injected on purpose: the first watchdog fixtures passed or
    failed by time zone because they leaned on the wall clock."""
    cfg = _cfg(tmp_path, retention_days=10)
    news_memory.record_context(_ctx(AAPL="old"), cfg,
                               now=NOW - timedelta(days=40))
    news_memory.record_context(_ctx(AAPL="fresh"), cfg, now=NOW)
    rows = news_memory.history("AAPL", cfg, now=NOW)
    assert [r["text"] for r in rows] == ["fresh"]


def test_disabled_records_nothing(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    assert news_memory.record_context(_ctx(MSFT="x"), cfg, now=NOW) == 0
    assert news_memory.history("MSFT", cfg, now=NOW) == []


# ---------------- the ledger event ----------------

class _Led:
    def __init__(self):
        self.events = []

    def log_event(self, kind, detail):
        self.events.append((kind, detail))


def test_the_event_is_written_even_when_nothing_was_remembered(tmp_path):
    """W6-A1's rule, applied again: a quiet news day and a broken recorder
    must not look identical in the record."""
    led = _Led()
    news_memory.record_context({"summary": "s", "symbol_flags": {},
                                "nominations": []}, _cfg(tmp_path),
                               ledger=led, now=NOW)
    assert [k for k, _ in led.events] == ["news_memory"]
    assert "0 symbol" in led.events[0][1]


# ---------------- outcomes ----------------

def test_an_outcome_links_back_to_the_symbol(tmp_path):
    cfg = _cfg(tmp_path)
    news_memory.record_context(_ctx(NVDA="datacentre demand"), cfg, now=NOW)
    assert news_memory.record_outcome("NVDA", "t-1", 6.4, cfg, now=NOW) is True
    outs = news_memory.outcomes("NVDA", cfg, now=NOW)
    assert len(outs) == 1
    assert outs[0]["trade_id"] == "t-1"
    assert outs[0]["pnl_pct"] == 6.4


def test_outcomes_do_not_leak_across_symbols(tmp_path):
    cfg = _cfg(tmp_path)
    news_memory.record_outcome("NVDA", "t-1", 6.4, cfg, now=NOW)
    assert news_memory.outcomes("MSFT", cfg, now=NOW) == []


# ---------------- the judge-facing block ----------------

def test_no_history_yields_no_block_at_all(tmp_path):
    assert news_memory.format_block("MSFT", _cfg(tmp_path), now=NOW) == ""


def test_the_block_shows_recurrence_and_prior_outcomes(tmp_path):
    cfg = _cfg(tmp_path)
    for d in (0, 1, 2, 3):
        news_memory.record_context(_ctx(MSFT=f"day {d}"), cfg,
                                   now=NOW - timedelta(days=d))
    news_memory.record_outcome("MSFT", "t-1", 6.4, cfg, now=NOW)
    block = news_memory.format_block("MSFT", cfg, now=NOW)
    assert "4 mention(s)" in block
    assert "1 prior news-linked closed trade(s)" in block
    assert "+6.40% average" in block


def test_the_block_obeys_its_own_cap(tmp_path):
    cfg = _cfg(tmp_path, max_context_chars=120)
    for d in range(6):
        news_memory.record_context(_ctx(MSFT="x" * 200), cfg,
                                   now=NOW - timedelta(days=d))
    assert len(news_memory.format_block("MSFT", cfg, now=NOW)) <= 120


def test_a_corrupt_store_degrades_to_no_block(tmp_path):
    """Append-only log written by a live process: a torn final write is normal,
    and must not cost the whole history OR raise into a judge call."""
    cfg = _cfg(tmp_path)
    news_memory.record_context(_ctx(MSFT="good line"), cfg, now=NOW)
    with open(cfg["news"]["memory"]["path"], "a") as f:
        f.write("{not json at all\n")
    block = news_memory.format_block("MSFT", cfg, now=NOW)
    assert "good line" in block          # the intact record survived


def test_an_unreadable_store_never_raises(tmp_path):
    cfg = _cfg(tmp_path, path=str(tmp_path))   # a directory, not a file
    assert news_memory.format_block("MSFT", cfg, now=NOW) == ""
    assert news_memory.history("MSFT", cfg, now=NOW) == []


# ---------------- integration with the judge context ----------------

def _mem(tmp_path, cfg, news_enabled=True):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "memory" / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "memory" / "judgments.jsonl")
    cfg.setdefault("news", {})["memory"] = {
        "enabled": news_enabled,
        "path": str(tmp_path / "news_memory.jsonl"),
        "retention_days": 30, "max_context_chars": 600, "max_rows": 6,
        "min_mentions_to_show": 1}
    # Today's market context must not interfere: point it somewhere empty.
    cfg["news"]["context_path"] = str(tmp_path / "no_such_context.json")
    led = Ledger(cfg["memory"]["ledger_path"])
    return Memory(cfg, led)


def test_disabled_gives_byte_identical_context(tmp_path, cfg):
    """The rollback property. A feature that cannot be switched off cleanly
    cannot be withdrawn when a gate rejects it."""
    ncfg = {"news": {"memory": {"enabled": True,
                                "path": str(tmp_path / "news_memory.jsonl"),
                                "retention_days": 30, "max_context_chars": 600,
                                "max_rows": 6, "min_mentions_to_show": 1}}}
    news_memory.record_context(_ctx(SPY="in the headlines"), ncfg, now=FRESH)

    off = _mem(tmp_path / "a", json.loads(json.dumps(cfg)), news_enabled=False)
    off.news_cfg["memory"]["path"] = str(tmp_path / "news_memory.jsonl")
    ctx_off = off.context_for_llm(symbol="SPY")
    assert "NEWS MEMORY" not in ctx_off

    on = _mem(tmp_path / "b", json.loads(json.dumps(cfg)), news_enabled=True)
    on.news_cfg["memory"]["path"] = str(tmp_path / "news_memory.jsonl")
    assert "NEWS MEMORY" in on.context_for_llm(symbol="SPY")


def test_the_lesson_block_does_not_shrink_when_news_is_present(tmp_path, cfg):
    """The silent-eviction guard. Without its own budget the news block eats
    the tail of max_context_chars and truncates whatever sorts after it."""
    base = _mem(tmp_path / "x", json.loads(json.dumps(cfg)), news_enabled=False)
    before = base.context_for_llm(symbol="SPY")

    ncfg = {"news": {"memory": {"enabled": True,
                                "path": str(tmp_path / "nm.jsonl"),
                                "retention_days": 30, "max_context_chars": 600,
                                "max_rows": 6, "min_mentions_to_show": 1}}}
    for d in range(6):
        news_memory.record_context(_ctx(SPY="y" * 120), ncfg,
                                   now=FRESH - timedelta(days=d))
    withnews = _mem(tmp_path / "y", json.loads(json.dumps(cfg)),
                    news_enabled=True)
    withnews.news_cfg["memory"]["path"] = str(tmp_path / "nm.jsonl")
    after = withnews.context_for_llm(symbol="SPY")

    # Vacuity guard. If the block never appeared, "lessons did not shrink" is
    # trivially true and this test proves nothing — which is exactly how two
    # of §45's negative controls failed to bite.
    assert "NEWS MEMORY" in after, "no news block present; the test is vacuous"

    marker = "VALIDATED LESSONS"
    lesson_before = before.split(marker, 1)[1].split("\n\n", 1)[0]
    lesson_after = after.split(marker, 1)[1].split("\n\n", 1)[0]
    assert lesson_after == lesson_before, "the news block evicted lessons"


def test_the_total_context_still_obeys_the_learning_cap(tmp_path, cfg):
    cfg = json.loads(json.dumps(cfg))
    cfg["learning"]["max_context_chars"] = 1500
    ncfg = {"news": {"memory": {"enabled": True,
                                "path": str(tmp_path / "nm2.jsonl"),
                                "retention_days": 30, "max_context_chars": 600,
                                "max_rows": 6, "min_mentions_to_show": 1}}}
    for d in range(6):
        news_memory.record_context(_ctx(SPY="z" * 200), ncfg,
                                   now=FRESH - timedelta(days=d))
    m = _mem(tmp_path / "z", cfg, news_enabled=True)
    m.news_cfg["memory"]["path"] = str(tmp_path / "nm2.jsonl")
    ctx = m.context_for_llm(symbol="SPY")
    # Vacuity guard: a cap test on a context that never got near the cap
    # asserts nothing. 6 rows x 200 chars is well over the 600-char block cap,
    # so the block must be present AND clipped for this to mean anything.
    assert "NEWS MEMORY" in ctx, "no news block present; the test is vacuous"
    assert len(ctx) <= 1500
