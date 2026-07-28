"""The bot's own blog must not depend on a third party accepting its posts.

Why this file exists
--------------------
`blog.render` filtered its source archive to `status == "posted"`. That made a
page titled "everything the bot posts" into a mirror of X's acceptances.

The cost was measured, not imagined. The four `X_*` values in `.env` were
emptied some time after 2026-07-24. On 2026-07-27 the bot placed ten trades,
wrote ten recaps, and archived all ten as `failed` — and **the public blog sat
frozen at 2026-07-24 while the bot traded.** Nobody reading it could tell the
difference between "the bot did nothing" and "the bot did ten things and X was
unreachable".

The second, sharper defect was latent: `post_text` returned on
`if not xc["enabled"]` **before** `_archive()`. So the obvious response to a
dead X account — switch it off — would have stopped anything reaching
`posts.jsonl` at all, and the blog would have gone from stale to empty. The fix
had to land in the same change as the filter removal, which is what
`test_the_blog_survives_x_being_switched_off` pins.

The shape of the fix: **compose → record → attempt.** Composition (paper
disclosure, length cap, journal link) happens before any early return, the
archive is written on every path, and `status` records what delivery did.

Five statuses, five different facts:

  * ``posted``          — X accepted it
  * ``failed``          — X was reached and rejected or errored
  * ``no_credentials``  — configured ON but unusable; fix the environment
  * ``dry_run``         — a rehearsal, deliberately not sent
  * ``x_disabled``      — X switched off deliberately

The last two are *choices*, so they must never write a degradation; the first
three-minus-one are *faults*, so two of them must. That distinction is what
keeps the degradation SLO meaningful once X is off for good, and
`test_switching_x_off_is_not_a_degradation` is the test that protects it.
"""
import json
import types

import pytest

import blog
import x_poster


@pytest.fixture
def cfg(tmp_path):
    return {
        "x_posting": {"enabled": True, "dry_run": False,
                      "disclose_paper": True,
                      "posts_log_path": str(tmp_path / "posts.jsonl")},
        "memory": {"ledger_path": str(tmp_path / "ledger.jsonl")},
    }


def _archived(cfg):
    try:
        return [json.loads(l) for l in open(cfg["x_posting"]["posts_log_path"])
                if l.strip()]
    except OSError:
        return []


def _degradations(cfg):
    try:
        rows = [json.loads(l) for l in open(cfg["memory"]["ledger_path"])
                if l.strip()]
    except OSError:
        return []
    return [r for r in rows
            if r.get("type") == "event" and r.get("event") == "degradation"]


def _render(cfg, tmp_path):
    out = tmp_path / "blog.html"
    blog.render(cfg, out_path=str(out))
    return out.read_text()


def _creds(monkeypatch, value="x" * 20):
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, value)


def _blank_creds(monkeypatch):
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")


# ---- the blog renders what the bot wrote, not what X accepted ----

def test_a_failed_post_still_reaches_the_blog(monkeypatch, cfg, tmp_path):
    """The exact 2026-07-27 case: ten recaps written, X unreachable, blog
    frozen. One post is enough to pin the behaviour."""
    _creds(monkeypatch)
    monkeypatch.setattr(x_poster, "_client", lambda: (_ for _ in ()).throw(
        RuntimeError("401 Unauthorized")))
    x_poster.post_text("bought NVDA because the 20/50 crossed up", cfg)

    assert [r["status"] for r in _archived(cfg)] == ["failed"]
    assert "the 20/50 crossed up" in _render(cfg, tmp_path)


def test_a_no_credentials_post_still_reaches_the_blog(
        monkeypatch, cfg, tmp_path):
    _blank_creds(monkeypatch)
    x_poster.post_text("closed AMD for +3.1%", cfg)

    assert [r["status"] for r in _archived(cfg)] == ["no_credentials"]
    assert "closed AMD for +3.1%" in _render(cfg, tmp_path)


def test_a_posted_post_still_reaches_the_blog(monkeypatch, cfg, tmp_path):
    """The permissive half. Without it, a render that dropped EVERYTHING would
    pass every test above that only checks for absence."""
    _creds(monkeypatch)
    monkeypatch.setattr(x_poster, "_client", lambda: types.SimpleNamespace(
        create_tweet=lambda text: types.SimpleNamespace(data={"id": "1"})))
    x_poster.post_text("bought JPM on the breakout", cfg)

    assert [r["status"] for r in _archived(cfg)] == ["posted"]
    assert "bought JPM on the breakout" in _render(cfg, tmp_path)


