"""Passwordless auth: magic links + signed session cookies. Stdlib only.

No passwords are ever stored or accepted (there is nothing to breach).
Login: we email a single-use token (DB stores its hash); verifying it sets
an HMAC-signed session cookie email|expiry|signature. Tampering with any
part invalidates the signature.
"""
import base64
import hmac
import hashlib
import secrets
import time


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _sign(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def make_session(secret: bytes, email: str, ttl_hours: int) -> str:
    exp = int(time.time()) + ttl_hours * 3600
    payload = f"{email}|{exp}".encode()
    return (base64.urlsafe_b64encode(payload).decode()
            + "." + _sign(secret, payload))


def verify_session(secret: bytes, cookie: str | None) -> str | None:
    """Email for a valid, unexpired, untampered session; else None."""
    if not cookie or "." not in cookie:
        return None
    b64, sig = cookie.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b64.encode())
    except Exception:  # noqa: BLE001 — any malformed cookie is just invalid
        return None
    if not hmac.compare_digest(_sign(secret, payload), sig):
        return None
    try:
        email, exp = payload.decode().rsplit("|", 1)
        if int(exp) < time.time():
            return None
        return email
    except ValueError:
        return None
