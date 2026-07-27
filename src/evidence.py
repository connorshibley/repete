"""Audit pack exporter (Phase C, 2026-07-22) — `python src/evidence.py`.

Bundles the proof a skeptical outsider (subscriber, attorney, future
auditor) would ask for into one dated directory:

    summary.md         cover page: gates, counts, model version
    performance.json   report card + monthly scorecard + version segments
    lineage.json       per-trade decision chain: entry -> judge -> exit
    invariants.json    machine-checked safety-invariant spot checks

Strictly read-only and offline: ledger + config + rendered pages only,
never the broker (an audit pack must be producible with zero keys). A
missing stream is noted in the pack, never a crash.
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# repo root too — the revenue-gate check imports the publisher package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ledger import Ledger
import disclaimer
import modelver
import review
import scorecard

GATE_MIN_DAYS = review.GATE_MIN_DAYS
GATE_MIN_CLOSED = review.GATE_MIN_CLOSED


def _jsonable(obj):
    """inf/nan are not valid JSON — stringify them so the pack always parses."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return "inf" if obj > 0 else ("-inf" if obj < 0 else "nan")
    return obj


def lineage(records: list[dict]) -> dict:
    """Per-trade decision chain keyed by trade_id. Every closed trade must
    trace entry decision -> judge verdict -> exit outcome; gaps are listed
    honestly under `incomplete` instead of being papered over."""
    decisions = {r["trade_id"]: r for r in records
                 if r.get("type") == "decision" and r.get("trade_id")}
    outcomes = {r["trade_id"]: r for r in records if r.get("type") == "outcome"}
    fills: dict = {}
    for r in records:
        if r.get("type") == "fill_quality" and r.get("trade_id"):
            fills.setdefault(r["trade_id"], []).append(
                {"ts": r.get("ts"), "side": r.get("side"),
                 "slippage_bps": r.get("slippage_bps")})

    trades, incomplete = {}, []
    for tid, d in decisions.items():
        if not d.get("executed"):
            continue
        rev = d.get("llm_review") or {}
        o = outcomes.get(tid)
        trades[tid] = {
            "symbol": d.get("symbol"),
            "strategy": d.get("strategy"),
            "model_version": d.get("model_version"),
            "entry": {"ts": d.get("ts"), "action": d.get("action"),
                      "reason": d.get("reason"), "qty": d.get("qty"),
                      "price": d.get("price")},
            "judge": {"verdict": rev.get("verdict"),
                      "scale": rev.get("scale"),
                      "confidence": rev.get("confidence"),
                      "reasoning": rev.get("reasoning")} if rev else None,
            "exit": ({"ts": o.get("ts"), "exit_price": o.get("exit_price"),
                      "pnl": o.get("pnl"), "pnl_pct": o.get("pnl_pct"),
                      "result": o.get("result"),
                      "exit_reason": o.get("exit_reason")} if o else None),
            "fills": fills.get(tid, []),
        }
    for tid, o in outcomes.items():
        if tid not in decisions:
            incomplete.append({"trade_id": tid,
                               "problem": "outcome without entry decision"})
    return {"n_trades": len(trades), "trades": trades,
            "incomplete": incomplete}


def _stream_scan(path: str) -> dict:
    """Every line of an append-only stream must parse as JSON — a corrupted
    line is exactly what this check exists to catch."""
    try:
        with open(path) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return {"pass": None, "detail": f"{path}: stream missing (fail-soft)"}
    bad = 0
    for ln in lines:
        try:
            json.loads(ln)
        except ValueError:
            bad += 1
    return {"pass": bad == 0,
            "detail": f"{path}: {len(lines)} records, {bad} unparseable"}


