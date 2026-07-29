"""The daily digest broadcast, and the opt-out link it mails.

Before this existed, `digest.daily_digest_html()` and
`SubscriberDB.active_emails()` both shipped and NOTHING joined them — no
caller, no scheduler job, no digest reaching anyone. The half that was missing
turned out to be larger than the join: `daily_digest_html()` takes an
`unsubscribe_url`, but the only unsubscribe route required a session, and an
unsubscribe link in an email is an unauthenticated GET from a mail client. So
every one of these tests is about a way this feature can look finished and be
broken.

Each test is written to fail against the specific wrong implementation named in
its docstring.
"""
import hashlib
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from fastapi.testclient import TestClient

from ledger import Ledger
from publisher import auth as pauth
from publisher import broadcast, config as pconfig, content, digest
from publisher.app import app
from publisher.readonly import ReadOnlyLedger
from publisher.subscribers import SubscriberDB

FREE = [f"free{i}@example.invalid" for i in (1, 2, 3)]
PAID = [f"paid{i}@example.invalid" for i in (1, 2)]
GONE = "gone@example.invalid"


def _seed_ledger(tmp_path):
    """A miniature agent memory/ carrying a judge reasoning string we can
    grep for, so tier leakage is visible rather than inferred."""
    led = Ledger(str(tmp_path / "memory" / "ledger.jsonl"))
    tid = led.log_decision(
        "SPY", "buy", "crossover", {"rsi": 4},
        {"verdict": "approve", "scale": 1.0, "confidence": 0.66,
         "reasoning": "secret-judge-reasoning", "bull_case": "b",
         "bear_case": "r", "cited_lessons": []},
        executed=True, entry_price=100.0, qty=10, strategy="tsmom",
        regime="up/low")
    led.close_trade(tid, 105.0, 50.0, 5.0, exit_reason="strategy_sell")
    led.log_event("cycle_complete", '{"equity": 100150.0}')
    return led


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A publisher wired to a temp tree, with the digest ENABLED.

    Purpose-built rather than reusing `pub_env` from test_publisher.py: this
    needs a populated subscriber list across tiers and the digest switch on,
    and pub_env deliberately has neither.

    RESEND_API_KEY is deleted, so even a bug that flipped dry_run could not
    reach the network.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISHER_SESSION_SECRET", "test-secret-key")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "mode": "paper",
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "x_posting": {"journal_path": "memory/journal.jsonl"},
        "publisher": {"digest": {"enabled": True}},
    }))
    _seed_ledger(tmp_path)
    cfg = pconfig.load(str(tmp_path))

    db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    for e in FREE + PAID + [GONE]:
        db.upsert_subscriber(e)
    for e in PAID:
        db.grant(e, "paid", source="test")
    db.unsubscribe(GONE)

    app.state.cfg = cfg
    app.state.db = db
    app.state.ledger = ReadOnlyLedger("memory/ledger.jsonl", cfg)
    app.state.secret = b"test-secret-key"
    yield TestClient(app), cfg, db, tmp_path
    for attr in ("cfg", "db", "ledger", "secret", "limiter"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _run(cfg, db, **kw):
    return broadcast.send_daily_digest(
        cfg, db, ReadOnlyLedger(cfg["memory"]["ledger_path"], cfg), **kw)


def _outbox(tmp_path):
    p = tmp_path / "publisher_data" / "outbox.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def _capture(monkeypatch):
    """Collect (to, subject, html) for every send without writing an outbox."""
    seen = []

    def fake(cfg, to_email, subject, html):
        seen.append((to_email, subject, html))
        return {"to": to_email, "subject": subject, "dry_run": True,
                "status": "queued", "ok": True}

    monkeypatch.setattr(digest, "send", fake)
    return seen


# ---- who gets mail ----

def test_broadcast_never_mails_an_unsubscribed_address(env):
    """The end-to-end version of test_active_emails_excludes_unsubscribed.

    Fails against an implementation that iterates the `subscribers` table, or
    `db._conn`, or anything other than active_emails() — all of which look
    correct and mail people who opted out.
    """
    _, cfg, db, tmp_path = env
    run = _run(cfg, db)

    mailed = {r["to"] for r in _outbox(tmp_path)}
    assert mailed == set(FREE + PAID), mailed
    assert GONE not in mailed, "unsubscribed address was mailed"
    assert run["recipients"] == 5 and run["failed"] == 0


def test_test_send_cannot_target_an_unsubscribed_address(env):
    """`--to` must be checked against the active list, not trusted.

    Fails if `only` is passed straight through to the send loop — a smoke-test
    flag that can reach someone who unsubscribed.
    """
    _, cfg, db, tmp_path = env
    run = _run(cfg, db, only=GONE)

    assert run["skipped_reason"] and "not an active subscriber" in run["skipped_reason"]
    assert _outbox(tmp_path) == []


def test_no_active_subscribers_is_a_named_outcome_not_a_silent_success(env):
    """An empty list must be reported, never mistaken for a clean run.

    This is how the feature breaks quietly: a wrong DB path yields an empty
    database, zero recipients, zero errors, exit 0 — indistinguishable from
    success. Fails if the code returns a bare zero-recipient result.
    """
    _, cfg, db, tmp_path = env
    for e in FREE + PAID:
        db.unsubscribe(e)

    run = _run(cfg, db)
    assert run["skipped_reason"] == "no active subscribers"
    assert _outbox(tmp_path) == []


# ---- what they get ----

def test_one_feed_is_built_per_tier_not_per_recipient(env, monkeypatch):
    """5 recipients across 2 tiers must cost 2 feed builds, not 5.

    content.feed() walks the whole record stream several times; at the QA
    fixture's 18,597 records a per-recipient build is tens of millions of
    traversals for output that is identical within a tier.
    """
    _, cfg, db, _ = env
    _capture(monkeypatch)
    calls = []
    real = content.feed
    monkeypatch.setattr(content, "feed",
                        lambda c, l, tier, **kw: (calls.append(tier),
                                                  real(c, l, tier, **kw))[1])

    _run(cfg, db)
    assert sorted(calls) == ["free", "paid"], calls


def test_free_and_paid_bodies_differ(env, monkeypatch):
    """The tier boundary must survive into the email body.

    Fails if the broadcast builds one feed for everyone — the failure mode
    that mails paid analysis to free subscribers.

    Note the free feed's `decisions` list is EMPTY here: free_delay_days is 1
    and the seed writes at now, so the assertion is on the delay banner and
    the absence of reasoning, not on decision presence.
    """
    _, cfg, db, _ = env
    seen = _capture(monkeypatch)
    _run(cfg, db)

    bodies = {to: html for to, _s, html in seen}
    paid_html, free_html = bodies[PAID[0]], bodies[FREE[0]]

    assert "secret-judge-reasoning" in paid_html
    assert "secret-judge-reasoning" not in free_html, (
        "judge reasoning leaked into the free-tier digest")
    assert "Free tier" in free_html and "delayed" in free_html
    assert "[PAID]" in paid_html and "[FREE]" in free_html


def test_subject_carries_the_paper_disclosure(env, monkeypatch):
    """A recipient scanning an inbox should see what this is before opening
    it, and subjects survive forwarding when footers get trimmed."""
    _, cfg, db, _ = env
    seen = _capture(monkeypatch)
    _run(cfg, db)
    assert all("[PAPER]" in s for _to, s, _h in seen)


def test_equity_of_exactly_zero_renders_zero_not_na():
    """`f"${eq:,.2f}" if eq else "n/a"` reported a blown-up account as a
    missing datapoint. Only None means unknown."""
    html = digest.daily_digest_html(
        {"equity": {"equity": 0.0}, "tier": "free", "open_positions": 0,
         "closed_trades": 0, "decisions": []}, "http://x", "http://x/u")
    assert "$0.00" in html and "n/a" not in html


# ---- the unsubscribe link ----

def _link_for(env, monkeypatch, email):
    _, cfg, db, _ = env
    seen = _capture(monkeypatch)
    _run(cfg, db)
    html = {to: h for to, _s, h in seen}[email]
    m = re.search(r'href="([^"]*/unsubscribe\?token=[^"]+)"', html)
    assert m, f"no unsubscribe link in the body: {html[:400]}"
    return m.group(1)


def test_emailed_unsubscribe_link_works_end_to_end(env, monkeypatch):
    """Capture the real link out of the real body and drive it.

    Fails without the GET route, without per-recipient token issuance, or if
    the link points somewhere nothing serves.
    """
    client, cfg, db, _ = env
    link = _link_for(env, monkeypatch, FREE[0])

    assert client.get(link).status_code == 200
    token = link.split("token=")[1]
    r = client.post(f"/unsubscribe/confirm?token={token}",
                    follow_redirects=False)
    assert r.status_code == 303
    assert FREE[0] not in db.active_emails()


def test_get_unsubscribe_does_not_unsubscribe_and_does_not_consume(
        env, monkeypatch):
    """Prefetch safety — the most important test here.

    Gmail's link proxy, Outlook Safe Links and Proofpoint all issue
    unauthenticated GETs against links in mail. A mutating GET would silently
    opt people out of a list they wanted, invisibly to both sides. A GET that
    merely CONSUMED the token would be almost as bad: the scanner burns it and
    the subscriber's real click then fails.

    Fails if the GET handler calls unsubscribe() or consume_token().
    """
    client, cfg, db, _ = env
    link = _link_for(env, monkeypatch, FREE[0])

    for _ in range(2):                       # a scanner, then a preview pane
        assert client.get(link).status_code == 200
    assert FREE[0] in db.active_emails(), "a GET unsubscribed the address"

    token = link.split("token=")[1]
    assert client.post(f"/unsubscribe/confirm?token={token}",
                       follow_redirects=False).status_code == 303, (
        "the GET consumed the token, so the human's real click failed")
    assert FREE[0] not in db.active_emails()


def test_unsubscribe_token_is_single_use(env, monkeypatch):
    """Fails if the link carries an email instead of a one-time token —
    which would let anyone unsubscribe anyone by editing the URL."""
    client, cfg, db, _ = env
    link = _link_for(env, monkeypatch, FREE[0])
    token = link.split("token=")[1]

    assert client.post(f"/unsubscribe/confirm?token={token}",
                       follow_redirects=False).status_code == 303
    assert client.post(f"/unsubscribe/confirm?token={token}").status_code == 400
    assert client.get(link).status_code == 400


def test_unsubscribe_page_works_without_a_session(env, monkeypatch):
    """The recipient is not signed in — that is the entire point.

    Fails against the most likely wrong implementation: copying
    `Depends(require_session)` from the existing POST /unsubscribe.
    """
    client, cfg, db, _ = env
    link = _link_for(env, monkeypatch, FREE[0])
    client.cookies.clear()

    r = client.get(link)
    assert r.status_code == 200, f"got {r.status_code} — signed-out user refused"
    assert FREE[0] in r.text


def test_expired_unsubscribe_token_is_refused_not_a_crash(env):
    """An expired opt-out link must explain itself, never 500."""
    client, cfg, db, _ = env
    tok = pauth.new_token()
    db.issue_token(FREE[0], "unsubscribe", tok, -1)

    assert client.get(f"/unsubscribe?token={tok}").status_code == 400
    assert client.post(f"/unsubscribe/confirm?token={tok}").status_code == 400
    assert FREE[0] in db.active_emails()


def test_tokens_do_not_cross_purposes(env):
    """A login token must not unsubscribe, and — the serious direction — an
    unsubscribe token must not mint a session, which would be account takeover
    of every address the digest is mailed to."""
    client, cfg, db, _ = env
    login_tok, unsub_tok = pauth.new_token(), pauth.new_token()
    db.issue_token(FREE[0], "login", login_tok, 30)
    db.issue_token(FREE[1], "unsubscribe", unsub_tok, 30)

    assert client.post(
        f"/unsubscribe/confirm?token={login_tok}").status_code == 400
    assert FREE[0] in db.active_emails()

    r = client.get(f"/auth/verify?token={unsub_tok}", follow_redirects=False)
    assert r.status_code == 400, "an unsubscribe token minted a session"


def test_bad_token_page_does_not_distinguish_failure_modes(env):
    """Invalid, expired and already-used render the same page, so a guessed
    token cannot be used to learn whether an address exists."""
    client, cfg, db, _ = env
    expired = pauth.new_token()
    db.issue_token(FREE[0], "unsubscribe", expired, -1)

    a = client.get("/unsubscribe?token=never-existed")
    b = client.get(f"/unsubscribe?token={expired}")
    assert a.status_code == b.status_code == 400
    assert a.text == b.text


# ---- safety rails ----

def test_broadcast_refuses_when_the_digest_is_disabled(env, monkeypatch):
    """The enable flag must be load-bearing, and must be checked FIRST.

    Zero outbox lines AND zero tokens issued — a flag checked halfway down has
    already had side effects by the time it says no. Every other test in this
    file has to turn the flag on, which is what proves it works.
    """
    _, cfg, db, tmp_path = env
    cfg["publisher"]["digest"]["enabled"] = False
    before = db._conn.execute("SELECT COUNT(*) c FROM tokens").fetchone()["c"]

    run = _run(cfg, db)
    assert "enabled is false" in run["skipped_reason"]
    assert _outbox(tmp_path) == []
    after = db._conn.execute("SELECT COUNT(*) c FROM tokens").fetchone()["c"]
    assert after == before, "tokens were minted before the gate was checked"


def test_broadcast_is_dry_run_by_default_and_touches_no_network(
        env, monkeypatch):
    """httpx.post raises if called at all. In dry-run, digest.send() must not
    even reach the import."""
    _, cfg, db, tmp_path = env
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network call from a dry run")))

    run = _run(cfg, db)
    assert run["sent"] == 0 and run["queued"] == 5
    assert all(r["dry_run"] for r in _outbox(tmp_path))


def test_partial_failure_is_counted_and_the_loop_keeps_going(env, monkeypatch):
    """digest.send() swallows transport errors and NEVER raises, so the run
    record is the only signal. Fails if the loop breaks on first error, which
    would mail the first half of the alphabet and stop."""
    _, cfg, db, _ = env
    calls = []

    def flaky(cfg_, to_email, subject, html):
        calls.append(to_email)
        bad = to_email == FREE[1]
        return {"to": to_email, "dry_run": True, "ok": not bad,
                "status": "error:boom" if bad else "queued"}

    monkeypatch.setattr(digest, "send", flaky)
    run = _run(cfg, db)

    assert len(calls) == 5, "the loop stopped early"
    assert run["failed"] == 1 and run["queued"] == 4
    assert run["failures"][0]["to"] == FREE[1]
    assert "boom" in run["failures"][0]["status"]


def test_a_second_run_the_same_day_is_refused_without_force(env):
    """The scheduler dedupes per scheduled minute; it cannot see a human
    running the CLI twice, which would mail the whole list again."""
    _, cfg, db, tmp_path = env
    _run(cfg, db)
    n = len(_outbox(tmp_path))

    again = _run(cfg, db)
    assert "already ran today" in again["skipped_reason"]
    assert len(_outbox(tmp_path)) == n

    forced = _run(cfg, db, force=True)
    assert forced["queued"] == 5 and len(_outbox(tmp_path)) == n * 2


def test_broadcast_writes_no_agent_state(env):
    """Invariant #9: the publisher is read-only against the ledger. A digest
    run must leave memory/ byte-identical."""
    _, cfg, db, tmp_path = env

    def snap():
        out = {}
        for root, _d, files in os.walk(tmp_path / "memory"):
            for fn in files:
                p = os.path.join(root, fn)
                out[p] = hashlib.sha256(open(p, "rb").read()).hexdigest()
        return out

    before = snap()
    _run(cfg, db)
    assert snap() == before, "the broadcast mutated agent state"


def test_run_is_auditable(env):
    """A send that left no record cannot be reasoned about afterwards."""
    _, cfg, db, tmp_path = env
    _run(cfg, db)

    runs = [json.loads(x) for x in
            (tmp_path / "publisher_data" / "digest_runs.jsonl"
             ).read_text().splitlines() if x.strip()]
    assert runs and runs[-1]["by_tier"] == {"free": 3, "paid": 2}
    assert runs[-1]["date_et"] and runs[-1]["failed"] == 0


# ---- documentation that has already rotted twice ----

def test_deploy_docs_state_the_real_scheduler_job_count():
    """`scheduler up — N jobs` is a verification step operators check against.
    It said 9 while JOBS held 11, having survived at least two job additions;
    a comment would not have stopped a third."""
    import scheduler

    root = os.path.join(os.path.dirname(__file__), "..")
    checked = 0
    for rel in ("deploy/README.md", "deploy/DIGITALOCEAN.md",
                "docs/runbooks.md"):
        path = os.path.join(root, rel)
        if not os.path.exists(path):        # DIGITALOCEAN.md is untracked
            continue
        for claimed in re.findall(r"scheduler up — (\d+) jobs",
                                  open(path).read()):
            checked += 1
            assert int(claimed) == len(scheduler.JOBS), (
                f"{rel} claims {claimed} scheduler jobs; "
                f"scheduler.JOBS holds {len(scheduler.JOBS)}")
    assert checked, "no documented job count found — did the wording change?"
