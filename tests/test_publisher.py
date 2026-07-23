"""Publisher platform (Phase B): read-only against agent state, revenue
gates enforced at the money boundary, magic-link auth, tiered content,
dry-run email, stub billing — all offline."""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from ledger import Ledger
from publisher import auth as pauth
from publisher import billing, config as pconfig, content, digest, gates
from publisher.app import app
from publisher.readonly import ReadOnlyLedger
from publisher.subscribers import SubscriberDB


# ---- fixtures ----

def _seed_agent_state(tmp_path, n_closed=1, first_ts=None):
    """A miniature agent memory/ with real records."""
    led = Ledger(str(tmp_path / "memory" / "ledger.jsonl"))
    if first_ts:  # backdate history by writing a raw first event
        led._store.append({"type": "event", "event": "cycle_complete",
                           "detail": '{"equity": 100000.0}', "ts": first_ts})
    for i in range(n_closed):
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
def pub_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISHER_SESSION_SECRET", "test-secret-key")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "mode": "paper",
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "x_posting": {"journal_path": "memory/journal.jsonl"},
    }))
    _seed_agent_state(tmp_path)
    cfg = pconfig.load(str(tmp_path))

    app.state.cfg = cfg
    app.state.db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    app.state.ledger = ReadOnlyLedger("memory/ledger.jsonl")
    app.state.secret = b"test-secret-key"
    client = TestClient(app)
    yield client, cfg, tmp_path
    for attr in ("cfg", "db", "ledger", "secret", "limiter"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _login(client, cfg, tmp_path, email="user@example.com"):
    r = client.post("/auth/request-link", json={"email": email})
    assert r.status_code == 200
    # the raw token is never in the response — recover the link from outbox
    outbox = (tmp_path / "publisher_data" / "outbox.jsonl").read_text()
    assert "sign-in link" in outbox.lower()
    # token isn't in the outbox either (only metadata) — issue a fresh one
    # through the DB directly, as the email body would have carried it.
    token = pauth.new_token()
    app.state.db.issue_token(email, "login", token, 30)
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    return email


# ---- invariant #9: publisher is read-only ----

def test_publisher_never_writes_agent_state(pub_env):
    client, cfg, tmp_path = pub_env
    mem = tmp_path / "memory"
    before = {p.name: p.read_bytes() for p in mem.iterdir()}

    client.get("/")
    client.get("/feed")
    _login(client, cfg, tmp_path)
    client.get("/feed")
    client.get("/account")
    client.post("/billing/checkout")
    ro = ReadOnlyLedger("memory/ledger.jsonl")
    content.feed(cfg, ro, "paid")
    digest.daily_digest_html(content.feed(cfg, ro, "free"), "http://x", "u")

    after = {p.name: p.read_bytes() for p in mem.iterdir()}
    assert before == after                      # byte-identical, invariant #9
    assert not hasattr(ro, "log_decision")      # writes aren't even present
    assert not hasattr(ro, "log_event")


def test_readonly_ledger_store_is_structurally_read_only():
    """Invariant #9 must be an AttributeError, not a convention: the store
    backing ReadOnlyLedger has no append method at all."""
    ro = ReadOnlyLedger("memory/ledger.jsonl")   # no cfg -> jsonl read-only
    assert not hasattr(ro._store, "append")
    with pytest.raises(AttributeError):
        ro._store.append({"type": "event", "event": "injected"})
    assert not hasattr(ro, "log_decision") and not hasattr(ro, "log_event")


def test_healthz_readonly_never_writes_or_reconfigures_under_sqlite(
        tmp_path, monkeypatch):
    """The publisher's /healthz path must not flip the process-wide storage
    backend or issue DDL against agent state (the SqliteStore DDL write vector
    the audit flagged). With sqlite configured but no agent.db present, a
    read-only status must report the backend, leave the global untouched, and
    create no database file."""
    monkeypatch.chdir(tmp_path)
    import store as store_mod
    import health
    (tmp_path / "memory").mkdir()
    cfg = {"mode": "paper",
           "memory": {"ledger_path": "memory/ledger.jsonl"},
           "storage": {"backend": "sqlite", "sqlite_path": "memory/agent.db"}}
    store_mod.configure(None)                          # global backend = jsonl
    try:
        s = health.status(cfg, read_only=True)
        assert s["storage_backend"] == "sqlite"        # reported from cfg
        assert store_mod.current_backend() == "jsonl"  # global NOT flipped
        assert not (tmp_path / "memory" / "agent.db").exists()  # no DDL/write
        assert s["open_positions"] == 0
    finally:
        store_mod.configure(None)


# ---- invariant #10: revenue gates ----

def test_gate_closed_today_with_reasons(pub_env):
    client, cfg, tmp_path = pub_env
    open_, reasons = gates.revenue_gate(cfg, app.state.ledger)
    assert not open_
    assert any("track record" in r for r in reasons)
    assert any("attorney" in r for r in reasons)
    assert any("drafts" in r for r in reasons)


def test_checkout_refused_while_gate_closed(pub_env):
    client, cfg, tmp_path = pub_env
    _login(client, cfg, tmp_path)
    r = client.post("/billing/checkout")
    assert r.status_code == 403
    assert "revenue_gate_closed" in r.text


def test_gate_opens_with_record_and_signoff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    led = _seed_agent_state(tmp_path, n_closed=30, first_ts=first)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "publisher": {"attorney_signoff": True, "legal_pages_final": True},
    }))
    cfg = pconfig.load(str(tmp_path))
    open_, reasons = gates.revenue_gate(cfg, led)
    assert open_ and reasons == []
    # and checkout (stub mode) now issues a session
    db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    out = billing.create_checkout(cfg, led, db, "a@b.com")
    assert out["ok"] and out["mode"] == "stub"


