"""The publisher web service (FastAPI).

    PUBLISHER_SESSION_SECRET=… uvicorn publisher.app:app --port 8080

Read-only against agent state (invariant #9); checkout refused while the
revenue gate is closed (invariant #10). Session = signed cookie; login =
emailed magic link; billing = Stripe (stub mode without keys).
"""
import os
import sys
import urllib.parse
from html import escape as html_escape

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from publisher import (auth, billing, config, content, dashboard_view, digest,
                       gates, legal, marketing_view, status_view)
from publisher.ratelimit import Limiter, client_key
from publisher.readonly import ReadOnlyLedger, agent_paths
from publisher.subscribers import SubscriberDB

app = FastAPI(title="Repete — publisher", docs_url=None, redoc_url=None)


def _trust_proxy(cfg: dict) -> bool:
    return bool(cfg["publisher"].get("trust_proxy"))


def _client_ip(request: Request, cfg: dict) -> str:
    host = request.client.host if request.client else None
    return client_key(host, request.headers.get("x-forwarded-for"),
                      _trust_proxy(cfg))


def _is_https(request: Request, cfg: dict) -> bool:
    """True when the effective client scheme is https, so session cookies get
    Secure. Behind a trusted proxy the app sees http; trust X-Forwarded-Proto
    only when trust_proxy is set (else it is attacker-controlled)."""
    if request.url.scheme == "https":
        return True
    if _trust_proxy(cfg):
        return request.headers.get("x-forwarded-proto", "").lower() == "https"
    return False


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Per-IP token buckets (Phase D). 429 before any handler runs."""
    cfg = _cfg(request)
    if not hasattr(request.app.state, "limiter"):
        request.app.state.limiter = Limiter(
            cfg["publisher"].get("rate_limit") or {})
    if not request.app.state.limiter.check(request.url.path,
                                           _client_ip(request, cfg)):
        return JSONResponse({"detail": "rate limited — slow down"},
                            status_code=429)
    return await call_next(request)


# ---- wiring (overridable in tests via app.state) ----

def _cfg(request: Request) -> dict:
    if not hasattr(request.app.state, "cfg"):
        request.app.state.cfg = config.load()
    return request.app.state.cfg


def _db(request: Request) -> SubscriberDB:
    if not hasattr(request.app.state, "db"):
        cfg = _cfg(request)
        request.app.state.db = SubscriberDB(
            os.path.join(cfg["publisher"]["data_dir"], "pub.db"))
    return request.app.state.db


def _ledger(request: Request) -> ReadOnlyLedger:
    if not hasattr(request.app.state, "ledger"):
        cfg = _cfg(request)
        request.app.state.ledger = ReadOnlyLedger(
            agent_paths(cfg)["ledger"], cfg)
    return request.app.state.ledger


def _secret(request: Request) -> bytes:
    if not hasattr(request.app.state, "secret"):
        request.app.state.secret = config.session_secret()
    return request.app.state.secret


def session_email(request: Request) -> str | None:
    """The signed-in email, NORMALISED the way the database stores it.

    `SubscriberDB` lowercases and strips inside every method, so tier and
    entitlement lookups were already case-insensitive — but the raw session
    string flowed straight through to responses. A session minted for
    `PAID1@Example.Invalid` resolved the correct paid tier while `/account`
    echoed back the un-normalised text, so one person could see two different
    spellings of their own identity depending how they typed it.

    Normalising once at the session boundary means every route downstream sees
    a single canonical form, rather than each remembering to normalise itself.
    """
    cookie = request.cookies.get(_cfg(request)["publisher"]["session_cookie"])
    email = auth.verify_session(_secret(request), cookie)
    return email.strip().lower() if email else None


def require_session(request: Request) -> str:
    email = session_email(request)
    if not email:
        raise HTTPException(401, "sign in via magic link first")
    return email


# ---- public pages ----

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Phase E: the marketing / landing page. Descriptive only — no
    performance claims, projections, or testimonials (PRODUCT.md)."""
    cfg, ledger = _cfg(request), _ledger(request)
    open_, reasons = gates.revenue_gate(cfg, ledger)
    return marketing_view.render(gate_open=open_, gate_reasons=reasons)


