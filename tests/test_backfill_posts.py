"""Backfill of already-open trades to X + journal. Fully offline: LLM is
disabled (template journal entries) and X stays dry_run, so nothing leaves
the machine."""
import json

import backfill_posts
import journal
from ledger import Ledger


def _seed_two_buys(cfg):
    """Two executed buys (opened 'earlier'), never posted; one plain hold."""
    led = Ledger(cfg["memory"]["ledger_path"])
    t1 = led.log_decision("NVDA", "buy",
                          "RSI2 at 7 — oversold dip in an uptrend",
                          {"rsi2": 7.4}, {"verdict": "approve", "scale": 1.0,
                                          "reasoning": "rule-based"},
                          executed=True, order={"id": "o1",
                                                "stop_price": 164.67},
                          entry_price=177.19, qty=5, regime="down/low",
                          strategy="meanrev")
    t2 = led.log_decision("SPY", "buy", "63-bar momentum +3.1%", {},
                          {"verdict": "approve", "scale": 1.0,
                           "reasoning": "rule-based"},
                          executed=True, entry_price=689.30, qty=1,
                          regime="down/low", strategy="tsmom")
    led.log_decision("QQQ", "hold", "awaiting signal", {}, None,
                     executed=False)
    return led, t1, t2


def test_backfill_text_is_honest_and_bounded(cfg):
    led, t1, _ = _seed_two_buys(cfg)
    rec = next(r for r in led.all_records()
               if r.get("trade_id") == t1 and r["type"] == "decision")
    text = backfill_posts.backfill_text(rec)
    assert "NVDA" in text and "meanrev" in text
    assert "opened" in text.lower() and "still open" in text.lower()
    assert "$177.19" in text
    assert len(text) <= 250  # leaves room for the appended journal link


def test_month_abbr():
    assert backfill_posts._month_abbr("2026-07-16") == "Jul 16"
    assert backfill_posts._month_abbr("bad") == "bad"


def test_pending_buys_selects_executed_and_respects_already(cfg):
    led, t1, t2 = _seed_two_buys(cfg)
    recs = led.all_records()
    pend = backfill_posts.pending_buys(recs, set())
    assert [r["trade_id"] for r in pend] == [t1, t2]  # buys only, oldest first
    # idempotency: a trade already journaled/posted is excluded
    pend2 = backfill_posts.pending_buys(recs, {t1})
    assert [r["trade_id"] for r in pend2] == [t2]


def test_run_dry_journals_and_archives_without_posting(cfg, tmp_path):
    cfg["x_posting"]["journal_url_base"] = "https://x.io/journal.html"
    led, t1, t2 = _seed_two_buys(cfg)
    acted = backfill_posts.run(cfg, live=False)
    assert {r["trade_id"] for r in acted} == {t1, t2}

    # 2 journal entries written, both marked as buys
    entries = journal._load(cfg["x_posting"]["journal_path"])
    assert {e["trade_id"] for e in entries} == {t1, t2}
    assert all(e["kind"] == "buy" for e in entries)

    # 2 archive rows, all dry_run (never posted), all [PAPER], all linked
    rows = [json.loads(l)
            for l in open(cfg["x_posting"]["posts_log_path"])]
    assert len(rows) == 2
    assert all(r["status"] == "dry_run" for r in rows)
    assert all(r["text"].startswith("[PAPER]") for r in rows)
    assert all("journal.html#" in (r["link"] or "") for r in rows)

    # backfill_post events mirrored to the ledger
    events = [r for r in led.all_records()
              if r.get("type") == "event" and r.get("event") == "backfill_post"]
    assert len(events) == 2


def test_posted_ids_parses_link_fragment(cfg, tmp_path):
    rows = [
        {"ts": "t", "text": "a", "link": "https://x.io/journal.html#aaa111",
         "status": "posted"},
        {"ts": "t", "text": "b", "link": "https://x.io/journal.html#bbb222",
         "status": "dry_run"},          # not counted (never posted)
        {"ts": "t", "text": "c", "link": None, "status": "failed"},
    ]
    with open(cfg["x_posting"]["posts_log_path"], "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    assert backfill_posts.posted_ids(cfg) == {"aaa111"}


def test_idempotency_keys_off_posted_not_journaled(cfg):
    """A dry-run journals but must not block a later live post; only a
    'posted' archive row removes a trade from the pending set."""
    led, t1, t2 = _seed_two_buys(cfg)
    backfill_posts.run(cfg, live=False)               # dry-run journals both
    assert set(backfill_posts.journaled_ids(cfg)) == {t1, t2}
    # still pending because nothing was actually posted
    pend = backfill_posts.pending_buys(led.all_records(),
                                       backfill_posts.posted_ids(cfg))
    assert {r["trade_id"] for r in pend} == {t1, t2}
    # mark t1 as genuinely posted -> only t2 remains pending
    with open(cfg["x_posting"]["posts_log_path"], "a") as f:
        f.write(json.dumps({"ts": "t", "text": "x",
                            "link": f"https://x.io/j.html#{t1}",
                            "status": "posted"}) + "\n")
    pend2 = backfill_posts.pending_buys(led.all_records(),
                                        backfill_posts.posted_ids(cfg))
    assert {r["trade_id"] for r in pend2} == {t2}


def test_run_reuses_journal_no_duplicate(cfg):
    _seed_two_buys(cfg)
    backfill_posts.run(cfg, live=False)
    backfill_posts.run(cfg, live=False)   # second dry-run
    entries = journal._load(cfg["x_posting"]["journal_path"])
    # exactly one journal entry per trade, not duplicated by the re-run
    ids = [e["trade_id"] for e in entries]
    assert len(ids) == len(set(ids)) == 2
