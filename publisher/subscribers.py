"""Subscriber state — the ONLY thing the publisher owns (mutable, so it is
NOT an agent event stream; it lives in its own SQLite DB under
publisher_data/, never inside memory/).

Privacy stance: we store an email, a status, entitlements, and HASHES of
one-time tokens. No passwords ever (magic-link auth), no payment details
(Stripe holds those), no tracking data.
"""
import hashlib
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

_LOCK = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
  email      TEXT PRIMARY KEY,
  created_ts TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active'   -- active | unsubscribed
);
CREATE TABLE IF NOT EXISTS entitlements (
  email      TEXT NOT NULL,
  tier       TEXT NOT NULL,                   -- paid
  source     TEXT NOT NULL,                   -- stripe:<sub_id> | manual:<note>
  granted_ts TEXT NOT NULL,
  expires_ts TEXT,                            -- NULL = until revoked
  revoked_ts TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
  token_hash TEXT PRIMARY KEY,                -- sha256; raw token never stored
  email      TEXT NOT NULL,
  purpose    TEXT NOT NULL,                   -- login | unsubscribe
  expires_ts TEXT NOT NULL,
  used_ts    TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class SubscriberDB:
    def __init__(self, path: str):
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with _LOCK:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    # ---- subscribers ----

    def upsert_subscriber(self, email: str):
        email = email.strip().lower()
        with _LOCK:
            self._conn.execute(
                "INSERT INTO subscribers (email, created_ts) VALUES (?, ?) "
                "ON CONFLICT(email) DO UPDATE SET status='active'",
                (email, _now().isoformat()))
            self._conn.commit()

    def unsubscribe(self, email: str):
        with _LOCK:
            self._conn.execute(
                "UPDATE subscribers SET status='unsubscribed' WHERE email=?",
                (email.strip().lower(),))
            self._conn.commit()

    def subscriber(self, email: str) -> dict | None:
        with _LOCK:
            r = self._conn.execute("SELECT * FROM subscribers WHERE email=?",
                                   (email.strip().lower(),)).fetchone()
        return dict(r) if r else None

    def active_emails(self) -> list[str]:
        with _LOCK:
            rows = self._conn.execute(
                "SELECT email FROM subscribers WHERE status='active' "
                "ORDER BY email").fetchall()
        return [r["email"] for r in rows]

    # ---- entitlements ----

    def grant(self, email: str, tier: str, source: str,
              expires: datetime | None = None):
        with _LOCK:
            self._conn.execute(
                "INSERT INTO entitlements (email, tier, source, granted_ts, "
                "expires_ts) VALUES (?, ?, ?, ?, ?)",
                (email.strip().lower(), tier, source, _now().isoformat(),
                 expires.isoformat() if expires else None))
            self._conn.commit()

    def revoke(self, email: str, tier: str):
        with _LOCK:
            self._conn.execute(
                "UPDATE entitlements SET revoked_ts=? "
                "WHERE email=? AND tier=? AND revoked_ts IS NULL",
                (_now().isoformat(), email.strip().lower(), tier))
            self._conn.commit()

    def tier(self, email: str) -> str:
        """'paid' when a live entitlement exists, else 'free'."""
        now = _now().isoformat()
        with _LOCK:
            r = self._conn.execute(
                "SELECT 1 FROM entitlements WHERE email=? AND tier='paid' "
                "AND revoked_ts IS NULL "
                "AND (expires_ts IS NULL OR expires_ts > ?) LIMIT 1",
                (email.strip().lower(), now)).fetchone()
        return "paid" if r else "free"

    # ---- one-time tokens (hash stored, never the token) ----

    def issue_token(self, email: str, purpose: str, raw_token: str,
                    ttl_minutes: int):
        with _LOCK:
            self._conn.execute(
                "INSERT INTO tokens (token_hash, email, purpose, expires_ts) "
                "VALUES (?, ?, ?, ?)",
                (token_hash(raw_token), email.strip().lower(), purpose,
                 (_now() + timedelta(minutes=ttl_minutes)).isoformat()))
            self._conn.commit()

    def peek_token(self, raw_token: str, purpose: str) -> str | None:
        """Email for a valid unused token WITHOUT burning it. None otherwise.

        Exists for `GET /unsubscribe`, which must render a confirmation page
        without consuming anything. Mail clients and security scanners (Gmail's
        proxy, Outlook Safe Links, Proofpoint) prefetch links in email; if that
        GET consumed the token, the scanner would destroy it and the human's
        real click would then fail with "link already used".

        Read-only by construction — no UPDATE — so the route cannot mutate
        state even if a future edit forgets that it must not.
        """
        with _LOCK:
            r = self._conn.execute(
                "SELECT email FROM tokens WHERE token_hash=? AND purpose=? "
                "AND used_ts IS NULL AND expires_ts > ?",
                (token_hash(raw_token), purpose, _now().isoformat())).fetchone()
        return r["email"] if r else None

    def consume_token(self, raw_token: str, purpose: str) -> str | None:
        """Email for a valid unused token — and burn it. None otherwise."""
        h = token_hash(raw_token)
        now = _now().isoformat()
        with _LOCK:
            r = self._conn.execute(
                "SELECT email FROM tokens WHERE token_hash=? AND purpose=? "
                "AND used_ts IS NULL AND expires_ts > ?",
                (h, purpose, now)).fetchone()
            if not r:
                return None
            self._conn.execute("UPDATE tokens SET used_ts=? WHERE token_hash=?",
                               (now, h))
            self._conn.commit()
        return r["email"]
