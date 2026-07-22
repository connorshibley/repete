"""Judge calibration — scoring the LLM judgment layer against reality.

Every reviewed signal writes a judgment event; when its outcome becomes
observable (realized close, or counterfactual after the embargo horizon) a
resolution event is appended. Pure metrics turn the pairs into calibration
numbers that are fed BACK into the review prompt — the judge sees its own
track record.

kind="llm" (approve/downsize/veto) and kind="rails" (risk-rejections) are
bucketed separately: rails rejections calibrate the deterministic rails, not
the judge, and mixing them would corrupt veto precision.
"""
import json
import os
import uuid
from datetime import datetime, timezone

MIN_RESOLVED_FOR_SIGNAL = 30  # below this, calibration is labeled noise


import store as store_mod


class JudgmentStore:
    def __init__(self, path: str):
        self.path = path
        self._store = store_mod.open_store(path)

    def _append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        self._store.append(record)

    def log_judgment(self, trade_id: str, symbol: str, action: str, verdict: str,
                     scale: float, price: float, regime: str | None, kind: str,
                     executed: bool, reasoning: str = "",
                     stop_price: float | None = None,
                     tp_price: float | None = None,
                     strategy: str | None = None,
                     cited_lessons: list[str] | None = None,
                     confidence: float | None = None) -> str:
        jid = f"jg-{uuid.uuid4().hex[:8]}"
        self._append({"event": "judgment", "id": jid, "trade_id": trade_id,
                      "symbol": symbol, "action": action, "verdict": verdict,
                      "scale": scale, "price_at_decision": price,
                      "regime": regime, "strategy": strategy, "kind": kind,
                      "executed": executed, "reasoning": reasoning,
                      "stop_price": stop_price, "tp_price": tp_price,
                      "cited_lessons": cited_lessons or [],
                      "confidence": confidence})  # stated win-probability
        return jid

    def log_resolution(self, judgment_id: str, kind: str, pnl_pct: float,
                       exit_reason: str, assessment: str):
        self._append({"event": "resolution", "judgment_id": judgment_id,
                      "kind": kind, "pnl_pct": pnl_pct,
                      "exit_reason": exit_reason, "assessment": assessment})

    def _records(self) -> list[dict]:
        return self._store.read_all()

    def replay(self) -> dict:
        """judgment_id -> judgment with attached resolution (or None)."""
        out: dict = {}
        for r in self._records():
            if r["event"] == "judgment":
                out[r["id"]] = {**r, "resolution": None}
            elif r["event"] == "resolution" and r["judgment_id"] in out:
                out[r["judgment_id"]]["resolution"] = r
        return out

    def unresolved(self) -> list[dict]:
        return [j for j in self.replay().values() if j["resolution"] is None]


# ---- pure calibration math ----

def assess(verdict: str, executed: bool, pnl_pct: float) -> str:
    """Label a resolved judgment. A veto is good when the blocked trade would
    have lost or gone nowhere; an approve is good when the trade won."""
    if executed:  # approve or downsize that traded
        return ("good_approve" if pnl_pct > 0 else "bad_approve")
    return "good_veto" if pnl_pct <= 0 else "bad_veto"


def recent_outcomes_block(judgments: dict, n: int = 20) -> str:
    """Scoreboard of the judge's last n RESOLVED calls for the review prompt
    (TradingGroup-style outcome-labeled reflection): what it ruled, what
    actually happened. Empty string when nothing is resolved yet."""
    resolved = [j for j in judgments.values()
                if j.get("resolution") and j.get("kind") == "llm"]
    resolved.sort(key=lambda j: j.get("ts") or "")
    lines = []
    for j in resolved[-n:]:
        r = j["resolution"]
        lines.append(
            f"  [{r.get('assessment', '?').upper()}] {j['verdict']} "
            f"{j['symbol']} {j['action']}"
            + (f" ({j['strategy']}" + (f", {j['regime']})" if j.get("regime")
                                       else ")") if j.get("strategy") else "")
            + f" -> {r.get('pnl_pct', 0):+.1f}%"
            + (f" [{r['kind']}]" if r.get("kind") == "counterfactual" else ""))
    if not lines:
        return ""
    return ("YOUR LAST RESOLVED CALLS (what you ruled -> what actually "
            "happened; counterfactuals are what a veto WOULD have done):\n"
            + "\n".join(lines))


