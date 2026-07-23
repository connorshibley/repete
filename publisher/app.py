"""The publisher web service (FastAPI).

    PUBLISHER_SESSION_SECRET=… uvicorn publisher.app:app --port 8080

Read-only against agent state (invariant #9); checkout refused while the
revenue gate is closed (invariant #10). Session = signed cookie; login =
emailed magic link; billing = Stripe (stub mode without keys).
"""
import os
import sys

from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from publisher import auth, billing, config, content, digest, gates, legal
from publisher.ratelimit import Limiter
from publisher.readonly import ReadOnlyLedger, agent_paths
from publisher.subscribers import SubscriberDB

app = FastAPI(title="Repete — publisher", docs_url=None, redoc_url=None)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """Per-IP token buckets (Phase D). 429 before any handler runs."""
    if not hasattr(request.app.state, "limiter"):
        request.app.state.limiter = Limiter(
            _cfg(request)["publisher"].get("rate_limit") or {})
    ip = request.client.host if request.client else "unknown"
    if not request.app.state.limiter.check(request.url.path, ip):
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
    cookie = request.cookies.get(_cfg(request)["publisher"]["session_cookie"])
    return auth.verify_session(_secret(request), cookie)


def require_session(request: Request) -> str:
    email = session_email(request)
    if not email:
        raise HTTPException(401, "sign in via magic link first")
    return email


# ---- public pages ----

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cfg, ledger = _cfg(request), _ledger(request)
    open_, reasons = gates.revenue_gate(cfg, ledger)
    gate_html = ("<p><b>Paid subscriptions are OPEN.</b></p>" if open_ else
                 "<p><b>Paid subscriptions are not open yet.</b> The gates "
                 "we hold ourselves to, honestly unmet:</p><ul>"
                 + "".join(f"<li>{r}</li>" for r in reasons) + "</ul>")
    return (f"<h1>Repete — the paper-trading robot, in public</h1>"
            f"{gate_html}"
            f"<p><a href='/feed'>free feed</a> · "
            f"<a href='/legal/risk'>risk disclosure</a></p>"
            f"<hr><p style='font-size:12px'>{gates.DISCLAIMER}</p>")


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
                    max_age=cfg["publisher"]["session_ttl_hours"] * 3600)
    return resp


# ---- subscriber surface ----

@app.get("/account")
def account(request: Request, email: str = Depends(require_session)):
    db = _db(request)
    return {"email": email, "tier": db.tier(email),
            "status": (db.subscriber(email) or {}).get("status")}


@app.get("/feed")
def feed(request: Request):
    cfg, db, ledger = _cfg(request), _db(request), _ledger(request)
    email = session_email(request)
    tier = db.tier(email) if email else "free"
    return content.feed(cfg, ledger, tier)


@app.post("/unsubscribe")
def unsubscribe(request: Request, email: str = Depends(require_session)):
    _db(request).unsubscribe(email)
    return {"ok": True, "unsubscribed": email}


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
                                 request.headers.get("stripe-signature"))
    if not out["ok"]:
        raise HTTPException(400, detail=out)
    return out