@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    """Phase E: public status page — is the bot actually running? Read-only
    against agent state (invariant #9) via health.status(read_only=True)."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    import health
    return status_view.render(health.status(_cfg(request), read_only=True))


@app.get("/healthz")
def healthz(request: Request):
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    import health
    # read_only: the publisher must never mutate the process-wide storage
    # backend or issue DDL against agent state (invariant #9).
    s = health.status(_cfg(request), read_only=True)
    return JSONResponse(s, status_code=200 if s["healthy"] else 503)


@app.get("/legal/{page}", response_class=HTMLResponse)
def legal_page(page: str):
    docs = {"tos": legal.TERMS, "privacy": legal.PRIVACY, "risk": legal.RISK}
    if page not in docs:
        raise HTTPException(404)
    return docs[page]


# ---- auth ----

@app.post("/auth/request-link")
async def request_link(request: Request):
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    if "@" not in email or len(email) > 254:
        raise HTTPException(400, "valid email required")
    cfg, db = _cfg(request), _db(request)
    db.upsert_subscriber(email)
    token = auth.new_token()
    db.issue_token(email, "login", token,
                   cfg["publisher"]["magic_link_ttl_minutes"])
    link = f"{cfg['publisher']['base_url']}/auth/verify?token={token}"
    digest.send(cfg, email, "Your Repete sign-in link",
                f"<p><a href='{link}'>Sign in to Repete</a> — this link "
                f"works once and expires in "
                f"{cfg['publisher']['magic_link_ttl_minutes']} minutes.</p>")
    # The raw token never appears in the response or any log — only the email.
    return {"ok": True, "sent_to": email}


@app.get("/auth/verify")
def verify(request: Request, token: str):
    cfg, db = _cfg(request), _db(request)
    email = db.consume_token(token, "login")
    if not email:
        raise HTTPException(400, "link invalid, used, or expired — "
                            "request a new one")
    cookie = auth.make_session(_secret(request), email,
                               cfg["publisher"]["session_ttl_hours"])
    resp = RedirectResponse("/account", status_code=303)
    resp.set_cookie(cfg["publisher"]["session_cookie"], cookie,
                    httponly=True, samesite="lax",
                    secure=_is_https(request, cfg),
                    max_age=cfg["publisher"]["session_ttl_hours"] * 3600)
    return resp


# ---- subscriber surface ----

@app.get("/account")
def account(request: Request, email: str = Depends(require_session)):
    db = _db(request)
    return {"email": email, "tier": db.tier(email),
            "status": (db.subscriber(email) or {}).get("status")}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, email: str = Depends(require_session)):
    """Phase E: the authenticated subscriber dashboard. Tier-aware and
    read-only against agent state (invariant #9)."""
    cfg, db, ledger = _cfg(request), _db(request), _ledger(request)
    open_, reasons = gates.revenue_gate(cfg, ledger)
    return dashboard_view.render(cfg, ledger, db.tier(email), email,
                                 gate_open=open_, gate_reasons=reasons)


@app.get("/feed")
def feed(request: Request):
    cfg, db, ledger = _cfg(request), _db(request), _ledger(request)
    email = session_email(request)
    tier = db.tier(email) if email else "free"
    return content.feed(cfg, ledger, tier)


@app.post("/unsubscribe")
def unsubscribe(request: Request, email: str = Depends(require_session)):
    """Session-based opt-out, driven by the signed-in account UI.

    Unchanged. The token routes below serve a DIFFERENT caller — someone
    clicking a link in an email, who has no session — and the two coexist:
    GET /unsubscribe and POST /unsubscribe are separate operations.
    """
    _db(request).unsubscribe(email)
    return {"ok": True, "unsubscribed": email}


# ---- unsubscribe from an emailed link (no session) ----
#
# Rate limiting: these deliberately get NO dedicated bucket and fall through to
# the global one (60/min per IP). They are capability-gated by a 256-bit token,
# so brute force is not the threat model, and the false positive of a tight
# limit is a 429 served to someone actively trying to leave a mailing list —
# which converts straight into a spam complaint. A control whose failure mode
# is a compliance incident is worse than no control.

_UNSUB_CSS = ("font:15px/1.5 -apple-system,system-ui,sans-serif;"
              "max-width:34rem;margin:4rem auto;padding:0 1rem")


def _unsub_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><meta name="viewport" content="width=device-width,'
        f'initial-scale=1"><title>{title} — Repete</title>'
        f'<div style="{_UNSUB_CSS}"><h2>{title}</h2>{body}</div>',
        status_code=status)