# ---- auth ----

def test_magic_link_single_use_and_expiry(pub_env):
    client, cfg, tmp_path = pub_env
    email = "once@example.com"
    token = pauth.new_token()
    app.state.db.issue_token(email, "login", token, 30)
    assert client.get(f"/auth/verify?token={token}",
                      follow_redirects=False).status_code == 303
    assert client.get(f"/auth/verify?token={token}",
                      follow_redirects=False).status_code == 400  # burned
    expired = pauth.new_token()
    app.state.db.issue_token(email, "login", expired, ttl_minutes=-1)
    assert client.get(f"/auth/verify?token={expired}",
                      follow_redirects=False).status_code == 400


def test_session_cookie_not_secure_over_plain_http(pub_env):
    """Without a trusted https proxy the cookie is not marked Secure (so it
    works over http); the Secure flag is scheme-driven, not hardcoded off."""
    client, cfg, tmp_path = pub_env
    token = pauth.new_token()
    app.state.db.issue_token("sec@example.com", "login", token, 30)
    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert "secure" not in r.headers.get("set-cookie", "").lower()


def test_session_cookie_secure_behind_trusted_https_proxy(pub_env):
    """Behind a trusted proxy terminating TLS (X-Forwarded-Proto: https) the
    session cookie is marked Secure."""
    client, cfg, tmp_path = pub_env
    cfg["publisher"]["trust_proxy"] = True
    token = pauth.new_token()
    app.state.db.issue_token("sec2@example.com", "login", token, 30)
    r = client.get(f"/auth/verify?token={token}",
                   headers={"X-Forwarded-Proto": "https"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "secure" in r.headers.get("set-cookie", "").lower()


def test_session_cookie_tamper_rejected():
    secret = b"k"
    good = pauth.make_session(secret, "a@b.com", 1)
    assert pauth.verify_session(secret, good) == "a@b.com"
    assert pauth.verify_session(secret, good[:-1] + "0") is None
    assert pauth.verify_session(b"other", good) is None
    assert pauth.verify_session(secret, None) is None


def test_tokens_stored_hashed(pub_env):
    client, cfg, tmp_path = pub_env
    token = pauth.new_token()
    app.state.db.issue_token("h@x.com", "login", token, 30)
    import sqlite3
    rows = sqlite3.connect(
        str(tmp_path / "publisher_data" / "pub.db")).execute(
        "SELECT token_hash FROM tokens").fetchall()
    assert all(token not in r[0] for r in rows)          # raw never stored
    assert any(hashlib.sha256(token.encode()).hexdigest() == r[0]
               for r in rows)


# ---- tiers ----

def test_free_feed_withholds_reasoning_and_delays(pub_env):
    client, cfg, tmp_path = pub_env
    body = client.get("/feed").json()
    assert body["tier"] == "free"
    assert body["delayed_by_days"] == 1
    assert "secret-judge-reasoning" not in json.dumps(body)
    assert body["disclaimer"].startswith("Repete is a PAPER-TRADING")


def test_paid_feed_has_reasoning_same_day(pub_env):
    client, cfg, tmp_path = pub_env
    email = _login(client, cfg, tmp_path)
    app.state.db.grant(email, "paid", "manual:test")
    body = client.get("/feed").json()
    assert body["tier"] == "paid" and body["delayed_by_days"] == 0
    dumped = json.dumps(body)
    assert "secret-judge-reasoning" in dumped
    assert '"bull_case"' in dumped
    assert body["disclaimer"]                        # paid still disclaims


def test_entitlement_expiry_downgrades(pub_env):
    client, cfg, tmp_path = pub_env
    db = app.state.db
    db.upsert_subscriber("exp@x.com")
    db.grant("exp@x.com", "paid", "manual:t",
             expires=datetime.now(timezone.utc) - timedelta(days=1))
    assert db.tier("exp@x.com") == "free"


# ---- billing stub webhook (invariant #10 guards) ----

@pytest.fixture()
def open_gate_env(tmp_path, monkeypatch):
    """A publisher env with the revenue gate OPEN (30 closed trades, 120d
    history, attestations set) and stub webhook grants enabled for testing."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISHER_SESSION_SECRET", "test-secret-key")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    first = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    _seed_agent_state(tmp_path, n_closed=30, first_ts=first)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "mode": "paper",
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "x_posting": {"journal_path": "memory/journal.jsonl"},
        "publisher": {"attorney_signoff": True, "legal_pages_final": True,
                      "billing": {"stub_webhook_grants": True}},
    }))
    cfg = pconfig.load(str(tmp_path))
    app.state.cfg = cfg
    app.state.db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    app.state.ledger = ReadOnlyLedger("memory/ledger.jsonl", cfg)
    app.state.secret = b"test-secret-key"
    yield TestClient(app), cfg, tmp_path
    for attr in ("cfg", "db", "ledger", "secret", "limiter"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _grant_evt():
    return {"type": "checkout.session.completed",
            "data": {"object": {"customer_email": "buyer@x.com",
                                "subscription": "sub_1"}}}


def test_stub_webhook_refused_while_gate_closed(pub_env):
    """Invariant #10: a stub webhook must not grant entitlements while the
    revenue gate is closed (the audit's ungated-grant bypass)."""
    client, cfg, tmp_path = pub_env  # gate closed (1 trade, no attestations)
    r = client.post("/billing/webhook", content=json.dumps(_grant_evt()))
    assert r.status_code == 400
    assert app.state.db.tier("buyer@x.com") == "free"  # never granted


def test_stub_webhook_inert_without_optin_even_when_gate_open(
        tmp_path, monkeypatch):
    """An unsigned stub webhook must not grant unless explicitly enabled, even
    with the gate open — protects a production deploy that forgot Stripe keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISHER_SESSION_SECRET", "test-secret-key")
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    first = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    _seed_agent_state(tmp_path, n_closed=30, first_ts=first)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "publisher": {"attorney_signoff": True, "legal_pages_final": True},
    }))  # stub_webhook_grants defaults False
    cfg = pconfig.load(str(tmp_path))
    app.state.cfg = cfg
    app.state.db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    app.state.ledger = ReadOnlyLedger("memory/ledger.jsonl", cfg)
    app.state.secret = b"test-secret-key"
    try:
        r = TestClient(app).post("/billing/webhook",
                                 content=json.dumps(_grant_evt()))
        assert r.status_code == 400 and "stub_webhook_grants_disabled" in r.text
        assert app.state.db.tier("buyer@x.com") == "free"
    finally:
        for attr in ("cfg", "db", "ledger", "secret", "limiter"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


def test_stub_webhook_grants_and_revokes_when_gate_open(open_gate_env):
    client, cfg, tmp_path = open_gate_env
    r = client.post("/billing/webhook", content=json.dumps(_grant_evt()))
    assert r.status_code == 200 and app.state.db.tier("buyer@x.com") == "paid"
    evt2 = {"type": "customer.subscription.deleted",
            "data": {"object": {"customer_email": "buyer@x.com"}}}
    client.post("/billing/webhook", content=json.dumps(evt2))
    assert app.state.db.tier("buyer@x.com") == "free"  # revoke always proceeds


def test_revenue_thresholds_not_config_overridable(tmp_path, monkeypatch):
    """Invariant #10: lowering publisher.revenue.* in config must NOT open the
    gate — the numeric thresholds are hardcoded constants in gates.py."""
    monkeypatch.chdir(tmp_path)
    _seed_agent_state(tmp_path, n_closed=3)  # only 3 closed trades
    (tmp_path / "config.yaml").write_text(json.dumps({
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "publisher": {"attorney_signoff": True, "legal_pages_final": True,
                      "revenue": {"min_closed_trades": 1,      # attempt to
                                  "min_history_days": 0}},     # weaken the gate
    }))
    cfg = pconfig.load(str(tmp_path))
    led = Ledger(str(tmp_path / "memory" / "ledger.jsonl"))
    open_, reasons = gates.revenue_gate(cfg, led)
    assert not open_                                    # override ignored
    assert any("gate: 30" in r for r in reasons)        # hardcoded 30 still wins


# ---- digest / email ----

def test_digest_dry_run_writes_outbox_no_network(pub_env, monkeypatch):
    client, cfg, tmp_path = pub_env
    import httpx
    monkeypatch.setattr(httpx, "post", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network call in dry-run")))
    ro = ReadOnlyLedger("memory/ledger.jsonl")
    html = digest.daily_digest_html(content.feed(cfg, ro, "free"),
                                    "http://x", "http://x/unsub")
    assert "PAPER-TRADING" in html and "unsubscribe" in html
    rec = digest.send(cfg, "a@b.com", "s", html)
    assert rec["dry_run"] and rec["status"] == "queued"
    outbox = (tmp_path / "publisher_data" / "outbox.jsonl").read_text()
    assert '"to": "a@b.com"' in outbox


# ---- misc surface ----

def test_account_requires_session(pub_env):
    client, cfg, tmp_path = pub_env
    assert client.get("/account").status_code == 401


def test_legal_pages_carry_draft_banner(pub_env):
    client, cfg, tmp_path = pub_env
    for page in ("tos", "privacy", "risk"):
        r = client.get(f"/legal/{page}")
        assert r.status_code == 200
        assert "REQUIRES ATTORNEY REVIEW" in r.text


def test_home_shows_gate_reasons_honestly(pub_env):
    client, cfg, tmp_path = pub_env
    r = client.get("/")
    assert "not open yet" in r.text
    assert "track record too small" in r.text
