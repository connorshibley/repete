"""Post-exit runner tracking — what happened AFTER we closed.

The one learning loop the bot lacked (ported from the common-trade
comparison, 2026-07-19): every close is marked again at 15/30/60 calendar
days so exit rules can be judged on evidence instead of vibes.

  leftover_pct      = (close_now − exit_price) / exit_price
  max_extension_pct = best high after exit vs exit_price

Verdicts (final mark day):
  winners: left_on_table  — kept running ≥ left_on_table_pct after we sold
           good_lock_in   — faded ≤ good_lock_in_pct; banking was right
           mixed          — neither
  losers:  stopped_then_recovered — bounced ≥ left_on_table_pct after the stop
           stop_confirmed         — kept falling / went nowhere

Design constraints (CLAUDE.md invariants):
  * outcome embargo intact — tracks only CLOSED trades; marks are
    observations made after the close, stamped at mark time;
  * measurement only — results feed the review report and future gate
    candidates; nothing here touches sizing, signals, or the judge prompt;
  * append-only JSONL + replay (the ledger pattern); bounded bar fetches
    per cycle; failures never crash a cycle.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import logging

log = logging.getLogger("postexit")

DEFAULTS = {"enabled": True, "path": "memory/postexit.jsonl",
            "mark_days": [15, 30, 60], "left_on_table_pct": 8.0,
            "good_lock_in_pct": -5.0, "max_marks_per_run": 10}


def _cfg(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("learning", {}).get("postexit") or {})}


class PostExitStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _records(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def replay(self) -> dict:
        """trade_id -> track state {track fields, marks: {day: mark}, verdict}."""
        states: dict = {}
        for r in self._records():
            if r["event"] == "track":
                states[r["trade_id"]] = {**r, "marks": {}, "verdict": None}
            elif r["event"] == "mark" and r["trade_id"] in states:
                states[r["trade_id"]]["marks"][r["day"]] = r
            elif r["event"] == "verdict" and r["trade_id"] in states:
                states[r["trade_id"]]["verdict"] = r["verdict"]
        return states


# ---- pure measurement ----

def window_after_exit(bars: list[dict], exit_ts: str, day: int) -> list[dict]:
    """Bars strictly after the exit, up to exit + day calendar days."""
    start = datetime.fromisoformat(exit_ts)
    end = start + timedelta(days=day)
    out = []
    for b in bars:
        t = datetime.fromisoformat(b["ts"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if start < t <= end:
            out.append(b)
    return out


def compute_mark(bars: list[dict], exit_ts: str, exit_price: float,
                 day: int) -> dict | None:
    """Leftover/extension for one mark window. None when no bars or bad price."""
    if not exit_price or exit_price <= 0:
        return None
    window = window_after_exit(bars, exit_ts, day)
    if not window:
        return None
    last = window[-1]["close"]
    hi = max(b["high"] for b in window)
    lo = min(b["low"] for b in window)
    return {"price": last,
            "leftover_pct": round((last - exit_price) / exit_price * 100, 2),
            "max_extension_pct": round((hi - exit_price) / exit_price * 100, 2),
            "min_extension_pct": round((lo - exit_price) / exit_price * 100, 2)}


def mark_due(state: dict, now: datetime, mark_days: list[int]) -> list[int]:
    """Mark days whose window has fully elapsed and aren't recorded yet."""
    exit_dt = datetime.fromisoformat(state["exit_ts"])
    return [d for d in mark_days
            if d not in state["marks"] and now >= exit_dt + timedelta(days=d)]


def verdict_for(state: dict, pcfg: dict) -> str | None:
    """Final verdict once the last mark day is in. None until then."""
    final_day = max(pcfg["mark_days"])
    final = state["marks"].get(final_day)
    if not final:
        return None
    max_ext = max(m["max_extension_pct"] for m in state["marks"].values())
    if state.get("result") == "win":
        if max_ext >= pcfg["left_on_table_pct"]:
            return "left_on_table"
        if final["leftover_pct"] <= pcfg["good_lock_in_pct"]:
            return "good_lock_in"
        return "mixed"
    if max_ext >= pcfg["left_on_table_pct"]:
        return "stopped_then_recovered"
    return "stop_confirmed"


def summary(states: dict) -> dict:
    """Aggregate for the review report. Pure."""
    done = [s for s in states.values() if s["verdict"]]
    verdicts: dict = {}
    for s in done:
        verdicts[s["verdict"]] = verdicts.get(s["verdict"], 0) + 1
    win_exts = [max(m["max_extension_pct"] for m in s["marks"].values())
                for s in done if s.get("result") == "win" and s["marks"]]
    return {"n_tracking": sum(1 for s in states.values() if not s["verdict"]),
            "n_resolved": len(done), "verdicts": verdicts,
            "avg_winner_max_extension_pct":
                round(sum(win_exts) / len(win_exts), 2) if win_exts else None}


# ---- cycle wiring (bounded, fail-soft) ----

def run(ledger, broker, cfg: dict, now: datetime | None = None) -> dict:
    """Open tracks for new closes, take due marks, write final verdicts.
    Bounded bar fetches; any failure skips and retries next cycle."""
    pcfg = _cfg(cfg)
    out = {"opened": 0, "marked": 0, "resolved": 0}
    if not pcfg["enabled"]:
        return out
    try:
        now = now or datetime.now(timezone.utc)
        store = PostExitStore(pcfg["path"])
        states = store.replay()

        for t in ledger.closed_trades():
            if t["trade_id"] in states or not t.get("exit_ts"):
                continue
            if t.get("exit_reason") == "entry_unfilled":
                continue  # nothing was held; nothing to learn from
            store.append({"event": "track", "trade_id": t["trade_id"],
                          "symbol": t["symbol"],
                          "strategy": t.get("strategy"),
                          "exit_ts": t["exit_ts"],
                          "exit_price": t["exit_price"],
                          "entry_price": t.get("entry_price"),
                          "pnl_pct": t.get("pnl_pct"),
                          "exit_reason": t.get("exit_reason"),
                          "result": t.get("result")})
            out["opened"] += 1

        states = store.replay()
        fetches = 0
        for tid, s in states.items():
            if s["verdict"] or fetches >= pcfg["max_marks_per_run"]:
                continue
            due = mark_due(s, now, pcfg["mark_days"])
            if not due:
                continue
            age = (now - datetime.fromisoformat(s["exit_ts"])).days
            try:
                bars = broker.bars(s["symbol"], "1Day", age + 10)
                fetches += 1
            except Exception as e:  # noqa: BLE001 — retry next cycle
                log.warning("postexit bars fetch failed for %s: %s",
                            s["symbol"], e)
                continue
            for day in due:
                mark = compute_mark(bars, s["exit_ts"], s["exit_price"], day)
                if mark is None:
                    continue
                store.append({"event": "mark", "trade_id": tid, "day": day,
                              **mark})
                s["marks"][day] = mark
                out["marked"] += 1
            verdict = verdict_for(s, pcfg)
            if verdict:
                store.append({"event": "verdict", "trade_id": tid,
                              "verdict": verdict})
                ledger.log_event("postexit_verdict",
                                 f"{s['symbol']} trade {tid}: {verdict}")
                out["resolved"] += 1
    except Exception as e:  # noqa: BLE001 — measurement never kills a cycle
        log.error("postexit pass failed: %s", e)
    return out
