"""X delivery is OFF in the SHIPPED config, and stays off silently-unrevivable.

Owner, 2026-07-28: "dont worry about it posting on X as long as it posts on
its own blog in the journal." Reaffirmed 2026-08-11: no more posts to X — the
bot keeps its internal journal. Both switches already existed; what did NOT
exist was a test that reads the file that deploys. Every X test in this suite
builds its config from a fixture, which is exactly the gap §61 found in the
principles guard — a check on a number nobody deploys. This file reads
config.yaml itself.

Turning X back on is a deliberate two-flag owner action (enabled AND
dry_run), and doing so makes this file fail — which is the point: the record
of the owner's decision should not be editable by accident from either side.

The blog/journal pipeline is deliberately NOT gated on these flags —
x_poster archives every post before it considers delivery, and
test_blog_is_independent_of_x.py pins that decoupling. This file pins only
the delivery sink.
"""
import sys

import yaml

sys.path.insert(0, "src")

with open("config.yaml") as _f:
    SHIPPED = yaml.safe_load(_f)


def test_x_delivery_is_disabled_in_the_shipped_config():
    assert SHIPPED["x_posting"]["enabled"] is False, (
        "x_posting.enabled flipped on — that reverses the owner's 2026-07-28/"
        "2026-08-11 decision (journal only, no X). If that decision has "
        "actually changed, update this test IN THE SAME COMMIT that flips "
        "the flag, and say so in the commit message.")


def test_the_second_lock_is_still_on_too():
    """dry_run: true is the W5-2 backstop — even an accidentally re-enabled
    X path would print instead of deliver. Both locks, independently."""
    assert SHIPPED["x_posting"]["dry_run"] is True


def test_the_journal_pipeline_is_not_what_these_flags_switch():
    """The paths the journal writes to must be configured regardless of X —
    the 2026-07-28 coupling bug was the blog going dark because a third-party
    sink was turned off. Absence of either key would resurrect it."""
    assert SHIPPED["x_posting"].get("journal_path", "memory/journal.jsonl")
    assert SHIPPED["x_posting"].get("posts_log_path", "memory/posts.jsonl")
