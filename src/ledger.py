"""Trade ledger — the audit trail. Every decision (including skips and
rejections) is appended as one JSON line with full reasoning.

This is the 'enterprise' documentation layer: append-only JSONL, one
record per decision, machine-parseable and human-readable. Outcomes are
written only AFTER a position closes (the 'outcome embargo' from the
research — memory must never see a result before it was observable).
"""
import json
import os
from datetime import datetime, timezone

import uuid

import store as store_mod


class Ledger:
    def __init__(self, path: str):
        self.path = path
        self.model_version: str | None = None  # set once per cycle (modelver)
        # Backend chosen once at startup (store.configure); JSONL by default.
        self._store = store_mod.open_store(path)

    def set_model_version(self, version: str | None):
        """Stamp every subsequent record with the decision-surface
        fingerprint, so the track record segments by rulebook version."""
        self.model_version = version

    def _append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        if self.model_version:
            record.setdefault("model_version", self.model_version)
        self._store.append(record)

    # ---- decision records ----

    def log_decision(self, symbol: str, action: str, reason: str,
                     indicators: dict, llm_review: dict | None,
                     executed: bool, detail: str = "", order: dict | None = None,
                     entry_price: float | None = None, qty: int | None = None,
                     regime: str | None = None, strategy: str | None = None,
                     entry_ts: str | None = None) -> str:
        trade_id = str(uuid.uuid4())[:8]
        self._append({
            "type": "decision",
            "trade_id": trade_id,
            "regime": regime,              # market regime tag (learning loop)
            "strategy": strategy,          # which strategy produced the signal
            "symbol": symbol,
            "action": action,
            "strategy_reason": reason,
            "indicators": indicators,
            "llm_review": llm_review,      # verdict + reasoning from the judgment layer
            "executed": executed,
            "detail": detail,              # e.g. risk-rejection reason
            "order": order,
            "entry_price": entry_price,
            "qty": qty,
            # The position's REAL fill time when it differs from this record's
            # write time (adopted positions). `ts` keeps its audit meaning —
            # when the record was appended — so age checks read entry_ts first.
            "entry_ts": entry_ts,
            "outcome": None,               # embargoed until close — see close_trade()
        })
        return trade_id

    def close_trade(self, trade_id: str, exit_price: float, pnl: float, pnl_pct: float,
                    exit_reason: str = ""):
        """Record the outcome of a closed trade as a separate event (append-only).

        exit_reason: strategy_sell | stop_loss | take_profit | closed_order |
        last_price_estimate | entry_unfilled ("" in pre-bracket records).
        """
        self._append({
            "type": "outcome",
            "trade_id": trade_id,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 3),
            "result": "win" if pnl > 0 else "loss",
            "exit_reason": exit_reason,
        })

    def log_event(self, event: str, detail: str = ""):
        self._append({"type": "event", "event": event, "detail": detail})

    def log_fill_quality(self, trade_id: str, symbol: str, side: str,
                         signal_price: float, filled_avg_price: float,
                         slippage_bps: float):
        """Measured execution cost for one fill: signal price (the close the
        decision was made on) vs the broker's actual average fill. Positive
        slippage_bps = the fill was worse than the signal price."""
        self._append({
            "type": "fill_quality",
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "signal_price": signal_price,
            "filled_avg_price": filled_avg_price,
            "slippage_bps": round(slippage_bps, 2),
        })

    # ---- reads ----

    def all_records(self) -> list[dict]:
        return self._store.read_all()

    def open_buys(self) -> dict:
        """trade_id -> decision record for executed buys with no outcome yet."""
        records = self.all_records()
        closed = {r["trade_id"] for r in records if r["type"] == "outcome"}
        return {r["trade_id"]: r for r in records
                if r["type"] == "decision" and r["action"] == "buy"
                and r["executed"] and r["trade_id"] not in closed}

    def closed_trades(self) -> list[dict]:
        """Decisions joined with their outcomes, oldest first."""
        records = self.all_records()
        decisions = {r["trade_id"]: r for r in records if r["type"] == "decision"}
        out = []
        for r in records:
            if r["type"] == "outcome" and r["trade_id"] in decisions:
                merged = dict(decisions[r["trade_id"]])
                merged.update({k: r[k] for k in ("exit_price", "pnl", "pnl_pct", "result")})
                merged["exit_reason"] = r.get("exit_reason", "")
                merged["exit_ts"] = r.get("ts")   # when the outcome was recorded
                out.append(merged)
        return out
