"""Bot blog: the post archive at the x_poster choke point + the daily
rendered page. All offline."""
import json

import blog
import x_poster


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


def test_render_groups_days_and_keeps_every_status(cfg, tmp_path):
    """Renamed and inverted on 2026-07-28.

    This test used to assert that a `dry_run` and a `failed` post were FILTERED
    OUT — i.e. it pinned the defect. The blog showed only what X had accepted,
    so it sat frozen at 2026-07-24 while the bot traded ten times on 07-27. The
    filter is gone: every archived post is the bot's own output and belongs on
    the bot's own page. Delivery `status` stays in the archive for audit and
    off the public page.

    Everything else here — day grouping, newest-first ordering, the morning
    read block, the journal anchor, the [PAPER] disclosure — is unchanged and
    still load-bearing. See tests/test_blog_is_independent_of_x.py.
    """
    cfg = _cfg_paths(cfg, tmp_path)
    posts = [
        {"ts": "2026-07-16T13:40:00+00:00", "text": "[PAPER] day one plan",
         "link": None, "status": "posted"},
        {"ts": "2026-07-16T20:25:00+00:00",
         "text": "[PAPER] BUY NVDA — dip in uptrend",
         "link": "https://x.io/journal.html#abc12345", "status": "posted"},
        {"ts": "2026-07-17T13:40:00+00:00", "text": "[PAPER] day two plan",
         "link": None, "status": "posted"},
        {"ts": "2026-07-17T14:00:00+00:00", "text": "rehearsed but not sent",
         "link": None, "status": "dry_run"},
        {"ts": "2026-07-17T14:05:00+00:00", "text": "written while X was down",
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
    assert "rehearsed but not sent" in html
    assert "written while X was down" in html
    assert "dry_run" not in html and "failed" not in html  # status stays private
    assert "Markets calm ahead of CPI" in html      # morning read block
    assert 'href="https://x.io/journal.html#abc12345"' in html
    assert "full write-up" in html
    assert "[PAPER]" in html


def test_render_empty_archive(cfg, tmp_path):
    cfg = _cfg_paths(cfg, tmp_path)
    out = blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    assert "No posts yet" in open(out).read()


# ---- F-09: a post link is a URL, not just a string (2026-08-11 QA sweep) ----
#
# render() escapes a post's TEXT and then built <a href="{url}"> from the same
# record with no scheme check, so a post whose link was `javascript:alert(1)`
# rendered as an ordinary clickable "full write-up →" on a public page.
# Confirmed against a hostile fixture in a real browser: 14 such anchors, with
# text escaping clean everywhere else.
#
# Nothing hostile can reach it today — every link is written by this bot from
# its own config — which is exactly why the check is cheap now and expensive
# after the first feature that lets an outside string become a post link.

def _one_post(cfg, tmp_path, link):
    cfg = _cfg_paths(cfg, tmp_path)
    with open(cfg["x_posting"]["posts_log_path"], "w") as f:
        f.write(json.dumps({"ts": "2026-08-11T13:00:00+00:00",
                            "text": "a post", "link": link,
                            "status": "posted"}) + "\n")
    return open(blog.render(cfg, out_path=str(tmp_path / "blog.html"))).read()


def test_a_dangerous_url_scheme_is_not_linked(cfg, tmp_path):
    html = _one_post(cfg, tmp_path, "javascript:alert(document.domain)")
    assert "javascript:" not in html
    assert "full write-up" not in html          # refused, not silently rewritten
    assert "a post" in html                     # the post itself still renders


def test_data_and_vbscript_urls_are_refused_too(cfg, tmp_path):
    """An allowlist, not a blocklist: javascript: is the one everybody
    remembers and these are the ones they forget."""
    for bad in ("data:text/html;base64,PHNjcmlwdD4=", "vbscript:msgbox(1)",
                "//evil.example.invalid/x"):
        html = _one_post(cfg, tmp_path, bad)
        assert "full write-up" not in html, f"{bad} was linked"


def test_ordinary_links_still_render(cfg, tmp_path):
    """Negative control. If the allowlist refused everything, the tests above
    would pass for the wrong reason and the blog would quietly lose every
    permalink it has ever published."""
    html = _one_post(cfg, tmp_path, "https://x.io/journal.html#abc12345")
    assert 'href="https://x.io/journal.html#abc12345"' in html
    assert "full write-up" in html


def test_a_relative_link_is_still_allowed(cfg, tmp_path):
    html = _one_post(cfg, tmp_path, "journal.html#abc12345")
    assert 'href="journal.html#abc12345"' in html
