"""The off-host mirror is judged by whether a run was DUE, not by hours.

Backups run WEEKDAYS at 17:00 ET (`scripts/scheduler.py`, the "backup" entry).
Friday 17:00 to Monday 17:00 is 72 hours of entirely correct silence. Any flat
threshold therefore either fires every Sunday on a healthy system or is loose
enough to miss a whole skipped weekday.

An alarm that fires on a working system is the failure divergence #22 records:
the test suite paged the operator, and the cost was not the pages, it was that
real ones became invisible among them.

This file is the reason to trust `mirror=... OVERDUE` when it appears.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import health

ET = ZoneInfo("America/New_York")


def _receipt(tmp_path, ts, verified=True):
    p = tmp_path / "offhost_mirror.json"
    p.write_text(json.dumps({
        "ts": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "remote", "destination": "b2:x/y", "archive": "a.tar.gz",
        "sha256": "deadbeef", "verified": verified}))
    return str(p)


# ---- last_backup_due ------------------------------------------------------

def test_a_weekend_points_back_at_friday():
    """THE TEST THAT MATTERS. Sunday must not demand a Saturday mirror."""
    due = health.last_backup_due(datetime(2026, 8, 23, 10, 0, tzinfo=ET))
    assert due.astimezone(ET) == datetime(2026, 8, 21, 17, 0, tzinfo=ET)


def test_inside_the_grace_window_the_run_is_not_yet_owed():
    """18:00 Monday is one hour after the job fires. A slow upload is not a
    missed backup, and treating it as one would page on every slow night."""
    due = health.last_backup_due(datetime(2026, 8, 24, 18, 0, tzinfo=ET))
    assert due.astimezone(ET) == datetime(2026, 8, 21, 17, 0, tzinfo=ET)


def test_the_next_morning_the_run_is_owed():
    due = health.last_backup_due(datetime(2026, 8, 25, 9, 0, tzinfo=ET))
    assert due.astimezone(ET) == datetime(2026, 8, 24, 17, 0, tzinfo=ET)


def test_before_the_first_fire_of_a_weekday_the_previous_weekday_is_owed():
    due = health.last_backup_due(datetime(2026, 8, 24, 16, 0, tzinfo=ET))
    assert due.astimezone(ET) == datetime(2026, 8, 21, 17, 0, tzinfo=ET)


# ---- mirror_receipt_ts ----------------------------------------------------

def test_a_missing_receipt_is_none_not_old(tmp_path):
    """None means NO verified mirror has ever happened. That is a different
    and worse state than a stale one, and callers must not collapse them."""
    assert health.mirror_receipt_ts(str(tmp_path / "nope.json")) is None


def test_an_unverified_receipt_is_refused(tmp_path):
    """`verified: false` must never read as a mirror. backup.sh only ever
    writes true — but a hand-edited or partially-written file must not be able
    to silence the alarm."""
    p = _receipt(tmp_path, datetime.now(timezone.utc), verified=False)
    assert health.mirror_receipt_ts(p) is None


def test_a_corrupt_receipt_is_refused(tmp_path):
    p = tmp_path / "offhost_mirror.json"
    p.write_text("{not json")
    assert health.mirror_receipt_ts(str(p)) is None


def test_a_verified_receipt_reads_back(tmp_path):
    ts = datetime(2026, 8, 21, 21, 0, tzinfo=timezone.utc)
    assert health.mirror_receipt_ts(_receipt(tmp_path, ts)) == ts
