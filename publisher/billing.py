"""Billing: Stripe when keys exist, a fully-testable stub when they don't.

The revenue gate (publisher/gates.py) is checked at the CHECKOUT boundary —
billing code refuses to create a session while any gate is closed, so "no
money before the gates" is enforced where money would enter, not in a doc.

Stub mode (no STRIPE_SECRET_KEY): checkout returns a fake session and the
webhook accepts unsigned JSON — enough to test the entitlement plumbing
end-to-end offline. With keys: real sessions and REQUIRED signature
verification on webhooks.
"""
import json
import os
from datetime import datetime, timezone


def stripe_mode() -> str:
    return "live" if os.environ.get("STRIPE_SECRET_KEY") else "stub"


def create_checkout(cfg: dict, ledger, db, email: str) -> dict:
    """Create a checkout session — ONLY if the revenue gate is open."""
    from publisher.gates import revenue_gate
    open_, reasons = revenue_gate(cfg, ledger)
    if not open_:
        return {"ok": False, "error": "revenue_gate_closed",
                "reasons": reasons}

    if stripe_mode() == "stub":
        session_id = f"stub_cs_{email.replace('@', '_at_')}"
        return {"ok": True, "mode": "stub", "session_id": session_id,
                "checkout_url": f"/billing/stub-complete?session={session_id}"}

    import stripe  # lazy: only needed with real keys
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    pub = cfg["publisher"]
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=email,
        line_items=[{"quantity": 1, "price_data": {
            "currency": "usd",
            "recurring": {"interval": "month"},
            "unit_amount": int(pub["billing"]["paid_price_usd_month"] * 100),
            "product_data": {"name": "Repete — paid publication"}}}],
        success_url=f"{pub['base_url']}/account?upgraded=1",
        cancel_url=f"{pub['base_url']}/account")
    return {"ok": True, "mode": "live", "session_id": session.id,
            "checkout_url": session.url}


def handle_webhook(cfg: dict, db, payload: bytes,
                   sig_header: str | None) -> dict:
    """Grant/revoke entitlements from billing events. In live mode the
    signature is verified or the event is rejected — no exceptions."""
    if stripe_mode() == "live":
        import stripe
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header or "",
                os.environ["STRIPE_WEBHOOK_SECRET"])
            data = event["data"]["object"]
            etype = event["type"]
        except Exception:  # noqa: BLE001 — bad signature = rejected, logged
            return {"ok": False, "error": "bad_signature"}
    else:
        try:
            event = json.loads(payload)
            etype = event.get("type", "")
            data = event.get("data", {}).get("object", {})
        except ValueError:
            return {"ok": False, "error": "bad_payload"}

    email = (data.get("customer_email")
             or data.get("customer_details", {}).get("email") or "").lower()
    if not email:
        return {"ok": False, "error": "no_email"}

    if etype in ("checkout.session.completed",
                 "invoice.payment_succeeded"):
        db.upsert_subscriber(email)
        db.grant(email, "paid",
                 source=f"stripe:{data.get('subscription') or data.get('id')}")
        return {"ok": True, "granted": email}
    if etype in ("customer.subscription.deleted",
                 "invoice.payment_failed"):
        db.revoke(email, "paid")
        return {"ok": True, "revoked": email}
    return {"ok": True, "ignored": etype,
            "at": datetime.now(timezone.utc).isoformat()}
