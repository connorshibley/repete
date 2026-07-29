"""The publish guard: an artifact that disagrees with the record must not ship.

`scripts/publish_dashboard.sh` tested one thing — `cmp` against the live copy —
and difference is not improvement. On 2026-07-28 the repo-root blog.html held
0 posts against 33 in memory/posts.jsonl and journal.html held 1 of 19. `cmp`
said "differs", which is exactly the condition that triggers a push.

`src/sitepaths.py` removed the cause. This file covers the second line: even if
something writes a bad artifact tomorrow, publishing refuses.
"""
import json
import os

import artifactcheck
import blog
import journal
import sitepaths


def _seed(cfg, posts=3, entries=2):
    """Write `posts` archive rows and `entries` journal entries, then render
    both artifacts honestly from them."""
    with open(cfg["x_posting"]["posts_log_path"], "w") as f:
        for i in range(posts):
            f.write(json.dumps({
                "ts": f"2026-07-2{i}T14:00:00+00:00",
                "text": f"[PAPER] post number {i}",
                "link": None, "status": "posted"}) + "\n")
    with open(cfg["x_posting"]["journal_path"], "w") as f:
        for i in range(entries):
            f.write(json.dumps({
                "trade_id": f"t{i}", "ts": f"2026-07-2{i}T14:00:00+00:00",
                "symbol": "SPY", "kind": "buy",
                "title": f"BUY SPY — entry {i}",
                "text": f"write-up {i}"}) + "\n")
    blog.render(cfg)
    journal.render(cfg)


def test_honest_artifacts_publish(cfg):
    _seed(cfg)
    assert artifactcheck.problems(cfg) == []


def test_a_truncated_blog_is_refused(cfg):
    """THE INCIDENT: the artifact renders fewer posts than the store holds."""
    _seed(cfg, posts=3)
    with open(sitepaths.resolve(cfg, blog.OUT_PATH), "w") as f:
        f.write("<html><body>nothing here</body></html>")   # 0 of 3
    found = artifactcheck.problems(cfg)
    assert len(found) == 1
    assert "blog.html" in found[0]
    assert "0 of 3" in found[0]


def test_a_truncated_journal_is_refused(cfg):
    _seed(cfg, entries=4)
    html = open(sitepaths.resolve(cfg, journal.OUT_PATH)).read()
    keep = html.split("<article", 2)          # drop all but the first entry
    with open(sitepaths.resolve(cfg, journal.OUT_PATH), "w") as f:
        f.write(keep[0] + "<article" + keep[1])
    found = artifactcheck.problems(cfg)
    assert any("journal.html" in p and "1 of 4" in p for p in found)


def test_an_inflated_artifact_is_also_refused(cfg):
    """Equality, not a floor. An artifact showing MORE than the record holds is
    just as wrong — that is the shape a stale copy or a merge accident takes,
    and it is how the fixture damage would have read if the fixture had been
    the larger file."""
    _seed(cfg, posts=2)
    path = sitepaths.resolve(cfg, blog.OUT_PATH)
    with open(path) as f:
        html = f.read()
    with open(path, "w") as f:
        f.write(html + "<div class=post>phantom</div>")      # 3 of 2
    assert any("3 of 2" in p for p in artifactcheck.problems(cfg))


def test_a_missing_artifact_is_not_a_fault(cfg):
    """The publish script skips files that do not exist; the checker must not
    invent a failure for them, or a fresh checkout could never publish."""
    _seed(cfg)
    os.remove(sitepaths.resolve(cfg, blog.OUT_PATH))
    assert not any("blog.html" in p for p in artifactcheck.problems(cfg))


def test_an_unreadable_store_refuses_rather_than_passing(cfg):
    """Fail-closed. 'Cannot measure' must never render as 'safe to publish' —
    that conflation is what let the frozen journal look healthy for four days.
    """
    _seed(cfg)
    os.remove(cfg["x_posting"]["posts_log_path"])
    found = artifactcheck.problems(cfg)
    assert any("cannot read" in p and "blog.html" in p for p in found)


def test_an_empty_store_is_not_an_unreadable_one(cfg):
    """Empty is a real state and must publish cleanly: a bot that has posted
    nothing yet renders an empty blog, and that is correct."""
    _seed(cfg, posts=0, entries=0)
    assert artifactcheck.problems(cfg) == []


def test_a_gutted_checker_would_fail_this_file(cfg):
    """Meta-assertion, per tests/test_rails_are_live.py:197.

    `problems()` returning [] unconditionally is the plausible way this guard
    rots. If that were true, every refusal test above would still pass its
    setup and silently assert nothing. Prove at least one refusal is real.
    """
    _seed(cfg, posts=5)
    with open(sitepaths.resolve(cfg, blog.OUT_PATH), "w") as f:
        f.write("<html></html>")
    assert artifactcheck.problems(cfg), (
        "problems() found nothing on a blatantly empty artifact — the checker "
        "is inert and every other test in this file is vacuous")
