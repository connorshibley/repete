"""Learning-loop engine — `python src/learn.py`.

Runs the closed loop the stores enable:
  evaluate unprocessed closed trades against the hypothesis book (LLM, bounded)
  -> resolve realized judgments (pure ledger join)
  -> resolve due counterfactuals for vetoed buys (read-only bars, capped, embargoed)
  -> apply deterministic lifecycle transitions (mirrored to the ledger for audit)
  -> optional --meta: one LLM merge of near-duplicates (deterministic caps)
  -> regenerate learnings.md (a rendered view of the store).

inline_pass() is the cheap subset main.py calls at cycle end so learning stays
fresh without a second cron. Everything degrades gracefully: no LLM key ->
learning pauses; data failures -> retried next run; nothing here can crash a
trading cycle or place an order.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import counterfactual
import llm
import lessons as lessons_mod
import judgments as judgments_mod

log = logging.getLogger("learn")


# ---- evaluation (closed trades vs hypothesis book) ----

def evaluate_closed_trades(ledger, lessons, cfg, limit: int) -> int:
    """Match up to `limit` unprocessed closed trades against live lessons.
    One LLM call per trade; on failure nothing is written so the trade is
    retried next run. Returns number of trades evaluated."""
    done = lessons.evaluated_trade_ids()
    live = lessons.by_status("candidate", "active")
    n = 0
    for trade in ledger.closed_trades():
        if n >= limit:
            break
        tid = trade["trade_id"]
        if tid in done:
            continue
        # A trade never provides evidence for the lesson it created.
        candidates = [s for s in live if s.get("source") != tid]
        if not candidates:
            lessons.mark_trade_evaluated(tid, 0)
            n += 1
            continue
        relations = llm.evaluate_trade_vs_lessons(trade, candidates, cfg)
        if relations is None:
            continue  # LLM unavailable — retry next run
        for r in relations:
            if r["relation"] != "unrelated":
                lessons.add_evidence(r["lesson_id"], tid, r["relation"],
                                     r.get("reason", ""))
        lessons.mark_trade_evaluated(tid, len(candidates))
        n += 1
    return n


# ---- judgment resolution ----

def grade_cited_lessons(lessons, judgment: dict, assessment: str) -> int:
    """Citation-graded lessons (decision-time attribution): the judge named
    the lesson ids that drove a verdict; the verdict's resolved outcome now
    scores those lessons through the normal evidence lifecycle.
    good_* -> supports (the lesson gave good guidance), bad_* -> contradicts.
    Deterministic guards: unknown/terminal lesson ids are dropped, and a
    trade never evidences the same lesson twice (the post-hoc evaluator may
    already have linked them)."""
    cited = judgment.get("cited_lessons") or []
    if not cited:
        return 0
    states = lessons.replay()
    n = 0
    for lid in cited:
        s = states.get(lid)
        if not s or s["status"] in lessons_mod.TERMINAL:
            continue
        tid = judgment["trade_id"]
        if tid in s["supports"] or tid in s["contradicts"]:
            continue  # already evidenced by this trade — never double-count
        rel = "supports" if assessment.startswith("good") else "contradicts"
        lessons.add_evidence(lid, tid, rel,
                             f"cited in {judgment['verdict']} judgment "
                             f"{judgment['id']} -> {assessment}")
        n += 1
    return n


def resolve_realized(ledger, judgments, lessons=None) -> int:
    """Join executed judgments to ledger outcomes (pure, no network)."""
    outcomes = {t["trade_id"]: t for t in ledger.closed_trades()}
    n = 0
    for j in judgments.unresolved():
        if not j["executed"] or j["trade_id"] not in outcomes:
            continue
        t = outcomes[j["trade_id"]]
        assessment = judgments_mod.assess(j["verdict"], True, t["pnl_pct"])
        judgments.log_resolution(
            j["id"], "realized", t["pnl_pct"], t.get("exit_reason", ""),
            assessment)
        if lessons is not None:
            grade_cited_lessons(lessons, j, assessment)
        n += 1
    return n


def resolve_counterfactuals(broker, judgments, cfg, now=None, lessons=None) -> int:
    """Resolve due vetoed/rejected buys via read-only bars. Capped per run;
    data failures skip and retry. Embargo enforced by resolution_due()."""
    lcfg = cfg["learning"]
    min_days = cfg["risk"].get("min_holding_days", 0)
    extra = lcfg["counterfactual_extra_days"]
    horizon = min_days + extra
    now = now or datetime.now(timezone.utc)
    n = 0
    for j in judgments.unresolved():
        # LOAD-BEARING: `!= "buy"`, deliberately NOT `not in ENTRY_ACTIONS`.
        #
        # This is the guard that keeps vetoed SHORTS out of the counterfactual
        # scorer, and it is the only thing standing between src/counterfactual.py
        # and a systematically corrupted learning loop. simulate_veto_counterfactual
        # is hard-coded long at lines 45-48: it fires the stop on `low <= stop`
        # and the target on `high >= tp`, and it returns `(exit - entry)/entry`
        # unsigned. A short's stop sits ABOVE entry, so the FIRST bar's low is
        # already below it — every vetoed short would resolve `stop_loss` on
        # bar one, and `_pct(stop)` on a stop above entry is POSITIVE. Result:
        # every vetoed short scored as a missed WINNER, without exception and
        # without a single failing test, teaching the judge that its short
        # vetoes were always wrong.
        #
        # Before widening this to ENTRY_ACTIONS, counterfactual.py must first
        # take the trade's direction and (a) test `high >= stop` / `low <= tp`
        # for a short and (b) sign `_pct` the way main.handle_close now does.
        # That is its own commit with its own evidence implications — the
        # judgment ledger is what judge calibration is computed from, so a
        # wrong resolution is a permanent false record, not a missed one.
        if j["executed"] or j["action"] != "buy":
            continue
        if not counterfactual.resolution_due(j["ts"], min_days, extra, now):
            continue
        if n >= lcfg["counterfactual_max_per_run"]:
            break
        # Fetch enough history to reach BACK to the decision date — an old
        # judgment (delayed by data failures) must still find its window.
        age_days = (now - datetime.fromisoformat(j["ts"])).days
        try:
            bars = broker.bars(j["symbol"], "1Day", age_days + horizon + 10)
        except Exception as e:  # noqa: BLE001 — retry next run
            log.warning("Counterfactual bars fetch failed for %s: %s",
                        j["symbol"], e)
            continue
        result = counterfactual.simulate_veto_counterfactual(
            bars, j["ts"], j["price_at_decision"],
            j.get("stop_price"), j.get("tp_price"), horizon)
        if result is None:
            continue
        assessment = judgments_mod.assess(j["verdict"], False, result["pnl_pct"])
        judgments.log_resolution(
            j["id"], "counterfactual", result["pnl_pct"], result["exit_reason"],
            assessment)
        if lessons is not None:
            grade_cited_lessons(lessons, j, assessment)
        n += 1
    return n


# ---- lifecycle + meta ----

def run_transitions(ledger, lessons, cfg, now=None) -> int:
    now = now or datetime.now(timezone.utc)
    transitions = lessons_mod.apply_transitions(lessons.replay(), now,
                                                cfg["learning"])
    for lid, from_s, to_s, reason in transitions:
        lessons.change_status(lid, from_s, to_s, reason)
        ledger.log_event(f"lesson_{to_s}", f"{lid}: {reason}")
    return len(transitions)


def apply_meta_merge(ledger, lessons, cfg) -> bool:
    """One LLM merge proposal per run, applied under deterministic caps.
    The merged lesson restarts as candidate and inherits the union of
    evidence, so it must re-earn active status."""
    live = lessons.by_status("candidate", "active")
    if len(live) < 2:
        return False
    merge = llm.propose_merge(live, cfg)
    if not merge:
        return False
    by_id = {s["id"]: s for s in live}
    sources = [by_id[i] for i in merge["source_ids"] if i in by_id]
    if len(sources) < 2:
        return False
    new_id = lessons.add_lesson(merge["hypothesis"], merge.get("scope") or {},
                                source="consolidation")
    seen = set()
    for s in sources:
        for rel in ("supports", "contradicts"):
            for tid in s[rel]:
                if (tid, rel) not in seen:  # union, not double-count
                    seen.add((tid, rel))
                    lessons.add_evidence(new_id, tid, rel,
                                         f"inherited from {s['id']}")
        lessons.change_status(s["id"], s["status"], "retired",
                              f"merged into {new_id}")
        ledger.log_event("lesson_retired", f"{s['id']}: merged into {new_id}")
    ledger.log_event("lesson_merged",
                     f"{new_id} <- {[s['id'] for s in sources]}")
    return True


# ---- entry points ----

def inline_pass(ledger, lessons, judgments, cfg, broker=None) -> dict:
    """Cheap cycle-end pass: bounded evaluation + pure resolutions +
    transitions + render. Never raises."""
    summary = {"evaluated": 0, "realized": 0, "counterfactual": 0,
               "transitions": 0}
    try:
        summary["evaluated"] = evaluate_closed_trades(
            ledger, lessons, cfg, cfg["learning"]["max_llm_calls_per_cycle"])
        summary["realized"] = resolve_realized(ledger, judgments, lessons)
        if broker is not None:
            summary["counterfactual"] = resolve_counterfactuals(
                broker, judgments, cfg, lessons=lessons)
        summary["transitions"] = run_transitions(ledger, lessons, cfg)
        lessons_mod.render_markdown(lessons.replay(),
                                    cfg["memory"]["learnings_path"])
    except Exception as e:  # noqa: BLE001 — learning must never kill a cycle
        log.error("inline learning pass failed: %s", e)
        ledger.log_event("learning_error", str(e))
    return summary


def migrate_legacy(ledger, lessons, cfg) -> int:
    """Import hand-written learnings.md bullets into the store (idempotent —
    a GENERATED file yields nothing)."""
    parsed = lessons_mod.parse_legacy_markdown(cfg["memory"]["learnings_path"])
    for p in parsed:
        lessons.add_lesson(p["text"], {}, source=p["trade_id"])
    if parsed:
        ledger.log_event("learnings_migrated", f"{len(parsed)} legacy bullets")
    return len(parsed)


def full_run(cfg, with_meta: bool = False, broker=None) -> dict:
    from ledger import Ledger
    from lessons import LessonStore
    from judgments import JudgmentStore

    ledger = Ledger(cfg["memory"]["ledger_path"])
    lessons = LessonStore(cfg["learning"]["lessons_path"])
    judgments = JudgmentStore(cfg["learning"]["judgments_path"])

    summary = {"migrated": migrate_legacy(ledger, lessons, cfg)}
    summary["evaluated"] = evaluate_closed_trades(
        ledger, lessons, cfg, cfg["learning"]["evaluator_batch_size"])
    summary["realized"] = resolve_realized(ledger, judgments, lessons)
    summary["counterfactual"] = (resolve_counterfactuals(broker, judgments, cfg,
                                                         lessons=lessons)
                                 if broker is not None else 0)
    summary["transitions"] = run_transitions(ledger, lessons, cfg)
    summary["merged"] = apply_meta_merge(ledger, lessons, cfg) if with_meta else False
    lessons_mod.render_markdown(lessons.replay(),
                                cfg["memory"]["learnings_path"])

    states = lessons.replay()
    summary["book"] = {st: sum(1 for s in states.values() if s["status"] == st)
                       for st in ("candidate", "active", "refuted", "retired")}
    return summary


def main():
    import yaml
    from dotenv import load_dotenv
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    load_dotenv(".env")
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    p = argparse.ArgumentParser(description="Learning-loop consolidation")
    p.add_argument("--meta", action="store_true",
                   help="also run the (LLM) near-duplicate merge pass")
    p.add_argument("--no-broker", action="store_true",
                   help="skip counterfactual resolution (no data API calls)")
    args = p.parse_args()

    broker = None
    if not args.no_broker:
        try:
            from broker import Broker
            broker = Broker(cfg)
        except Exception as e:  # noqa: BLE001 — counterfactuals just wait
            log.warning("Broker unavailable (%s) — skipping counterfactuals", e)

    s = full_run(cfg, with_meta=args.meta, broker=broker)
    print(f"Learning pass complete: {s['evaluated']} trades evaluated, "
          f"{s['realized']} realized + {s['counterfactual']} counterfactual "
          f"resolutions, {s['transitions']} lifecycle transitions, "
          f"merge={'yes' if s['merged'] else 'no'}, "
          f"migrated={s['migrated']}.")
    print(f"Hypothesis book: {s['book']}")


if __name__ == "__main__":
    main()