def invariants_check(cfg: dict, records: list[dict], root: str = ".") -> dict:
    """Machine-checked spot checks of the CLAUDE.md safety invariants that
    ARE checkable from files alone. Each check: pass True/False/None
    (None = not verifiable offline) + a human-readable detail line."""
    checks: dict = {}

    mode = cfg.get("mode", "paper")
    live_ok = os.environ.get("LIVE_TRADING_CONFIRMED", "NO") == "YES"
    checks["paper_mode_interlock"] = {
        "pass": mode != "live" or live_ok is False,
        "detail": (f"mode={mode}; live requires BOTH config and "
                   f"LIVE_TRADING_CONFIRMED=YES (interlock intact)")}
    if mode == "live":  # would only pass with both halves — flag loudly
        checks["paper_mode_interlock"]["pass"] = False
        checks["paper_mode_interlock"]["detail"] = "mode=live configured!"

    ledger_path = cfg.get("memory", {}).get("ledger_path",
                                            "memory/ledger.jsonl")
    checks["ledger_stream_integrity"] = _stream_scan(
        os.path.join(root, ledger_path))

    # Outcome embargo: no still-open trade may carry outcome fields.
    open_with_outcome = [
        r["trade_id"] for r in records
        if r.get("type") == "decision" and r.get("executed")
        and r.get("action") == "buy"
        and ("pnl" in r or "result" in r)]
    checks["outcome_embargo"] = {
        "pass": not open_with_outcome,
        "detail": (f"{len(open_with_outcome)} entry decisions carry outcome "
                   f"fields (must be 0 — outcomes are separate records "
                   f"written only at close)")}

    # Every executed entry has a judge record on it (LLM reviewed, never
    # generated) — reconciled broker-side closes legitimately lack one.
    executed_buys = [r for r in records if r.get("type") == "decision"
                     and r.get("executed") and r.get("action") == "buy"]
    # A `degraded` review is the FALLBACK verdict, not a judgement: the judge
    # was unreachable or unconfigured, and the trade was approved at full size
    # regardless. Testing only for the PRESENCE of an llm_review block let this
    # report "0 missing" on entries no judge ever saw — an evidence pack
    # asserting something untrue, which is worse than having no pack at all.
    no_judge = [r["trade_id"] for r in executed_buys
                if (not r.get("llm_review")
                    or (r.get("llm_review") or {}).get("degraded"))
                and "reconcile" not in (r.get("reason") or "")]
    checks["every_entry_judged"] = {
        "pass": not no_judge,
        "detail": (f"{len(executed_buys)} executed entries, "
                   f"{len(no_judge)} without a real judge verdict "
                   f"(absent, or a degraded fallback)")}

    # Disclaimer on every rendered public page that exists.
    pages = {}
    for page in ("dashboard.html", "journal.html", "blog.html"):
        p = os.path.join(root, page)
        if not os.path.exists(p):
            pages[page] = "not rendered"
            continue
        with open(p, encoding="utf-8") as f:
            pages[page] = ("present" if "PAPER-TRADING experiment" in f.read()
                           else "MISSING")
    checks["disclaimer_on_pages"] = {
        "pass": all(v != "MISSING" for v in pages.values()),
        "detail": json.dumps(pages)}

    hard_fail = [k for k, c in checks.items() if c["pass"] is False]
    return {"checks": checks, "all_pass": not hard_fail,
            "failed": hard_fail}


def gates_status(report: dict, cfg: dict) -> dict:
    """Both pre-registered gates, from ledger facts alone (benchmark legs
    need market data and are marked unverifiable offline)."""
    trading = {
        "min_days": {"gate": GATE_MIN_DAYS, "have": report["history_days"],
                     "pass": report["history_days"] >= GATE_MIN_DAYS},
        "min_closed": {"gate": GATE_MIN_CLOSED, "have": report["n_closed"],
                       "pass": report["n_closed"] >= GATE_MIN_CLOSED},
        "beats_buy_and_hold": {"pass": None,
                               "detail": "needs market data — see review.py"},
    }
    revenue = None
    try:
        from publisher import config as pub_config, gates as pub_gates

        class _R:  # minimal read-only view for the gate check
            def __init__(self, records):
                self._records = records

            def all_records(self):
                return self._records

            def closed_trades(self):
                by_id = {r["trade_id"]: r for r in self._records
                         if r.get("type") == "decision" and r.get("trade_id")}
                return [{**by_id[r["trade_id"]], **r} for r in self._records
                        if r.get("type") == "outcome"
                        and r["trade_id"] in by_id]

        pcfg = pub_config.load()
        open_, reasons = pub_gates.revenue_gate(pcfg, _R(cfg["_records"]))
        revenue = {"open": open_, "reasons": reasons}
    except Exception as e:  # noqa: BLE001 — the pack must build regardless
        revenue = {"open": None, "reasons": [f"not evaluated: {e}"]}
    return {"trading_go_live": trading, "revenue": revenue}


