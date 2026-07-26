"""Refresh what the open book is worth. MARKS ONLY — never trades.

The gap this closes
-------------------
The dashboard has shown per-position value and unrealized ±% since 2026-07-25,
and it reads them from the most recent `positions_mark` event in the ledger.
But only two things write that event: the end of a trading cycle
(`main.py:1057`) and the daily post job (`daily_posts.py:249`). The scheduler
runs those weekdays at 09:35, 12:00, 15:45 and 16:20 ET.

So between the open cycle and the 15:45 cycle the dashboard showed **morning
prices for six hours**, and over a weekend it showed Friday's close — while
presenting them in a table headed "Open positions" with no reason for a reader
to suspect the numbers had stopped moving. Measured 2026-07-26: the live mark
was 20.9 hours old.

The values were never wrong. They were just old, and old numbers that look
current are their own kind of wrong.

Why it cannot trade
-------------------
It needs the broker — you cannot value a book without reading it — so unlike
`opportunity_scan` it cannot promise safety by refusing the import. Instead:
it calls exactly one broker method, `positions()`, which is a GET; it holds no
order-submitting call path; and a test asserts the module contains none of the
order verbs. If that test ever goes red, this file has grown a capability it
was never meant to have.

Relationship to the invariants
------------------------------
Invariant #4 says positions and equity for DECISIONS are always read fresh from
the broker. This module makes no decisions. What it writes is display state,
consumed only by the dashboard, and nothing reads a mark back into a trading
path — the same standing this data has had since `main.py` first wrote it.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

log = logging.getLogger(__name__)


def refresh(cfg: dict, broker, ledger) -> dict:
    """Read the open book and append one positions_mark. Returns a summary.

    Never raises on a broker hiccup: a cosmetic snapshot must not be able to
    take down a scheduled job, exactly as `main.py` treats its own mark call.
    """
    try:
        positions = broker.positions()
    except Exception as e:                       # noqa: BLE001
        log.warning("positions read failed: %s", e)
        return {"ok": False, "n": 0, "reason": str(e)}

    if not positions:
        # An empty book is a real state, not a failure — but writing an empty
        # mark would make the dashboard's "valued at" stamp fresh while the
        # table it heads has nothing in it. Say nothing instead.
        return {"ok": True, "n": 0, "reason": "book is flat — nothing to mark"}

    try:
        ledger.log_positions_mark(positions)
    except Exception as e:                       # noqa: BLE001
        log.warning("positions mark failed: %s", e)
        return {"ok": False, "n": len(positions), "reason": str(e)}

    value = sum(p.get("market_value") or 0 for p in positions.values())
    upl = sum(p.get("unrealized_pl") or 0 for p in positions.values())
    return {"ok": True, "n": len(positions), "value": value, "unrealized": upl,
            "symbols": sorted(positions)}


def main() -> int:
    import yaml

    from broker import Broker
    from ledger import Ledger

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    out = refresh(cfg, Broker(cfg), Ledger(cfg["memory"]["ledger_path"]))
    if not out["ok"]:
        log.warning("mark refresh: %s", out.get("reason"))
        return 0                     # never fail a scheduled job over a mark
    if out["n"] == 0:
        log.info("mark refresh: %s", out.get("reason"))
        return 0
    log.info("marked %d position%s — value $%s, unrealized $%s",
             out["n"], "s" if out["n"] != 1 else "",
             f"{out['value']:,.2f}", f"{out['unrealized']:+,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
