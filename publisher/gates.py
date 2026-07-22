"""Revenue gates (CLAUDE.md invariant #10) — enforced in code, not promised.

Checkout stays refused until EVERY gate passes, exactly like the trading
gates: pre-registered thresholds, checked automatically, reasons published.
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# Single source of truth (Phase C): src/disclaimer.py. Re-exported here so
# existing `from publisher.gates import DISCLAIMER` callers keep working.
from disclaimer import DISCLAIMER  # noqa: E402,F401


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