def _bad_token_page() -> HTMLResponse:
    """One response for invalid / expired / already-used.

    Deliberately does not distinguish them: three different messages would let
    an attacker with a guessed token learn whether an address exists.
    """
    return _unsub_page(
        "This link no longer works",
        "<p>It may have expired, or already been used.</p>"
        "<p>You can manage your subscription by signing in with a new "
        "magic link.</p>", status=400)


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(request: Request, token: str = ""):
    """Confirmation page for an emailed opt-out link. MUTATES NOTHING.

    This is a GET reached from a mail client, so it must be safe to fetch
    repeatedly by something that is not the subscriber. Gmail's link proxy,
    Outlook Safe Links and Proofpoint URL Defense all issue unauthenticated
    GETs against links in mail. If this handler unsubscribed, a corporate
    security scanner would silently opt people out of a list they wanted, and
    neither side would ever find out — the failure is invisible in both
    directions.

    So: peek_token (no UPDATE), render a form, and let the POST do the work.
    Note this takes NO session dependency — the recipient is not signed in,
    which is the whole point.
    """
    email = _db(request).peek_token(token, "unsubscribe") if token else None
    if not email:
        return _bad_token_page()
    safe = html_escape(email)
    return _unsub_page(
        "Unsubscribe",
        f"<p>Stop sending the daily digest to <b>{safe}</b>?</p>"
        f'<form method="post" action="/unsubscribe/confirm'
        f'?token={urllib.parse.quote(token, safe="")}">'
        f'<button type="submit">Unsubscribe me</button></form>')


@app.post("/unsubscribe/confirm")
def unsubscribe_confirm(request: Request, token: str = ""):
    """Burn the token and opt the address out. The only mutating step.

    The token rides the QUERY STRING rather than a form field because
    `Form(...)` needs python-multipart, which the publisher does not install.
    /auth/verify?token= already sets that precedent for a burn-on-use
    capability.

    Redirects to a TOKENLESS page so a browser reload cannot re-POST a
    now-spent token and show the user a failure for succeeding.
    """
    email = _db(request).consume_token(token, "unsubscribe") if token else None
    if not email:
        return _bad_token_page()
    _db(request).unsubscribe(email)
    return RedirectResponse("/unsubscribe/done", status_code=303)


@app.get("/unsubscribe/done", response_class=HTMLResponse)
def unsubscribe_done():
    return _unsub_page(
        "Unsubscribed",
        "<p>You will not receive further digests.</p>"
        "<p>Changed your mind? Request a magic link to resubscribe.</p>")


# ---- billing ----

@app.post("/billing/checkout")
def checkout(request: Request, email: str = Depends(require_session)):
    cfg, db, ledger = _cfg(request), _db(request), _ledger(request)
    out = billing.create_checkout(cfg, ledger, db, email)
    if not out["ok"]:
        # Invariant #10: no money before the gates — refused with reasons.
        raise HTTPException(403, detail=out)
    return out


@app.post("/billing/webhook")
async def webhook(request: Request):
    payload = await request.body()
    out = billing.handle_webhook(_cfg(request), _db(request), payload,
                                 request.headers.get("stripe-signature"),
                                 _ledger(request))
    if not out["ok"]:
        raise HTTPException(400, detail=out)
    return out