def build_pack(cfg: dict, records: list[dict], now: datetime,
               root: str = ".") -> dict:
    """All four artifacts as one dict (pure enough to unit-test)."""
    report = review.build_report(records, [], now)
    card = scorecard.monthly_scorecard(
        records, [], scorecard.realized_pnl_by_month(records))
    closed = lineage(records)
    version = modelver.current_version(root)
    cfg = {**cfg, "_records": records}   # hand records to the gate check

    perf = {
        "generated": now.isoformat(),
        "report": report,
        "monthly_scorecard": card,
        "per_strategy": review.per_strategy_breakdown(
            _closed_trades(records)),
        "model_versions": review.model_version_breakdown(
            _closed_trades(records), version),
    }
    inv = invariants_check(cfg, records, root)
    gates = gates_status(report, cfg)

    summary = "\n".join([
        "# Repete — audit pack",
        f"Generated {now.strftime('%Y-%m-%d %H:%M UTC')} · model version "
        f"`{version}`",
        "",
        f"- History: **{report['history_days']} days**, "
        f"{report['n_decisions']} decisions, {report['n_executed']} executed, "
        f"**{report['n_closed']} closed** ({report['n_open']} open)",
        f"- Realized P&L: **${report['realized_pnl']:,.2f}** (paper)",
        f"- Trading go-live gate: "
        f"{'PASS' if all(g.get('pass') for g in gates['trading_go_live'].values() if g.get('pass') is not None) else 'NOT MET'}"
        f" (benchmark leg needs market data — run src/review.py)",
        f"- Revenue gate: "
        + ("OPEN" if gates["revenue"]["open"]
           else "CLOSED — " + "; ".join(gates["revenue"]["reasons"])),
        f"- Invariant checks: "
        + ("ALL PASS" if inv["all_pass"]
           else "FAILED: " + ", ".join(inv["failed"])),
        "",
        "Files: performance.json · lineage.json · invariants.json",
        "",
        f"> {disclaimer.DISCLAIMER}",
        ""])

    return {"summary.md": summary,
            "performance.json": _jsonable(perf),
            "lineage.json": _jsonable(closed),
            "invariants.json": _jsonable({"generated": now.isoformat(),
                                          "gates": gates, **inv})}


def _closed_trades(records: list[dict]) -> list[dict]:
    by_id = {r["trade_id"]: r for r in records
             if r.get("type") == "decision" and r.get("trade_id")}
    return [{**by_id[r["trade_id"]], **r} for r in records
            if r.get("type") == "outcome" and r["trade_id"] in by_id]


def export(out_dir: str, cfg: dict, records: list[dict],
           now: datetime | None = None, root: str = ".") -> str:
    now = now or datetime.now(timezone.utc)
    pack = build_pack(cfg, records, now, root)
    dest = os.path.join(out_dir, f"pack-{now.strftime('%Y%m%d-%H%M')}")
    os.makedirs(dest, exist_ok=True)
    for name, content in pack.items():
        with open(os.path.join(dest, name), "w") as f:
            if name.endswith(".json"):
                json.dump(content, f, indent=2, default=str)
            else:
                f.write(content)
    return dest


def main():
    ap = argparse.ArgumentParser(description="export the audit pack")
    ap.add_argument("--out", default="evidence")
    args = ap.parse_args()
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    records = Ledger(cfg["memory"]["ledger_path"]).all_records()
    dest = export(args.out, cfg, records)
    print(f"audit pack written: {dest}")
    with open(os.path.join(dest, "summary.md")) as f:
        print(f.read())


if __name__ == "__main__":
    main()
