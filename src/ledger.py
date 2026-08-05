"""Trade ledger — the audit trail. Every decision (including skips and
rejections) is appended as one JSON line with full reasoning.

This is the 'enterprise' documentation layer: append-only JSONL, one
record per decision, machine-parseable and human-readable. Outcomes are
written only AFTER a position closes (the 'outcome embargo' from the
research — memory must never see a result before it was observable).
"""
import json
from datetime import datetime, timezone

import uuid

import store as store_mod
from strategies.base import ENTRY_ACTIONS


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
                     entry_ts: str | None = None,
                     trade_plan: dict | None = None,
                     rail: str | None = None) -> str:
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
            # WHICH rail refused this, as a queryable key rather than prose
            # (2026-08-02). §40 made the same fix in the backtester's census on
            # 2026-07-29 — "the reason existed at the moment of the block and
            # was dropped" — but only there, so until now the simulator could
            # break blocks down by rail and the LIVE bot could not. Answering
            # "what stopped us trading this week" meant string-matching `detail`
            # against message text that is free to be reworded.
            # None on every non-rejection record, which is most of them.
            "rail": rail,
            "order": order,
            "entry_price": entry_price,
            "qty": qty,
            # The position's REAL fill time when it differs from this record's
            # write time (adopted positions). `ts` keeps its audit meaning —
            # when the record was appended — so age checks read entry_ts first.
            "entry_ts": entry_ts,
            # The bot's game plan at entry: thesis, expected hold, exit levels,
            # regime. Every field is read from a real signal/order/config value
            # (src/trade_plan.py) — never generated prose, never a forecast.
            "trade_plan": trade_plan,
            "outcome": None,               # embargoed until close — see close_trade()
        })
        return trade_id

    def close_trade(self, trade_id: str, exit_price: float, pnl: float, pnl_pct: float,
                    exit_reason: str = "", benchmark_pnl_pct: float | None = None):
        """Record the outcome of a closed trade as a separate event (append-only).

        exit_reason: strategy_sell | stop_loss | take_profit | closed_order |
        last_price_estimate | entry_unfilled ("" in pre-bracket records).

        benchmark_pnl_pct: the benchmark's % move over the SAME holding window,
        or None when it could not be computed. `alpha_pct` is derived from it.

        `result` stays keyed to absolute P&L — it is what the broker did, and
        rewriting its meaning would silently reinterpret 564 existing records.
        Alpha is added ALONGSIDE it (2026-07-27) because a +3% trade in a +5%
        week is a win by that field and a miss by this one, and until now only
        the flattering half reached lessons.py.

        Absent benchmark => both new fields absent, never 0.0: "unknown" and
        "matched the market" must not collapse into the same number.
        """
        rec = {
            "type": "outcome",
            "trade_id": trade_id,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 3),
            "result": "win" if pnl > 0 else "loss",
            "exit_reason": exit_reason,
        }
        if benchmark_pnl_pct is not None:
            rec["benchmark_pnl_pct"] = round(benchmark_pnl_pct, 3)
            rec["alpha_pct"] = round(pnl_pct - benchmark_pnl_pct, 3)
        self._append(rec)

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

    def log_positions_mark(self, positions: dict):
        """Snapshot of what the open book is worth RIGHT NOW, from the broker.

        DISPLAY ONLY. Invariant #4 says positions and equity for a trading
        decision are read fresh from the broker every cycle — this record is a
        photograph for the dashboard, never an input to sizing, signals or the
        judge. Reading it back to make a decision would reintroduce exactly the
        stale-state bug the invariant exists to prevent.

        Stored RAW (the broker's own qty / avg_entry / market_value /
        unrealized_pl) rather than pre-computed percentages, so the record stays
        lossless and the presentation layer can change its mind later.

        Written at every touchpoint that already holds a broker — 09:35, 15:45
        and 16:20 ET — so `dashboard.html`, a static file published to GitHub
        Pages, can show current value without needing keys or a network call at
        render time.
        """
        if not positions:
            return
        self._append({
            "type": "event",
            "event": "positions_mark",
            "detail": json.dumps({
                sym: {"qty": p.get("qty"),
                      "avg_entry": p.get("avg_entry"),
                      "market_value": p.get("market_value"),
                      "unrealized_pl": p.get("unrealized_pl")}
                for sym, p in sorted(positions.items())
            }, separators=(",", ":")),
        })

    # ---- reads ----

    def all_records(self) -> list[dict]:
        return self._store.read_all()

    def open_buys(self) -> dict:
        """trade_id -> decision record for executed ENTRIES with no outcome yet.

        ENTRY_ACTIONS, not `== "buy"`. This is the ONLY path by which the live
        bot reloads its open book from persisted state (main.py reads it at
        cycle start, and reconcile/adopt both key off it), so on the old filter
        a restart would have silently dropped every open SHORT: the position
        would still exist at the broker, but the bot would hold no record of
        it — no owner to route its exit, no stop-risk in the heat cap, and
        `adopt_untracked_positions` would then re-adopt it as a fresh trade
        with a fabricated entry price.

        The NAME is deliberately unchanged. It is now imprecise (it returns
        shorts too), but renaming it would rewrite ~25 call sites across six
        test files and three read-only mirrors inside the largest single
        commit this project has made — churn, in the one commit where diff
        size is itself a risk. Flagged for a follow-up rename to
        `open_entries()`; do not read the name as evidence that shorts are
        excluded."""
        records = self.all_records()
        closed = {r["trade_id"] for r in records if r["type"] == "outcome"}
        return {r["trade_id"]: r for r in records
                if r["type"] == "decision" and r["action"] in ENTRY_ACTIONS
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
