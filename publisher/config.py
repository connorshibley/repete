"""Publisher configuration: config.yaml `publisher:` block + env secrets.

Secrets NEVER live in config.yaml (project invariant #8):
  PUBLISHER_SESSION_SECRET  — cookie/token signing key (required to serve)
  RESEND_API_KEY            — real email sending (absent => dry-run outbox)
  STRIPE_SECRET_KEY         — real billing (absent => stub mode)
  STRIPE_WEBHOOK_SECRET     — webhook signature verification
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

DEFAULTS = {
    "enabled": True,
    "base_url": "http://localhost:8080",
    "session_cookie": "repete_session",
    "session_ttl_hours": 24 * 30,
    "magic_link_ttl_minutes": 30,
    "data_dir": "publisher_data",
    "attorney_signoff": False,       # flipped only after counsel review
    "legal_pages_final": False,      # flipped when ToS/Privacy/Risk are final
    "revenue": {
        "min_closed_trades": 30,
        "min_history_days": 90,
    },
    "email": {"dry_run": True, "from": "repete@localhost"},
    "billing": {"paid_price_usd_month": 15},
    "free_delay_days": 1,            # free tier sees decisions delayed
}


def load(root: str | None = None) -> dict:
    """Agent config + publisher block with defaults deep-merged."""
    root = root or os.getcwd()
    with open(os.path.join(root, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    pub = cfg.get("publisher") or {}
    merged = _merge(DEFAULTS, pub)
    cfg["publisher"] = merged
    return cfg


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = (_merge(base[k], v)
                  if isinstance(v, dict) and isinstance(base.get(k), dict)
                  else v)
    return out


def session_secret() -> bytes:
    """Signing key from env. A missing secret is a hard failure at serve
    time (never a silent default key), but tests inject their own."""
    s = os.environ.get("PUBLISHER_SESSION_SECRET")
    if not s:
        raise RuntimeError(
            "PUBLISHER_SESSION_SECRET missing — refusing to sign sessions "
            "with a default key. Generate one: python -c "
            "\"import secrets; print(secrets.token_hex(32))\"")
    return s.encode()
