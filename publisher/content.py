"""Tiered content, rendered from READ-ONLY stores.

  free — the honest overview: equity, monthly scorecard, position COUNT,
         and decisions DELAYED by publisher.free_delay_days with the
         judge's reasoning withheld.
  paid — same-day decisions with the judge's full reasoning, the bull/bear
         debate, stated confidence, and journal entries.

The tier difference is timeliness + depth. Both tiers carry the disclaimer,
both see the same honest numbers — the free tier is never shown a rosier
picture, only a smaller one.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import scorecard  # noqa: E402
from publisher.gates import DISCLAIMER  # noqa: E402
from publisher.readonly import read_stream, agent_paths  # noqa: E402


def _equity_summary(records: list[dict]) -> dict:
    import json
    eq = None
    for r in records:
        if r.get("type") == "event" and r.get("event") == "cycle_complete":
            try:
                eq = float(json.loads(r.get("detail") or "")["equity"])
            except (ValueError, TypeError, KeyError):
                continue
    return {"equity": eq}


def _decision_view(r: dict, include_reasoning: bool) -> dict:
    out = {
        "ts": r.get("ts"),
        "symbol": r.get("symbol"),
        "action": r.get("action"),
        "strategy": r.get("strategy"),
        "executed": bool(r.get("executed")),
        "detail": r.get("detail") or "",
        "regime": r.get("regime"),
    }
    if include_reasoning:
        rev = r.get("llm_review") or {}
        out["judge"] = {
            "verdict": rev.get("verdict"),
            "scale": rev.get("scale"),
            "confidence": rev.get("confidence"),
            "reasoning": rev.get("reasoning"),
            "bull_case": rev.get("bull_case"),
            "bear_case": rev.get("bear_case"),
        }
        out["strategy_reason"] = r.get("strategy_reason")
    return out


def feed(cfg: dict, ledger, tier: str,
         now: datetime | None = None) -> dict:
    """The subscriber feed object for a tier. Pure read."""
    now = now or datetime.now(timezone.utc)
    records = ledger.all_records()
    paid = tier == "paid"

    cutoff = (now - timedelta(days=cfg["publisher"]["free_delay_days"])
              ) if not paid else now
    decisions = [r for r in records if r.get("type") == "decision"
                 and (r.get("executed") or "risk rejection" in
                      (r.get("detail") or "") or (r.get("llm_review") or {}
                      ).get("verdict") == "veto")]
    visible = [d for d in decisions
               if datetime.fromisoformat(d["ts"]) <= cutoff]

    card = scorecard.monthly_scorecard(
        records, [], scorecard.realized_pnl_by_month(records))

    out = {
        "tier": tier,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "equity": _equity_summary(records),
        "open_positions": len(ledger.open_buys()),
        "closed_trades": len(ledger.closed_trades()),
        "monthly_scorecard": card,
        "decisions": [_decision_view(d, include_reasoning=paid)
                      for d in visible[-40:]],
        "delayed_by_days": 0 if paid else
        cfg["publisher"]["free_delay_days"],
    }
    if paid:
        journal = read_stream(agent_paths(cfg)["journal"])
        out["journal"] = [{"trade_id": e.get("trade_id"),
                           "ts": e.get("ts"), "title": e.get("title"),
                           "text": e.get("text")} for e in journal[-10:]]
    return out
