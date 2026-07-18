"""Bot blog: the post archive at the x_poster choke point + the daily
rendered page. All offline."""
import json

import blog
import x_poster
from ledger import Ledger


def _cfg_paths(cfg, tmp_path):
    cfg["x_posting"]["posts_log_path"] = str(tmp_path / "posts.jsonl")
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    return cfg


def test_dry_run_post_is_archived(cfg, tmp_path, capsys):
    cfg = _cfg_paths(cfg, tmp_path)
    x_poster.post_text("hello world", cfg,
                       link="https://example.com/journal.html#abc")
    rows = [json.loads(l) for l in open(cfg["x_posting"]["posts_log_path"])]
    assert len(rows) == 1
    assert rows[0]["status"] == "dry_run"
    assert rows[0]["link"].endswith("#abc")
    assert "[PAPER]" in rows[0]["text"]


def test_archive_failure_never_breaks_posting(cfg, capsys):
    cfg["x_posting"]["posts_log_path"] = "/nonexistent-dir!!/posts.jsonl"
    x_poster.post_text("still posts fine", cfg)  # must not raise
    assert "still posts fine" in capsys.readouterr().out


def test_render_groups_days_and_filters_status(cfg, tmp_path):
    cfg = _cfg_paths(cfg, tmp_path)
    posts = [
        {"ts": "2026-07-16T13:40:00+00:00", "text": "[PAPER] day one plan",
         "link": None, "status": "posted"},
        {"ts": "2026-07-16T20:25:00+00:00",
         "text": "[PAPER] BUY NVDA — dip in uptrend",
         "link": "https://x.io/journal.html#abc12345", "status": "posted"},
        {"ts": "2026-07-17T13:40:00+00:00", "text": "[PAPER] day two plan",
         "link": None, "status": "posted"},
        {"ts": "2026-07-17T14:00:00+00:00", "text": "should not appear",
         "link": None, "status": "dry_run"},
        {"ts": "2026-07-17T14:05:00+00:00", "text": "nor this",
         "link": None, "status": "failed"},
    ]
    with open(cfg["x_posting"]["posts_log_path"], "w") as f:
        for p in posts:
            f.write(json.dumps(p) + "\n")
    # Context event dated to a post day (07-17) so the morning-read block
    # attaches deterministically — log_event would stamp real `now`, which
    # drifts off the fixture days as the clock advances.
    with open(cfg["memory"]["ledger_path"], "a") as f:
        f.write(json.dumps({
            "type": "event", "event": "market_context",
            "detail": "Markets calm ahead of CPI | nominations: none",
            "ts": "2026-07-17T13:30:00+00:00"}) + "\n")

    out = blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    html = open(out).read()

    assert "2026-07-17" in html and "2026-07-16" in html
    assert html.index("2026-07-17") < html.index("2026-07-16")  # newest first
    assert "day one plan" in html and "day two plan" in html
    assert "should not appear" not in html and "nor this" not in html
    assert "Markets calm ahead of CPI" in html      # morning read block
    assert 'href="https://x.io/journal.html#abc12345"' in html
    assert "full write-up" in html
    assert "[PAPER]" in html


def test_render_empty_archive(cfg, tmp_path):
    cfg = _cfg_paths(cfg, tmp_path)
    out = blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    assert "No posts yet" in open(out).read()
