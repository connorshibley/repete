"""Revenue gates (CLAUDE.md invariant #10) — enforced in code, not promised.

Checkout stays refused until EVERY gate passes, exactly like the trading
gates: pre-registered thresholds, checked automatically, reasons published.
"""
from datetime import datetime, timezone


DISCLAIMER = (
    "Repete is a PAPER-TRADING experiment. All fills are simulated; no real "
    "money is traded. Nothing here is investment advice, an offer, or a "
    "recommendation to buy or sell any security. Content is impersonal and "
    "published on a regular schedule to all subscribers alike. Past "
    "performance — simulated or otherwise — does not indicate future "
    "results. Do your own research; consult a licensed advisor before "
    "investing.")


def history_days(records: list[dict]) -> int:
    ts = [r.get("ts") for r in records if r.get("ts")]
    if not ts:
        return 0
    first = datetime.fromisoformat(min(ts))
    return (datetime.now(timezone.utc) - first).days


def revenue_gate(cfg: dict, ledger) -> tuple[bool, list[str]]:
    """(open, reasons-it-is-closed). Every reason is user-visible honesty."""
    pub = cfg["publisher"]
    rev = pub["revenue"]
    reasons: list[str] = []

    n_closed = len(ledger.closed_trades())
    if n_closed < rev["min_closed_trades"]:
        reasons.append(f"track record too small: {n_closed} closed trades "
                       f"(gate: {rev['min_closed_trades']})")

    days = history_days(ledger.all_records())
    if days < rev["min_history_days"]:
        reasons.append(f"history too short: {days} days "
                       f"(gate: {rev['min_history_days']})")

    if not pub.get("attorney_signoff"):
        reasons.append("securities attorney has not signed off on the "
                       "publication model and marketing copy")

    if not pub.get("legal_pages_final"):
        reasons.append("legal pages (ToS / Privacy / Risk) are still drafts")

    return (not reasons), reasons