def confidence_calibration(judgments: dict) -> dict:
    """Stated confidence vs realized outcome, bucketed (2026-07-21,
    EastEquity-review port). Uses EXECUTED resolved llm judgments that carry a
    stated confidence. Returns {bucket_label: {"n": int, "stated": mean,
    "won": wins}}; empty dict when nothing qualifies."""
    buckets: dict = {}
    for j in judgments.values():
        conf, r = j.get("confidence"), j.get("resolution")
        if (conf is None or not r or j.get("kind") != "llm"
                or not j.get("executed")):
            continue
        lo = min(int(float(conf) * 10) / 10, 0.9)
        label = f"{lo:.1f}-{lo + 0.09:.2f}"
        b = buckets.setdefault(label, {"n": 0, "stated_sum": 0.0, "won": 0})
        b["n"] += 1
        b["stated_sum"] += float(conf)
        b["won"] += 1 if r.get("pnl_pct", 0) > 0 else 0
    return {k: {"n": b["n"], "stated": b["stated_sum"] / b["n"],
                "won": b["won"]}
            for k, b in sorted(buckets.items())}


def confidence_calibration_lines(judgments: dict, min_n: int = 5) -> list[str]:
    """Human-readable calibration block for review.py. Below min_n resolved
    samples it reports the count only — published either way, because hiding
    an unflattering number until it improves is how track records launder."""
    cal = confidence_calibration(judgments)
    total = sum(b["n"] for b in cal.values())
    if total == 0:
        return ["Confidence calibration: no resolved trades carry a stated "
                "confidence yet (field added 2026-07-21; accumulating)"]
    if total < min_n:
        return [f"Confidence calibration: {total} resolved sample(s) — too "
                f"few to read, shown for honesty only"] + [
            f"  {k}: said {b['stated']:.0%} avg, won {b['won']}/{b['n']}"
            for k, b in cal.items()]
    return ["Confidence calibration (stated vs realized):"] + [
        f"  {k}: n={b['n']}, said {b['stated']:.0%} avg, "
        f"won {b['won'] / b['n']:.0%} "
        f"(gap {b['won'] / b['n'] - b['stated']:+.0%})"
        for k, b in cal.items()]


def downsize_value_usd(judgment: dict, pnl: float) -> float:
    """P&L avoided on the shares NOT bought because of a downsize.
    Positive when the downsize helped (the trade lost)."""
    scale = judgment.get("scale", 1.0)
    if scale >= 1.0 or scale <= 0:
        return 0.0
    return -pnl * (1 / scale - 1)


def calibration_metrics(judgments: dict, min_n: int = 5) -> dict:
    llm = [j for j in judgments.values()
           if j["kind"] == "llm" and j["resolution"]]
    vetoes = [j for j in llm if j["verdict"] == "veto"]
    approves = [j for j in llm if j["executed"]]
    rails = [j for j in judgments.values()
             if j["kind"] == "rails" and j["resolution"]]

    def _rate(items, good_label):
        if len(items) < min_n:
            return None
        good = sum(1 for j in items
                   if j["resolution"]["assessment"].startswith("good"))
        return good / len(items)

    downsizes = [j for j in approves if j["verdict"] == "downsize"]
    return {
        "n_resolved": len(llm),
        "veto_precision": _rate(vetoes, "good_veto"),
        "n_vetoes_resolved": len(vetoes),
        "approve_accuracy": _rate(approves, "good_approve"),
        "n_approves_resolved": len(approves),
        "n_downsizes_resolved": len(downsizes),
        "rails_block_precision": _rate(rails, "good_veto"),
        "n_rails_resolved": len(rails),
    }


def calibration_line(metrics: dict) -> str:
    """One skeptical line for the review prompt / report."""
    if metrics["n_resolved"] == 0:
        return "YOUR RECENT CALIBRATION: (no resolved judgments yet)"
    parts = []
    if metrics["veto_precision"] is not None:
        parts.append(f"veto precision {metrics['veto_precision']:.0%} "
                     f"(n={metrics['n_vetoes_resolved']})")
    if metrics["approve_accuracy"] is not None:
        parts.append(f"approve accuracy {metrics['approve_accuracy']:.0%} "
                     f"(n={metrics['n_approves_resolved']})")
    body = ", ".join(parts) or f"{metrics['n_resolved']} resolved, too few per bucket"
    note = (" — fewer than 30 resolved judgments: treat as noise."
            if metrics["n_resolved"] < MIN_RESOLVED_FOR_SIGNAL else "")
    return f"YOUR RECENT CALIBRATION: {body}{note}"
