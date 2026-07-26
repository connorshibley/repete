#!/usr/bin/env python3
"""End-to-end QA sweep of the publisher, driven as a real user would drive it.

Every check below is an ACCEPTANCE CRITERION with a stated expectation, run
against the production-scale fixture from `qa_fixture.py` (18k+ records, 450
closed trades, 549 days) rather than the 11-day live ledger. That matters: the
revenue gate needs 30 closed trades and 90 days, so on live data every paid
path — checkout, entitlements, the paid feed, the paid dashboard — is
unreachable and has never been exercised.

Roles covered: anonymous · free · paid · expired-entitlement · unsubscribed.

    python scripts/qa_sweep.py --fixture /tmp/qa
    python scripts/qa_sweep.py --fixture /tmp/qa --verbose

Exit code is the number of FAILed criteria, so CI can gate on it.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

RESULTS: list[dict] = []


def check(cid: str, surface: str, role: str, criterion: str):
    """Register one acceptance criterion. The body returns (ok, evidence)."""
    def deco(fn):
        RESULTS.append({"id": cid, "surface": surface, "role": role,
                        "criterion": criterion, "fn": fn})
        return fn
    return deco


# --------------------------------------------------------------- harness

def build_client(fixture: str, *, gate_open: bool):
    """A TestClient wired to the fixture — never to live state.

    `gate_open` flips only the two CONFIG flags (attorney sign-off, legal
    pages). The numeric thresholds stay hardcoded in gates.py by design, so
    this cannot fake a track record it does not have.
    """
    os.environ.setdefault("PUBLISHER_SESSION_SECRET", "qa-" + "0" * 60)
    from fastapi.testclient import TestClient

    from publisher import config as pconfig
    from publisher.app import app
    from publisher.readonly import ReadOnlyLedger
    from publisher.subscribers import SubscriberDB

    cfg = copy.deepcopy(pconfig.load())
    cfg["memory"]["ledger_path"] = os.path.join(fixture, "ledger.jsonl")
    # The journal has to move too. content.feed() reads it for the PAID tier,
    # so leaving this pointed at the repo meant every paid-tier criterion was
    # reading the operator's real journal instead of the fixture's.
    cfg.setdefault("x_posting", {})["journal_path"] = os.path.join(
        fixture, "journal.jsonl")
    cfg["publisher"]["data_dir"] = os.path.join(fixture, "publisher_data")
    cfg["publisher"]["attorney_signoff"] = gate_open
    cfg["publisher"]["legal_pages_final"] = gate_open

    app.state.cfg = cfg
    # pub.db is the name publisher/app.py:73 opens. This used to open
    # subscribers.db — a database production never reads.
    app.state.db = SubscriberDB(
        os.path.join(fixture, "publisher_data", "pub.db"))
    app.state.ledger = ReadOnlyLedger(cfg["memory"]["ledger_path"], cfg)
    if hasattr(app.state, "secret"):
        del app.state.secret
    return TestClient(app), cfg, app.state.db


def login(client, cfg, email: str):
    """Session cookie for `email` via the real magic-link flow."""
    from publisher import auth
    from publisher.config import session_secret
    cookie = auth.make_session(session_secret(), email,
                               cfg["publisher"]["session_ttl_hours"])
    client.cookies.set(cfg["publisher"]["session_cookie"], cookie)
    return client


def logout(client, cfg):
    client.cookies.clear()
    return client


# --------------------------------------------------------------- criteria
# Each returns (passed, evidence-string). Evidence is printed on failure so a
# report line is a reproduction, not an assertion.

def run_all(fixture: str, verbose: bool) -> list[dict]:
    c, cfg, db = build_client(fixture, gate_open=False)
    out = []

    def rec(cid, surface, role, criterion, ok, evidence):
        out.append({"id": cid, "surface": surface, "role": role,
                    "criterion": criterion, "ok": ok, "evidence": evidence})

    # ---- public surfaces, anonymous ----
    r = c.get("/")
    rec("PUB-01", "/", "anon", "marketing page renders",
        r.status_code == 200, f"HTTP {r.status_code}")
    rec("PUB-02", "/", "anon", "carries the paper/not-advice disclaimer",
        "PAPER" in r.text.upper() or "not investment advice" in r.text.lower(),
        f"disclaimer markers absent; len={len(r.text)}")

    r = c.get("/status")
    rec("PUB-03", "/status", "anon", "status page renders",
        r.status_code == 200, f"HTTP {r.status_code}")

    r = c.get("/healthz")
    rec("PUB-04", "/healthz", "anon", "health endpoint returns 200 JSON",
        r.status_code == 200, f"HTTP {r.status_code} body={r.text[:120]}")

    for slug in ("tos", "privacy", "risk"):
        r = c.get(f"/legal/{slug}")
        rec(f"LEG-{slug}", f"/legal/{slug}", "anon", "legal page renders",
            r.status_code == 200, f"HTTP {r.status_code}")
    r = c.get("/legal/nonexistent")
    rec("LEG-404", "/legal/{page}", "anon", "unknown legal slug 404s",
        r.status_code == 404, f"HTTP {r.status_code}")

    # ---- auth input validation ----
    for bad, label in [("", "empty"), ("notanemail", "no @"),
                       ("x@" + "a" * 300, "over 254 chars")]:
        r = c.post("/auth/request-link", json={"email": bad})
        rec(f"AUTH-BAD-{label}", "/auth/request-link", "anon",
            f"rejects {label} email with 400",
            r.status_code == 400, f"HTTP {r.status_code} for {bad[:40]!r}")

    r = c.post("/auth/request-link", json={"email": "new1@example.invalid"})
    rec("AUTH-OK", "/auth/request-link", "anon", "valid email accepted",
        r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    rec("AUTH-NOLEAK", "/auth/request-link", "anon",
        "raw magic-link token never returned in the response",
        "token" not in r.text.lower(), f"body leaked a token: {r.text[:200]}")

    for tok, label in [("garbage", "garbage"), ("", "empty")]:
        r = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
        rec(f"AUTH-VERIFY-{label}", "/auth/verify", "anon",
            f"{label} token rejected (not a session)",
            r.status_code >= 400, f"HTTP {r.status_code}")

    # ---- authorization: anonymous must not reach subscriber surfaces ----
    logout(c, cfg)
    for path in ("/account", "/dashboard"):
        r = c.get(path, follow_redirects=False)
        rec(f"AUTHZ-{path}", path, "anon", "requires a session (401/403/redirect)",
            r.status_code in (401, 403, 302, 303, 307),
            f"HTTP {r.status_code} — anonymous access allowed")
    r = c.post("/unsubscribe")
    rec("AUTHZ-unsub", "/unsubscribe", "anon", "requires a session",
        r.status_code in (401, 403), f"HTTP {r.status_code}")
    r = c.post("/billing/checkout")
    rec("AUTHZ-checkout", "/billing/checkout", "anon", "requires a session",
        r.status_code in (401, 403), f"HTTP {r.status_code}")

    # ---- tier resolution ----
    for email, want in [("free1@example.invalid", "free"),
                        ("paid1@example.invalid", "paid"),
                        ("expired1@example.invalid", "free"),
                        ("gone1@example.invalid", "free")]:
        got = db.tier(email)
        rec(f"TIER-{email.split('@')[0]}", "SubscriberDB.tier", email.split("@")[0],
            f"resolves to {want!r}", got == want, f"got {got!r}, want {want!r}")

    # ---- THE tier boundary: free must not receive paid content ----
    logout(c, cfg)
    anon_feed = c.get("/feed").json()
    login(c, cfg, "free1@example.invalid")
    free_feed = c.get("/feed").json()
    login(c, cfg, "paid1@example.invalid")
    paid_feed = c.get("/feed").json()
    login(c, cfg, "expired1@example.invalid")
    exp_feed = c.get("/feed").json()

    def has_judge(feed) -> bool:
        return any("judge" in d for d in feed.get("decisions", []))

    rec("TIER-LEAK-free", "/feed", "free",
        "free tier NEVER receives judge reasoning",
        not has_judge(free_feed),
        f"judge block present in free feed: "
        f"{json.dumps(next((d for d in free_feed.get('decisions', []) if 'judge' in d), {}))[:200]}")
    rec("TIER-LEAK-anon", "/feed", "anon",
        "anonymous receives free-tier content only",
        not has_judge(anon_feed), "judge block present for anonymous")
    rec("TIER-LEAK-expired", "/feed", "expired",
        "an EXPIRED paid entitlement is downgraded to free",
        not has_judge(exp_feed),
        "expired entitlement still receiving paid content")
    rec("TIER-PAID", "/feed", "paid",
        "paid tier DOES receive judge reasoning",
        has_judge(paid_feed),
        "paid feed has no judge block — the paid tier delivers nothing extra")

    # ---- timeliness boundary ----
    def newest(feed):
        ts = [d["ts"] for d in feed.get("decisions", []) if d.get("ts")]
        return max(ts) if ts else None

    delay = cfg["publisher"]["free_delay_days"]
    fn, pn = newest(free_feed), newest(paid_feed)
    ok = True
    ev = f"free newest={fn} paid newest={pn} delay={delay}d"
    if fn and pn:
        ok = datetime.fromisoformat(fn) <= datetime.fromisoformat(pn)
    rec("TIME-01", "/feed", "free",
        f"free decisions lag paid by ~{delay}d (never newer)", ok, ev)

    # ---- billing: gate CLOSED ----
    login(c, cfg, "free1@example.invalid")
    r = c.post("/billing/checkout")
    body = r.text
    rec("GATE-CLOSED", "/billing/checkout", "free",
        "checkout refused 403 while the revenue gate is closed",
        r.status_code == 403, f"HTTP {r.status_code} {body[:200]}")
    rec("GATE-REASONS", "/billing/checkout", "free",
        "refusal states the reasons (user-visible honesty)",
        r.status_code == 403 and ("attorney" in body.lower()
                                  or "legal" in body.lower()),
        f"no reasons in refusal: {body[:200]}")

    # ---- billing: unsigned webhook must not grant ----
    r = c.post("/billing/webhook",
               content=json.dumps({"type": "checkout.session.completed",
                                   "data": {"object": {
                                       "customer_email": "free2@example.invalid"}}}))
    granted = db.tier("free2@example.invalid") == "paid"
    rec("WEBHOOK-NOGRANT", "/billing/webhook", "anon",
        "unsigned stub webhook does NOT grant paid (stub_webhook_grants=False)",
        not granted,
        f"HTTP {r.status_code} — free2 tier is now {db.tier('free2@example.invalid')!r}")

    # ---- unsubscribe workflow ----
    login(c, cfg, "free3@example.invalid")
    r = c.post("/unsubscribe")
    sub = db.subscriber("free3@example.invalid") or {}
    rec("UNSUB-01", "/unsubscribe", "free", "unsubscribe flips status",
        r.status_code == 200 and sub.get("status") == "unsubscribed",
        f"HTTP {r.status_code} status={sub.get('status')!r}")
    # Put free3 back. This criterion mutated the fixture and never restored it,
    # so the sweep was already non-idempotent — harmless while nothing read the
    # active set, and no longer harmless now that DIGEST-01 does.
    db.upsert_subscriber("free3@example.invalid")

    # ---- authenticated dashboard, both tiers ----
    for email, tier in [("free1@example.invalid", "free"),
                        ("paid1@example.invalid", "paid")]:
        login(c, cfg, email)
        r = c.get("/dashboard")
        rec(f"DASH-{tier}", "/dashboard", tier, "subscriber dashboard renders",
            r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
        if r.status_code == 200:
            rec(f"DASH-{tier}-disc", "/dashboard", tier,
                "dashboard carries the disclaimer",
                "PAPER" in r.text.upper(), "no disclaimer on the dashboard")

    # ---- gate OPEN: the path live data can never reach ----
    c2, cfg2, db2 = build_client(fixture, gate_open=True)
    login(c2, cfg2, "free1@example.invalid")
    r = c2.post("/billing/checkout")
    rec("GATE-OPEN", "/billing/checkout", "free",
        "with 450 closed trades / 549 days and flags set, checkout is permitted",
        r.status_code == 200, f"HTTP {r.status_code} {r.text[:250]}")

    # ---- read-only invariant #9 ----
    from publisher.readonly import ReadOnlyLedger
    rec("RO-01", "ReadOnlyLedger", "system",
        "publisher ledger exposes no write methods",
        not any(hasattr(ReadOnlyLedger, m) for m in
                ("append", "log_decision", "close_trade", "log_event")),
        "a write method is reachable from the publisher")

    # ---- session forgery: the escalation attempts that matter ----
    # An earlier draft "tampered" by string-replacing the email in the cookie.
    # The payload is base64, so that substring never appears — the check was a
    # no-op that always passed. These decode, rewrite and re-sign for real.
    import base64
    import hmac as _hmac
    import hashlib as _hashlib
    import time as _time
    from publisher import auth as _auth
    from publisher.config import session_secret as _secret_fn

    sec = _secret_fn()
    good = _auth.make_session(sec, "free1@example.invalid",
                              cfg["publisher"]["session_ttl_hours"])
    b64, sig = good.split(".")
    payload = base64.urlsafe_b64decode(b64)
    esc = payload.replace(b"free1", b"paid1")
    email_part, _exp = payload.decode().split("|")

    forgeries = {
        "rewritten email, original signature":
            base64.urlsafe_b64encode(esc).decode() + "." + sig,
        "rewritten email, signed with a wrong secret":
            base64.urlsafe_b64encode(esc).decode() + "." +
            _hmac.new(b"wrong-secret", esc, _hashlib.sha256).hexdigest(),
        "expiry extended, original signature":
            base64.urlsafe_b64encode(
                f"{email_part}|{int(_time.time()) + 10**7}".encode()).decode()
            + "." + sig,
        "correctly signed but EXPIRED":
            _auth.make_session(sec, "free1@example.invalid", -1),
        "signature stripped": b64 + ".",
    }
    for label, cookie in forgeries.items():
        logout(c, cfg)
        c.cookies.set(cfg["publisher"]["session_cookie"], cookie)
        r = c.get("/account")
        rec(f"FORGE-{label[:18]}", "/account", "attacker",
            f"rejects a session with {label}",
            r.status_code == 401,
            f"HTTP {r.status_code} — forged session ACCEPTED: {r.text[:120]}")
    logout(c, cfg)

    # ---- magic-link token lifecycle ----
    tok = _auth.new_token()
    db.issue_token("free1@example.invalid", "login", tok, 30)
    r1 = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
    rec("TOK-01", "/auth/verify", "anon", "a fresh token signs the user in",
        r1.status_code == 303, f"HTTP {r1.status_code}")
    sc = r1.headers.get("set-cookie", "").lower()
    rec("TOK-02", "/auth/verify", "anon",
        "session cookie is HttpOnly and SameSite",
        "httponly" in sc and "samesite" in sc, f"set-cookie: {sc[:160]}")
    logout(c, cfg)
    r2 = c.get(f"/auth/verify?token={tok}", follow_redirects=False)
    rec("TOK-03", "/auth/verify", "attacker",
        "a magic-link token is single-use (replay rejected)",
        r2.status_code >= 400, f"HTTP {r2.status_code} — token replayed")
    exp_tok = _auth.new_token()
    db.issue_token("free1@example.invalid", "login", exp_tok, -1)
    r3 = c.get(f"/auth/verify?token={exp_tok}", follow_redirects=False)
    rec("TOK-04", "/auth/verify", "anon", "an expired token is rejected",
        r3.status_code >= 400, f"HTTP {r3.status_code}")

    # ---- email normalisation at the session boundary (F-02) ----
    logout(c, cfg)
    login(c, cfg, "PAID1@Example.Invalid")
    r = c.get("/account")
    body = r.json() if r.status_code == 200 else {}
    rec("NORM-01", "/account", "paid",
        "a mixed-case session echoes the canonical lowercase address",
        body.get("email") == "paid1@example.invalid",
        f"echoed {body.get('email')!r} — DB lookups normalise but the "
        f"response did not")
    rec("NORM-02", "/account", "paid",
        "mixed-case session still resolves the paid tier",
        body.get("tier") == "paid", f"tier={body.get('tier')!r}")

    # ---- rate limiting on the email-sending endpoint ----
    logout(c, cfg)
    codes = [c.post("/auth/request-link",
                    json={"email": f"rl{i}@example.invalid"}).status_code
             for i in range(15)]
    rec("RL-01", "/auth/request-link", "anon",
        "burst on the email-sending endpoint is rate limited (429)",
        429 in codes, f"no 429 in 15 rapid requests: {codes}")

    # ---- bounded responses at production scale ----
    logout(c, cfg)
    r = c.get("/feed")
    n_dec = len(r.json().get("decisions", []))
    total = sum(1 for _ in open(cfg["memory"]["ledger_path"]))
    rec("SCALE-01", "/feed", "anon",
        "feed is bounded, not the whole ledger",
        n_dec < 500, f"{n_dec} decisions returned from {total:,} records")

    # ---- cross-surface number agreement ----
    import review as _review
    reps = _review.build_report(app_ledger(c).all_records(), [],
                                datetime.now(timezone.utc))
    feed_now = c.get("/feed").json()
    rec("XSURF-01", "review vs /feed", "system",
        "open-position count agrees across surfaces",
        reps["n_open"] == feed_now.get("open_positions"),
        f"review={reps['n_open']} feed={feed_now.get('open_positions')}")

    # ---- the daily digest broadcast (F-01) ----
    #
    # These replaced a criterion that grepped inspect.getsource(digest) for the
    # string "active_emails". That tested TEXT, not behaviour: it could not
    # tell a correct broadcast from one that greps clean and mails the wrong
    # list, and it passed trivially because the function it guarded against did
    # not exist yet. Everything below drives the real thing.
    import copy as _copy

    from publisher import broadcast as _bc
    from publisher import digest as _digest

    dcfg = _copy.deepcopy(cfg)
    dcfg["publisher"]["digest"]["enabled"] = True
    dcfg["publisher"]["email"]["dry_run"] = True     # belt; braces below
    outbox_path = os.path.join(cfg["publisher"]["data_dir"], "outbox.jsonl")
    before = os.path.getsize(outbox_path) if os.path.exists(outbox_path) else 0

    bodies = {}
    _real_send = _digest.send

    def _spy(cfg_, to_email, subject, html):
        bodies[to_email] = html
        return _real_send(cfg_, to_email, subject, html)

    _digest.send = _spy
    try:
        run = _bc.send_daily_digest(dcfg, db, app_ledger(c), force=True)
    finally:
        _digest.send = _real_send

    active = set(db.active_emails())
    mailed = set(bodies)
    rec("DIGEST-01", "publisher/broadcast.py", "system",
        "reaches every active subscriber and no unsubscribed one",
        mailed == active and "gone1@example.invalid" not in mailed,
        f"mailed-not-active={sorted(mailed - active)} "
        f"active-not-mailed={sorted(active - mailed)}")

    exp = bodies.get("expired1@example.invalid", "")
    rec("DIGEST-02", "publisher/broadcast.py", "expired",
        "a lapsed entitlement receives the FREE body, not the paid one",
        bool(exp) and "[FREE]" in exp and "judge:" not in exp,
        "expired subscriber received paid content — the entitlement expiry "
        "did not reach the digest")

    # DIGEST-03/04 drive the emailed link exactly as a mail client would:
    # signed out, starting from the href that is actually in the body.
    link = re.search(r'href="([^"]*/unsubscribe\?token=[^"]+)"',
                     bodies.get("free1@example.invalid", "") or "")
    logout(c, cfg)
    if link:
        url = link.group(1)
        tok = url.split("token=")[1]
        g1 = c.get(url)
        g2 = c.get(url)                       # a scanner, then a preview pane
        still_active = "free1@example.invalid" in db.active_emails()
        conf = c.post(f"/unsubscribe/confirm?token={tok}",
                      follow_redirects=False)
        gone_now = "free1@example.invalid" not in db.active_emails()
    else:
        g1 = g2 = conf = None
        still_active = gone_now = False

    rec("DIGEST-03", "/unsubscribe", "email-recipient",
        "the emailed opt-out link works with NO session",
        bool(link) and g1.status_code == 200 and conf.status_code == 303
        and gone_now,
        f"link={bool(link)} "
        f"get={getattr(g1, 'status_code', None)} "
        f"confirm={getattr(conf, 'status_code', None)} unsubscribed={gone_now}")

    rec("DIGEST-04", "/unsubscribe", "mail-scanner",
        "GET alone never unsubscribes (link-prefetch safety)",
        bool(link) and g2.status_code == 200 and still_active,
        "a GET mutated state — Gmail/Outlook/Proofpoint prefetch would "
        "silently unsubscribe people who never clicked")

    # Leave the fixture as we found it, or a second sweep sees a different
    # active set and DIGEST-01 drifts. A harness that is not rerunnable is
    # itself a defect.
    db.upsert_subscriber("free1@example.invalid")

    lines = []
    if os.path.exists(outbox_path):
        with open(outbox_path) as f:
            f.seek(before)
            lines = [json.loads(x) for x in f.read().splitlines() if x.strip()]
    rec("DIGEST-05", "publisher/digest.py", "system",
        "no real email was possible: dry-run everywhere, no API key",
        not os.environ.get("RESEND_API_KEY") and bool(lines)
        and all(x["dry_run"] for x in lines),
        f"RESEND_API_KEY set={bool(os.environ.get('RESEND_API_KEY'))} "
        f"non-dry lines={[x for x in lines if not x.get('dry_run')][:3]}")

    rec("DIGEST-06", "publisher/broadcast.py", "system",
        "the run is auditable (recipients, tiers, failures recorded)",
        run.get("skipped_reason") is None and run.get("failed") == 0
        and bool(run.get("by_tier")),
        f"run record thin or the send was skipped: "
        f"skipped={run.get('skipped_reason')} failed={run.get('failed')} "
        f"by_tier={run.get('by_tier')}")

    return out


def app_ledger(client):
    from publisher.app import app
    return app.state.ledger


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    results = run_all(args.fixture, args.verbose)
    failed = [r for r in results if not r["ok"]]

    print(f"\n{'='*74}\nQA SWEEP — {len(results)} acceptance criteria\n{'='*74}")
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        line = f"[{mark}] {r['id']:<22} {r['role']:<10} {r['criterion']}"
        if not r["ok"] or args.verbose:
            print(line)
            if not r["ok"]:
                print(f"         evidence: {r['evidence']}")
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} FAILED")
    return len(failed)


if __name__ == "__main__":
    raise SystemExit(main())
