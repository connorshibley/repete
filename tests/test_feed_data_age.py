"""dashboard_data.json carries TWO clocks, and says "I don't know" properly.

WHY. `generated_at` is when the page was RENDERED — ~0 by construction, so a
consumer reading only that learns nothing about whether the content is current.
The fleet console reads only that, which is why on a weekend a healthy system
and a publisher that died on Friday afternoon both render as "published 25h
ago" with an amber dot. Every publish-chaining job is Mon-Fri, so the feed sits
untouched for 65 hours and the two states are indistinguishable.

`data_at` / `data_age_hours` are the second clock. The rule they must obey is
the one this repo keeps relearning: ABSENT MUST NOT COLLAPSE TO ZERO. An
unreadable ledger reported as `0.0` hours old would be the freshest thing on
the page.
"""
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone

import dashboard
from ledger import Ledger
from test_dashboard import _cfg_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 8, 23, 17, 0, tzinfo=timezone.utc)


# ---- the second clock -----------------------------------------------------

def test_an_empty_ledger_is_none_not_zero():
    """THE ONE THAT MATTERS. 0.0 hours old means brand new. No records means
    we cannot say. Reporting the first for the second makes a dead feed look
    like the freshest thing on the page."""
    assert dashboard.newest_record_at([]) is None


def test_an_unparseable_timestamp_is_none_not_zero():
    assert dashboard.newest_record_at([{"ts": "not-a-date"}]) is None
    assert dashboard.newest_record_at([{"nots": 1}]) is None


def test_the_newest_record_wins_not_the_last_one():
    """Ledger order is append order, which is usually but not always time
    order — an adopted position backfills an older ts."""
    recs = [{"ts": "2026-08-21T19:45:00+00:00"},
            {"ts": "2026-08-23T12:00:00+00:00"},
            {"ts": "2026-08-20T09:00:00+00:00"}]
    got = dashboard.newest_record_at(recs)
    assert got == datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_a_naive_timestamp_is_treated_as_utc_not_local():
    got = dashboard.newest_record_at([{"ts": "2026-08-23T12:00:00"}])
    assert got.tzinfo is not None
    assert got == datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def test_the_two_helpers_agree():
    """`data_age_hours` is what the server-side badge already uses. The feed
    must report the SAME number, not a second implementation that can drift —
    src/dashboard.py:546 records what happened the last time a threshold
    stopped being consulted while still being defined."""
    recs = [{"ts": (NOW - timedelta(hours=25)).isoformat()}]
    assert dashboard.data_age_hours(recs, NOW) == 25.0
    assert dashboard.newest_record_at(recs) == NOW - timedelta(hours=25)


# ---- the scheduled heartbeat ----------------------------------------------

def _jobs():
    spec = importlib.util.spec_from_file_location(
        "sched_feed", os.path.join(ROOT, "scripts", "scheduler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {j[0]: j for j in mod.JOBS}


def test_the_weekend_heartbeat_runs_on_both_weekend_days():
    job = _jobs()["weekend-publish"]
    assert sorted(job[1]) == [5, 6], (
        "the heartbeat must cover Saturday AND Sunday — the gap it closes is "
        "Friday 16:20 to Monday 09:35, and one weekend day still leaves a "
        "stretch where silence means two different things")


def test_the_heartbeat_renders_before_it_publishes():
    """publish_dashboard.sh diffs each file before committing. Publishing
    without re-rendering finds nothing changed and pushes nothing — a job that
    runs, succeeds, and proves no liveness at all."""
    cmd = " ".join(_jobs()["weekend-publish"][4])
    assert "dashboard.py" in cmd and "publish_dashboard.sh" in cmd
    assert cmd.index("dashboard.py") < cmd.index("publish_dashboard.sh"), (
        "publish runs before render — the heartbeat would push nothing")


def test_the_heartbeat_does_not_trade():
    """It is a render and a git push. Anything reaching main.py or the broker
    on a weekend is a different and much worse change."""
    cmd = " ".join(_jobs()["weekend-publish"][4])
    for forbidden in ("src/main.py", "broker", "swing_scan", "--open-cycle"):
        assert forbidden not in cmd, f"weekend job touches {forbidden}"


# ---- the feed's shape -----------------------------------------------------

def _render_feed(tmp_path, cfg, records=()):
    """Drive the REAL render and read the feed it emits.

    Recomputing the payload inside the test would prove nothing: it would pass
    just as happily if dashboard.py stopped emitting the fields entirely. That
    is the exact failure src/dashboard.py:546 records — thresholds asserted
    PRESENT rather than READ, and every test green while the badge was
    hardcoded to 'green'.
    """
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    for r in records:
        led.log_event("positions_mark", "seeded", extra=r)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    feed = os.path.join(os.path.dirname(out) or ".", dashboard.DATA_PATH)
    return json.load(open(feed))


def test_the_real_render_emits_both_clocks(tmp_path, cfg):
    feed = _render_feed(tmp_path, cfg)
    for key in ("generated_at", "data_at", "data_age_hours",
                "stale_amber_hours", "stale_red_hours"):
        assert key in feed, f"{key} missing from dashboard_data.json"
    assert feed["stale_amber_hours"] == dashboard.STALE_AMBER_HOURS
    assert feed["stale_red_hours"] == dashboard.STALE_RED_HOURS


def test_the_real_render_reports_null_not_zero_for_an_empty_ledger(tmp_path, cfg):
    """End-to-end version of the rule. An empty ledger must not publish
    `data_age_hours: 0.0`, which a consumer reads as perfectly fresh."""
    feed = _render_feed(tmp_path, cfg)
    assert feed["data_at"] is None
    assert feed["data_age_hours"] is None, (
        "an empty ledger published itself as 0 hours old — the freshest "
        "possible value for the least knowable state")


def test_the_hash_still_covers_regions_only(tmp_path, cfg):
    """LIVE_JS's swap() re-renders the DOM whenever `hash` moves. Folding the
    new top-level keys into it would make every 30s poll a full swap.

    Checked against the REAL emitted feed, recomputing from its own regions."""
    import hashlib
    feed = _render_feed(tmp_path, cfg)
    expected = hashlib.sha256(
        json.dumps(feed["regions"], sort_keys=True).encode()).hexdigest()[:16]
    assert feed["hash"] == expected, (
        "the feed hash no longer equals sha256(regions) — the new top-level "
        "keys have leaked into it, and every poll will now re-swap the DOM")


def test_the_published_thresholds_are_the_module_constants():
    """Published so a consumer can stop hardcoding its own. The console calls
    25h amber (<48h) while this page calls the same number red (>=24h); these
    fields are what would let that disagreement be resolved rather than
    silently persist."""
    assert dashboard.STALE_AMBER_HOURS == 8
    assert dashboard.STALE_RED_HOURS == 24
