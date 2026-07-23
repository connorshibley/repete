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

# Invariant #10 numeric thresholds are HARDCODED constants, not config knobs —
# a casual config edit must not be able to lower the bar for collecting money.
# (Mirrors the trading go-live gate's hardcoded review.GATE_MIN_* constants;
# the attorney_signoff / legal_pages_final attestations stay config booleans.)
MIN_CLOSED_TRADES = 30
MIN_HISTORY_DAYS = 90


def history_days(records: list[dict]) -> int:
    ts = [r.get("ts") for r in records if r.get("ts")]
    if not ts:
        return 0
    first = datetime.fromisoformat(min(ts))
    return (datetime.now(timezone.utc) - first).days


def revenue_gate(cfg: dict, ledger) -> tuple[bool, list[str]]:
    """(open, reasons-it-is-closed). Every reason is user-visible honesty."""
    pub = cfg["publisher"]
    reasons: list[str] = []

    n_closed = len(ledger.closed_trades())
    if n_closed < MIN_CLOSED_TRADES:
        reasons.append(f"track record too small: {n_closed} closed trades "
                       f"(gate: {MIN_CLOSED_TRADES})")

    days = history_days(ledger.all_records())
    if days < MIN_HISTORY_DAYS:
        reasons.append(f"history too short: {days} days "
                       f"(gate: {MIN_HISTORY_DAYS})")

    if not pub.get("attorney_signoff"):
        reasons.append("securities attorney has not signed off on the "
                       "publication model and marketing copy")

    if not pub.get("legal_pages_final"):
        reasons.append("legal pages (ToS / Privacy / Risk) are still drafts")

    return (not reasons), reasons