def test_every_status_appears_on_one_page(monkeypatch, cfg, tmp_path):
    """Mixed days are the normal case — Jul 24 posted, Jul 27 failed, Jul 28
    off. The page must be continuous across all of them."""
    _creds(monkeypatch)
    monkeypatch.setattr(x_poster, "_client", lambda: types.SimpleNamespace(
        create_tweet=lambda text: types.SimpleNamespace(data={"id": "1"})))
    x_poster.post_text("post which went out", cfg)

    monkeypatch.setattr(x_poster, "_client", lambda: (_ for _ in ()).throw(
        RuntimeError("boom")))
    x_poster.post_text("post which failed", cfg)

    _blank_creds(monkeypatch)
    x_poster.post_text("post with no credentials", cfg)

    cfg["x_posting"]["enabled"] = False
    x_poster.post_text("post while X was off", cfg)

    html = _render(cfg, tmp_path)
    for fragment in ("went out", "failed", "no credentials", "X was off"):
        assert fragment in html, f"{fragment!r} missing from the blog"


# ---- switching X off must not empty the blog ----

def test_the_blog_survives_x_being_switched_off(monkeypatch, cfg, tmp_path):
    """The latent defect. `post_text` used to return on `not enabled` BEFORE
    archiving, so turning X off — the obvious fix for a dead account — would
    have taken the blog down with it."""
    cfg["x_posting"]["enabled"] = False
    _blank_creds(monkeypatch)
    x_poster.post_text("the bot still has things to say", cfg)

    assert [r["status"] for r in _archived(cfg)] == ["x_disabled"]
    assert "the bot still has things to say" in _render(cfg, tmp_path)


def test_post_recap_also_survives_x_being_switched_off(
        monkeypatch, cfg, tmp_path):
    """`post_recap` carried its own duplicate `enabled` guard, which would have
    short-circuited before `post_text` ever ran."""
    cfg["x_posting"]["enabled"] = False
    cfg["x_posting"]["hashtags"] = ""
    x_poster.post_recap({"symbol": "CAT", "action": "buy", "qty": 3,
                         "entry_price": 412.5,
                         "strategy_reason": "momentum breakout"}, cfg)

    assert [r["status"] for r in _archived(cfg)] == ["x_disabled"]
    assert "CAT" in _render(cfg, tmp_path)


def test_an_empty_post_is_not_archived(cfg):
    """Nothing was written, so there is nothing to record. Guards against
    'archive on every path' degenerating into archiving blanks."""
    x_poster.post_text("", cfg)
    assert _archived(cfg) == []


# ---- a choice is not an incident ----

def test_switching_x_off_is_not_a_degradation(monkeypatch, cfg):
    """Deliberate configuration must not burn the degradation error budget.
    Otherwise turning X off — the owner's decision on 2026-07-28 — would
    generate an alert on every single post, forever."""
    cfg["x_posting"]["enabled"] = False
    _blank_creds(monkeypatch)
    x_poster.post_text("hello", cfg)
    assert _degradations(cfg) == []


def test_missing_credentials_is_still_a_degradation(monkeypatch, cfg):
    """The other half. `x_disabled` is a choice; `no_credentials` is a
    misconfiguration and must stay visible."""
    _blank_creds(monkeypatch)
    x_poster.post_text("hello", cfg)
    assert len(_degradations(cfg)) == 1


# ---- composition happens before recording, on every path ----

def test_the_archived_text_carries_the_paper_disclosure(monkeypatch, cfg):
    """Composition moved above the early returns, so this has to hold on the
    paths that never touch X — not just the delivered one."""
    cfg["x_posting"]["enabled"] = False
    x_poster.post_text("bought NVDA", cfg)
    assert _archived(cfg)[0]["text"].startswith("[PAPER] ")


def test_the_archived_text_respects_the_cap(monkeypatch, cfg):
    cfg["x_posting"]["enabled"] = False
    x_poster.post_text("z" * 1000, cfg)
    assert len(_archived(cfg)[0]["text"]) <= 275


def test_the_journal_link_survives_x_being_off(monkeypatch, cfg, tmp_path):
    """The link is the whole point of the blog entry — it is how a reader gets
    from a one-line recap to the 500-word write-up."""
    cfg["x_posting"]["enabled"] = False
    url = "https://example.invalid/journal.html#abc123"
    x_poster.post_text("bought LLY", cfg, link=url)

    row = _archived(cfg)[0]
    assert row["link"] == url
    assert url in row["text"]
    assert url in _render(cfg, tmp_path)


# ---- and the page stays honest about what it is ----

def test_delivery_status_is_not_shown_to_readers(monkeypatch, cfg, tmp_path):
    """`status` is an operational fact about X, kept for audit. A trade blog
    owes its reader the bot's reasoning, not its syndication receipts."""
    _blank_creds(monkeypatch)
    x_poster.post_text("bought XOM", cfg)
    html = _render(cfg, tmp_path)
    for leak in ("no_credentials", "x_disabled", "X_API_KEY"):
        assert leak not in html, f"{leak!r} leaked onto the public page"


def test_an_empty_archive_still_renders(cfg, tmp_path):
    assert "No posts yet" in _render(cfg, tmp_path)
