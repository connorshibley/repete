"""Orchestrator — one full decision cycle per run.

Pipeline per symbol:
    broker state (source of truth) -> deterministic signal -> LLM review
    (approve/downsize/veto only) -> hard risk rails -> execute -> ledger
    -> lesson (on close) -> X recap

Run once per bar via cron/Task Scheduler (see GUIDE.md §6), e.g. daily:
    python src/main.py
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from broker import Broker
from ledger import Ledger
from memory import Memory
import journal
import learn
import llm
import datacheck
import flatten_recovery
import trade_plan
import modelver
import postexit
import preflight
import regime as regime_mod
import risk
import store
import strategies
import strategy
import x_poster
from strategies.base import ENTRY_ACTIONS, EXIT_ACTIONS


def journal_and_link(trade: dict, cfg: dict) -> str | None:
    """Write a public journal entry for a trade event and return the link
    for the recap tweet. Cosmetic — never raises, never blocks trading."""
    try:
        entry = journal.add_entry(trade, cfg)
        journal.render(cfg)
        base = cfg.get("x_posting", {}).get("journal_url_base")
        if entry and base:
            return f"{base}#{entry['trade_id']}"
    except Exception as e:  # noqa: BLE001
        log.warning("journal failed: %s", e)
    return None

log = logging.getLogger("main")


def configure_logging() -> None:
    """Attach the file handlers. Called from __main__, never on import.

    This used to run at import time, and 14 test files import this module —
    so every pytest run appended the whole session's log records to the real
    `logs/agent.jsonl` (240 in a single run, measured). Two consequences, both
    bad: production diagnostics were buried under fixture output, and
    `logs/agent.log` stayed 0 bytes forever, because pytest attaches its own
    root handler before collection and `basicConfig()` is a silent no-op when
    the root logger already has handlers.

    The second one is why the 2026-07-24 crash could not be diagnosed: there
    was no stderr left to read. Same pattern as backfill_posts.py, which had
    it right all along.
    """
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler("logs/agent.log", mode="a")],
        force=True,       # own the root logger; basicConfig is a no-op without it
    )
    import log as structlog
    structlog.attach_json_handler()  # JSON mirror w/ secret redaction (Phase D)
    # ...and put the same redaction in front of the console + agent.log
    # handlers, which are the two a human actually opens in an incident.
    structlog.redact_existing_handlers()


def handle_close(trade_id: str, open_rec: dict, exit_price: float,
                 ledger: Ledger, memory: Memory, cfg: dict,
                 exit_reason: str = "strategy_sell",
                 bench_bars: list[dict] | None = None):
    """A position was exited: record outcome, learn, post.

    `bench_bars` are daily benchmark (SPY) bars covering the holding window.
    Optional: callers that have them pass them, callers that don't get an
    outcome record without alpha rather than a crash or a fabricated zero.
    """
    entry = open_rec.get("entry_price") or exit_price
    qty = open_rec.get("qty") or 0
    pnl = (exit_price - entry) * qty
    pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0

    # Alpha over the identical window. Best-effort by design: a benchmark
    # problem must never block recording the trade that actually happened.
    bench_pct = None
    try:
        import scorecard
        bench_pct = scorecard.benchmark_return_pct(
            bench_bars,
            open_rec.get("entry_ts") or open_rec.get("ts") or "",
            datetime.now(timezone.utc).isoformat())
    except Exception as e:  # noqa: BLE001
        log.warning("benchmark return for %s failed: %s", trade_id, e)

    ledger.close_trade(trade_id, exit_price, pnl, pnl_pct,
                       exit_reason=exit_reason, benchmark_pnl_pct=bench_pct)
    if bench_pct is None:
        log.info("Closed trade %s (%s): P&L $%.2f (%.2f%%)",
                 trade_id, exit_reason, pnl, pnl_pct)
    else:
        log.info("Closed trade %s (%s): P&L $%.2f (%.2f%%) | benchmark %+.2f%% "
                 "=> alpha %+.2f%%", trade_id, exit_reason, pnl, pnl_pct,
                 bench_pct, pnl_pct - bench_pct)

    # W7: write the realised result back against the news that preceded this
    # symbol. This is the half that makes news memory evidence rather than a
    # notepad — it produces the two populations a future §46 would compare
    # (entered with news present vs without). Best-effort, like the benchmark
    # above: bookkeeping never blocks recording the trade that happened.
    try:
        import news_memory
        news_memory.record_outcome(open_rec.get("symbol") or "", trade_id,
                                   pnl_pct, cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("news outcome for %s skipped: %s", trade_id,
                    type(e).__name__)

    closed = {**open_rec, "exit_price": exit_price, "pnl": round(pnl, 2),
              "pnl_pct": round(pnl_pct, 2), "result": "win" if pnl > 0 else "loss",
              "exit_reason": exit_reason}
    if bench_pct is not None:
        # Surfaced to the lesson generator: without it the judge learns that
        # riding a rising market was skill.
        closed["benchmark_pnl_pct"] = round(bench_pct, 2)
        closed["alpha_pct"] = round(pnl_pct - bench_pct, 2)
    lesson = llm.generate_lesson_structured(closed, memory.context_for_llm(), cfg)
    if lesson:
        lid = memory.lessons.add_lesson(lesson["hypothesis"], lesson["scope"],
                                        source=trade_id)
        ledger.log_event("lesson_created", f"{lid} from trade {trade_id}")

    recap = {**closed, "action": "sell"}
    if lesson:  # close recaps carry what the bot learned
        recap["lesson_hypothesis"] = lesson["hypothesis"]
    link = journal_and_link(recap, cfg)
    x_poster.post_recap(recap, cfg, llm.write_x_post(recap, cfg), link=link)


def resolve_exit_price(broker, open_rec: dict) -> tuple[float | None, str]:
    """Best-effort exit fill for a ledger-open trade whose symbol left the
    broker's positions. Preference: filled bracket leg -> recent closed SELL
    order -> latest daily close (flagged as an estimate)."""
    symbol = open_rec["symbol"]
    order = open_rec.get("order") or {}
    # A stop-only (OTO) entry has NO take-profit leg, so a filled leg can only
    # be the stop. Without this, a leg whose `type` the broker omitted fell
    # through to "take_profit" and corrupted exit-reason analytics.
    stop_only = (str(order.get("order_class", "")).lower() == "oto"
                 or order.get("take_profit_price") is None)

    def _leg_reason(leg_type: str | None) -> str:
        t = str(leg_type or "").lower()
        if "stop" in t:
            return "stop_loss"
        if "limit" in t:
            return "take_profit"
        return "stop_loss" if stop_only else "take_profit"   # type unavailable

    for leg_id in order.get("leg_ids", []):
        try:
            leg = broker.get_order(leg_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Leg lookup failed for %s: %s", leg_id, e)
            continue
        if "filled" in leg["status"].lower() and leg["filled_avg_price"]:
            return leg["filled_avg_price"], _leg_reason(leg.get("type"))
        for sub in leg.get("legs", []):
            if "filled" in sub["status"].lower() and sub["filled_avg_price"]:
                return sub["filled_avg_price"], _leg_reason(sub.get("type"))

    try:
        for o in broker.closed_orders(symbol):
            if o["side"].lower().endswith("sell") and o["filled_avg_price"]:
                reason = ("stop_loss" if "stop" in o["type"].lower()
                          else "take_profit" if "limit" in o["type"].lower()
                          else "closed_order")
                return o["filled_avg_price"], reason
    except Exception as e:  # noqa: BLE001
        log.warning("Closed-order lookup failed for %s: %s", symbol, e)

    try:
        price = broker.last_price(symbol)
        if price:
            return price, "last_price_estimate"
    except Exception as e:  # noqa: BLE001
        log.warning("Last-price fallback failed for %s: %s", symbol, e)
    return None, "unknown"


def reconcile_closed_positions(broker, ledger: Ledger, memory: Memory, cfg: dict,
                               positions: dict):
    """Cycle-start sync: ledger-open trades whose symbol is gone from the broker
    were exited broker-side (bracket leg fill, kill-switch flatten, or a manual
    close in the dashboard). Write their outcomes so open_buys() never desyncs.
    Every path writes a ledger record; errors never crash the cycle."""
    stale = {tid: rec for tid, rec in ledger.open_buys().items()
             if rec["symbol"] not in positions}

    # Benchmark bars for alpha attribution, fetched ONCE and only when there is
    # actually something to close. This runs before the cycle's bar fetch, so
    # without it every reconcile-path close — which is how most closes happen —
    # would record no alpha at all. A failure here costs the alpha field, never
    # the outcome record.
    bench_bars = None
    if stale:
        try:
            bench_bars = broker.bars("SPY", "1Day", 260)
        except Exception as e:  # noqa: BLE001
            log.warning("benchmark bars unavailable for reconcile: %s", e)

    for tid, rec in stale.items():
        symbol = rec["symbol"]
        try:
            entry_order = rec.get("order") or {}
            if entry_order.get("id"):
                try:
                    o = broker.get_order(entry_order["id"])
                    if o["filled_qty"] == 0 and "filled" not in o["status"].lower():
                        ledger.close_trade(tid, rec.get("entry_price") or 0.0, 0.0, 0.0,
                                           exit_reason="entry_unfilled")
                        ledger.log_event("reconcile", f"{symbol} trade {tid}: entry never filled")
                        continue
                except Exception as e:  # noqa: BLE001
                    log.warning("Entry-order lookup failed for %s: %s", tid, e)

            price, reason = resolve_exit_price(broker, rec)
            if price is None:
                ledger.log_event("reconcile_error",
                                 f"{symbol} trade {tid}: no exit price resolvable")
                continue
            log.info("Reconciling %s trade %s: closed broker-side (%s @ $%.2f)",
                     symbol, tid, reason, price)
            handle_close(tid, rec, price, ledger, memory, cfg,
                         exit_reason=reason, bench_bars=bench_bars)
        except Exception as e:  # noqa: BLE001 — reconciliation must never kill the cycle
            log.error("Reconcile failed for %s trade %s: %s", symbol, tid, e)
            ledger.log_event("reconcile_error", f"{symbol} trade {tid}: {e}")


def position_entry_ts(rec: dict) -> str | None:
    """When a position was actually ENTERED, for age-based rules (swing guard,
    max_hold_days, chandelier high-water). Prefers the recovered real fill time
    over the ledger write time — they differ for adopted positions, where
    stamping write-time would reset the holding clock and block a genuinely old
    position's exit for another min_holding_days."""
    return rec.get("entry_ts") or rec.get("ts")


def _recover_fill_ts(broker, symbol: str) -> str | None:
    """Real BUY fill time from the broker's closed orders (Alpaca's Position
    model carries no timestamp). Fail-open: None on any problem."""
    if broker is None:
        return None
    try:
        for o in broker.closed_orders(symbol):
            if str(o.get("side", "")).lower().endswith("buy") and o.get("filled_at"):
                return o["filled_at"]
    except Exception as e:  # noqa: BLE001 — adoption must never crash a cycle
        log.warning("Fill-time lookup failed for %s: %s", symbol, e)
    return None


def adopt_untracked_positions(broker, ledger: Ledger, cfg: dict,
                              positions: dict):
    """Complement to reconcile_closed_positions: a broker position with NO
    open ledger buy is a 'ghost' — an entry order that filled but whose ledger
    write never landed (a crash/kill between broker.submit and log_decision in
    _process_signal). Adopt it so it becomes tracked again: its P&L, the
    learning loop, judge calibration, and the public track record must never
    silently omit a real position. Entry price and qty come from the broker
    (the source of truth); the strategy defaults to the owner fallback so the
    normal owner-only exit path routes it. Errors never crash the cycle."""
    tracked = {rec["symbol"] for rec in ledger.open_buys().values()}
    for symbol, pos in positions.items():
        if symbol in tracked:
            continue
        try:
            qty = int(pos.get("qty") or 0)
            if qty <= 0:
                continue
            entry = pos.get("avg_entry")
            if not entry:  # defensive: derive from market value if absent
                entry = (pos.get("market_value", 0.0) / qty) if qty else 0.0
            fill_ts = _recover_fill_ts(broker, symbol)
            tid = ledger.log_decision(
                symbol, "buy", "adopted: broker position with no ledger record",
                {}, None, executed=True,
                order={"id": None, "symbol": symbol, "adopted": True},
                entry_price=float(entry), qty=qty,
                strategy=strategies.DEFAULT_OWNER,
                entry_ts=fill_ts,
                detail="adopted untracked broker position"
                       + (f" (real fill {fill_ts})" if fill_ts else
                          " (fill time unrecoverable — age measured from adoption)"))
            ledger.log_event("position_adopted",
                             f"{symbol}: qty {qty} @ {float(entry):.2f} "
                             f"(trade {tid})")
            log.warning("Adopted untracked %s position: qty %d @ $%.2f "
                        "(trade %s) — an entry filled but its ledger write was "
                        "lost", symbol, qty, float(entry), tid)
        except Exception as e:  # noqa: BLE001 — adoption must never crash the cycle
            log.error("Adoption failed for %s: %s", symbol, e)
            ledger.log_event("adopt_error", f"{symbol}: {e}")


def update_trailing_stops(broker, ledger, cfg: dict, open_trades: dict,
                          all_bars: dict):
    """Chandelier ratchet (gate candidate §7, param-gated by
    risk.brackets.trailing_atr_mult — 0 disables): raise each open
    position's resting stop leg to high-water-since-entry − mult·ATR when
    that sits above the current stop. Deterministic protective-leg
    maintenance, computed before any LLM involvement — the same class of
    broker-side exit the swing-guard invariant already exempts. Stops are
    only ever RAISED, never lowered or widened. Fail-soft per position."""
    if not (cfg["risk"].get("brackets") or {}).get("trailing_atr_mult", 0):
        return
    try:
        stops_open = {}
        for o in broker.open_stop_orders():  # survives leg replacement
            cur = stops_open.get(o["symbol"])
            if cur is None or o["stop_price"] > cur["stop_price"]:
                stops_open[o["symbol"]] = o
    except Exception as e:  # noqa: BLE001
        log.warning("trailing pass: open-order lookup failed: %s", e)
        return
    atr_period = cfg["risk"]["brackets"].get("atr_period", 14)
    for rec in open_trades.values():
        symbol = rec["symbol"]
        leg = stops_open.get(symbol)
        bars = all_bars.get(symbol)
        if not leg or not bars or not rec.get("entry_price"):
            continue
        try:
            entry_ts = position_entry_ts(rec) or ""
            high_water = max([rec["entry_price"]]
                             + [b["high"] for b in bars if b["ts"] > entry_ts])
            new_stop = risk.trail_stop(high_water,
                                       strategy.atr(bars, atr_period), cfg,
                                       strategy=rec.get("strategy"))
            if new_stop is None or new_stop <= leg["stop_price"] + 0.01:
                continue
            broker.replace_stop(leg["id"], new_stop)
            ledger.log_event("trail_stop_raised",
                             f"{symbol}: {leg['stop_price']:.2f} -> "
                             f"{new_stop:.2f} (hw {high_water:.2f})")
            log.info("%s: trailing stop raised %.2f -> %.2f",
                     symbol, leg["stop_price"], new_stop)
        except Exception as e:  # noqa: BLE001 — the old stop stays in force
            log.warning("trailing pass failed for %s: %s", symbol, e)


HEARTBEAT_FILE = "memory/heartbeat"


def write_heartbeat():
    """Proof-of-life for the watchdog: written on EVERY cycle exit path
    (normal, halted, stale-data abort, crash) — it means 'the process ran',
    not 'trading happened'."""
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FILE), exist_ok=True)
        with open(HEARTBEAT_FILE, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat() + "\n")
    except OSError as e:
        log.warning("heartbeat write failed: %s", e)


def record_fill_quality(broker, ledger: Ledger, max_checks: int = 20):
    """Measured slippage: the price the signal saw vs the broker's actual
    fill, appended once per trade as a fill_quality record. GUIDE §9's
    go-live comparison should use measured costs, not estimates.
    Bounded, idempotent, never raises."""
    try:
        records = ledger.all_records()
        have = {r["trade_id"] for r in records if r["type"] == "fill_quality"}
        candidates = [r for r in records
                      if r["type"] == "decision" and r.get("executed")
                      and (r.get("order") or {}).get("id")
                      and r.get("entry_price")
                      and r["trade_id"] not in have]
        for rec in candidates[-max_checks:]:
            try:
                o = broker.get_order(rec["order"]["id"])
            except Exception as e:  # noqa: BLE001
                log.warning("fill lookup failed for %s: %s", rec["trade_id"], e)
                continue
            fill = o.get("filled_avg_price")
            if not fill:
                continue  # not filled yet — retried next cycle
            signal = rec["entry_price"]
            sign = 1 if rec["action"] == "buy" else -1  # worse-than-signal is +
            bps = sign * (fill - signal) / signal * 1e4
            ledger.log_fill_quality(rec["trade_id"], rec["symbol"],
                                    rec["action"], signal, fill, bps)
    except Exception as e:  # noqa: BLE001 — instrumentation never kills a cycle
        log.error("fill-quality pass failed: %s", e)


def log_cycle_crash(exc: BaseException) -> None:
    """Leave a ledger record saying the cycle died, and roughly where.

    On 2026-07-24 the 15:45 cycle died about six seconds in, before its first
    ledger write. The result was a day with no decisions, no abort record and
    a perfectly fresh heartbeat — which read as a quiet market. The ledger is
    the only durable surface (stderr is captured by the scheduler and the log
    files had already rotated away by the time anyone looked), so the crash
    has to land there.

    Best-effort by construction: this runs while an exception is already in
    flight and must never replace it with one of its own.
    """
    import traceback
    try:
        import yaml
        with open("config.yaml") as fh:
            path = yaml.safe_load(fh)["memory"]["ledger_path"]
        tb = "".join(traceback.format_exception(
            type(exc), exc, exc.__traceback__))[-1200:]
        Ledger(path).log_event(
            "cycle_crashed",
            f"{type(exc).__name__}: {exc}\n{tb}"[:2000])
    except BaseException:  # noqa: BLE001 — the ledger may be the thing that broke
        log.critical("cycle crashed AND the crash could not be recorded: %r",
                     exc)


def run_cycle(completed_bars_only: bool = False):
    """One trading cycle.

    `completed_bars_only` drops today's still-forming daily bar before signals
    are computed (see datacheck.drop_forming_bar). The 15:45 cycle leaves it
    False — at 15 minutes to the close the forming bar is effectively the
    close, which is what every gate measured. The 09:35 open cycle sets it
    True, because a five-minute-old stub is not a daily bar."""
    completed = False
    try:
        completed = bool(_run_cycle(completed_bars_only=completed_bars_only))
    except BaseException as e:  # noqa: BLE001 — recorded, then re-raised
        # BaseException, not Exception: broker.py raises SystemExit when the
        # Alpaca keys are missing, and that is exactly the kind of death this
        # needs to catch. The record is written, then the exception continues
        # on its way so the exit code and the external ping stay truthful.
        log_cycle_crash(e)
        raise
    finally:
        write_heartbeat()
        # PUSH proof-of-life to an external monitor. The local heartbeat file
        # only helps if something on this host is still alive to read it; if
        # the container or the host dies, silence looks exactly like a quiet
        # market. An outside observer alerting on missing pings is the only
        # check that survives that. No-op unless HEARTBEAT_PING_URL is set.
        #
        # `completed`, not "did not raise". The old flag was set on every
        # deliberate early return too, so a HALT day, a kill-switch day and a
        # stale-data abort all told the external monitor they had succeeded.
        # A cycle that did not trade is not a cycle that worked.
        try:
            import alerting
            alerting.heartbeat_ping(success=completed)
        except Exception as e:  # noqa: BLE001 — never break a cycle to alert
            log.warning("heartbeat ping failed: %s", e)


def check_deploy_drift(ledger: Ledger) -> dict:
    """Is the running code the reviewed code? (§26 divergence #7, 2026-07-25)

    Returns the deploycheck status so the caller can stamp the sha on
    `cycle_complete` — drift stays reconstructable from the ledger even if no
    alert ever fired.

    ONE alert per day. A per-cycle alert on a condition that persists for days
    is how an alert channel gets muted, and a muted channel is worse than none.

    Fail-OPEN and non-blocking, unlike preflight: stale gated params are wrong
    but not unsafe, and halting the bot over them would cost a trading day to
    fix a reporting problem.
    """
    try:
        import deploycheck
        st = deploycheck.status()
        msg = deploycheck.drift_message(st)
        if not msg:
            return st
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        already = any(r.get("event") == "deploy_drift"
                      and (r.get("ts") or "")[:10] == today
                      for r in ledger.all_records() if r.get("type") == "event")
        if already:
            return st
        # A 'degradation' too: this is a guard reporting that it cannot
        # vouch for what is running, and review.py already counts those.
        ledger.log_event("degradation", msg)
        ledger.log_event("deploy_drift", msg)
        try:
            import alerting
            alerting.send("trading-agent: deployment drift", msg)
        except Exception:  # noqa: BLE001
            pass
        log.warning("%s", msg)
        return st
    except Exception as e:  # noqa: BLE001 — monitoring never kills a cycle
        log.warning("deploy drift check failed: %s", e)
        return {}


def check_degradation_slo(ledger: Ledger, cfg: dict):
    """Ops error budget (2026-07-21): count today's ledgered fail-open
    'degradation' events; at/over ops.max_degradations_per_day, write ONE
    slo_breach event and raise a desktop alert. Never raises, never HALTs —
    escalation to a human, not to the kill switch."""
    try:
        limit = int((cfg.get("ops") or {}).get("max_degradations_per_day", 3))
        if limit <= 0:
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        events = [r for r in ledger.all_records()
                  if r.get("type") == "event"
                  and (r.get("ts") or "")[:10] == today]
        n = sum(1 for r in events if r.get("event") == "degradation")
        if n < limit or any(r.get("event") == "slo_breach" for r in events):
            return
        ledger.log_event("slo_breach",
                         f"{n} degradation events today >= limit {limit} — "
                         f"guards are running fail-open too often")
        try:
            from watchdog import notify
            notify("trading-agent: degradation SLO breach",
                   f"{n} fail-open events today (limit {limit}) — check "
                   f"data feeds and logs")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001 — monitoring never kills a cycle
        log.warning("SLO check failed: %s", e)


def _bootstrap_cycle():
    """Config, preflight, stores, HALT check, broker state.

    Returns `(cfg, ledger, memory, broker, account, positions, halted)`, or
    **None** when the cycle must not proceed — a preflight failure or a HALT in
    `freeze` mode. `None` is the abort signal rather than an exception because
    both cases are ORDINARY: a misconfigured system and a deliberately halted
    one are things the operator did, not faults to raise through.

    `halted` is True under a HALT in `exits` mode: the cycle RUNS, works its
    exits, and opens nothing. See risk.halt_mode for why the two are separate
    instructions rather than one flag.

    Extracted from `_run_cycle` in W4-7 (2026-07-29).
    """
    load_dotenv()
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)

    # Pre-flight: a misconfigured system FAILS SAFE (opposite polarity from
    # data outages, which degrade gracefully). Nothing trades past this.
    fails = preflight.run(cfg)
    if fails:
        for f_msg in fails:
            log.critical("PREFLIGHT: %s", f_msg)
        try:
            Ledger(cfg["memory"]["ledger_path"]).log_event(
                "preflight_failure", "; ".join(fails)[:500])
        except Exception:  # noqa: BLE001 — even the ledger may be the problem
            pass
        try:
            from watchdog import notify
            notify("trading-agent PREFLIGHT FAILED",
                   fails[0][:120] + (" (+more)" if len(fails) > 1 else ""))
        except Exception:  # noqa: BLE001
            pass
        return None

    # Storage backend is chosen ONCE, before any store is constructed.
    store.configure(cfg)

    ledger = Ledger(cfg["memory"]["ledger_path"])
    # Decision-surface fingerprint: every record this cycle carries the
    # rulebook version, so the track record segments honestly by model.
    ledger.set_model_version(modelver.current_version())
    memory = Memory(cfg, ledger)

    # Two halts, two behaviours. `freeze` is the original: nothing runs, which is
    # what you want when the bot or the broker is itself suspect. `exits` runs
    # the cycle with entries blocked so open positions are still managed —
    # before 2026-08-02 that was only ever true of the broker's own bracket
    # legs, while halt.py's docstring claimed otherwise.
    halted = False
    mode = risk.halt_mode()
    if mode == risk.HALT_MODE_FREEZE:
        # One free recovery attempt per cycle, on top of the dedicated
        # flatten-retry job. Costs nothing when nothing is pending (a substring
        # check on a file that does not exist), and covers the 09:35 open —
        # which the retry job deliberately does not, because a market order
        # before the bell would be rejected and would burn an attempt on
        # something that is not the outage.
        if risk.flatten_pending():
            try:
                outcome = flatten_recovery.run(Broker(cfg), ledger, cfg)
                log.critical("HALT (freeze) with a pending flatten — "
                             "recovery: %s", outcome)
            except Exception as e:  # noqa: BLE001 — never block the halt itself
                log.critical("flatten recovery raised: %s", e)
        log.critical("HALT (freeze) — refusing to trade. Delete HALT to resume.")
        ledger.log_event("halted_cycle_skipped")
        return None
    if mode == risk.HALT_MODE_EXITS:
        halted = True
        log.critical("HALT (exits) — entries blocked; still working exits. "
                     "Delete HALT to resume normal trading.")
        # A DISTINCT event from halted_cycle_skipped. Collapsing them would make
        # the ledger unable to answer "did the bot do anything while halted?",
        # which is the first question anyone asks after a halt.
        ledger.log_event("halted_exits_only")

    broker = Broker(cfg)
    account = broker.account()          # deterministic state: always from the broker,
    positions = broker.positions()      # never from memory or prior LLM output.
    log.info("Equity: $%.2f | Positions: %s", account["equity"], list(positions) or "none")
    return cfg, ledger, memory, broker, account, positions, halted


# In-cycle flatten retries, and the pause between them. Small and fixed rather
# than configurable: this runs inside a trading cycle, so the total worst-case
# delay it can add (2 x 2s) has to stay well under anything that could push a
# fill past the close. Sustained outages are the scheduled job's problem, not
# this loop's.
_KILL_SWITCH_INCYCLE_TRIES = 3
_KILL_SWITCH_BACKOFF_SEC = 2.0


def _kill_switch_fired(broker, ledger: Ledger, account: dict, cfg: dict) -> bool:
    """The HARD stop: flatten the book and engage HALT on a daily-loss breach.

    Returns True when it fired and the cycle must end. Distinct from the
    drawdown breaker, which only blocks entries — see config.yaml's
    `max_drawdown_pct` note for why the two are deliberately not merged.

    Extracted from `_run_cycle` in W4-7 (2026-07-29), behaviour unchanged.
    """
    if not risk.daily_loss_breached(account, cfg):
        return False
    # Engage HALT and record the breach BEFORE attempting the flatten. A
    # broker error while closing (timeout/API outage) must not leave the
    # daily-loss breach un-halted and unrecorded — otherwise the next
    # scheduled cycle re-enters this path instead of being HALT-blocked.
    # FREEZE, explicitly, and this is load-bearing rather than a default.
    # The comment above depends on HALT stopping the next cycle. Under
    # `exits` the cycle WOULD run again, re-enter this function while the
    # daily loss still stands, and re-call flatten_all() every cycle — and
    # return early before any exit ran, so it would not even buy the exits
    # that mode exists for. There is also nothing to exit: the book was just
    # flattened.
    risk.engage_halt("daily loss limit breached — flattening all positions",
                     mode=risk.HALT_MODE_FREEZE)
    ledger.log_event("kill_switch",
                     "daily loss limit breached; HALT engaged; flattening")

    # Retry in-cycle before giving up on this run. The overwhelmingly common
    # failure is a transient API blip, and the whole point of a kill switch is
    # that you want out NOW — waiting 15 minutes for the scheduled retry when a
    # second call would have worked is exposure bought for nothing.
    #
    # Success is decided by RE-READING THE BOOK, not by the absence of an
    # exception. flatten_all() is cancel_orders() + close_all_positions(), and
    # Alpaca's close-all reports per-position results rather than raising when
    # only some of them close — so until 2026-08-02 a flatten that closed 3 of 5
    # positions was recorded as complete and the two still open were invisible.
    # See flatten_recovery.flatten_and_verify.
    remaining = {}
    for attempt in range(1, _KILL_SWITCH_INCYCLE_TRIES + 1):
        remaining = flatten_recovery.flatten_and_verify(broker)
        if not remaining:
            break
        log.critical("flatten attempt %d left %d position(s) open: %s",
                     attempt, len(remaining), ", ".join(sorted(remaining)))
        if attempt < _KILL_SWITCH_INCYCLE_TRIES:
            time.sleep(_KILL_SWITCH_BACKOFF_SEC)

    if remaining:
        names = ", ".join(sorted(remaining))
        # `kill_switch_flatten_failed` KEEPS ITS NAME. docs/runbooks.md tells the
        # operator to grep for exactly this string, and renaming it would
        # silently break a documented incident command — the test_runbook_
        # accuracy.py lesson: a documented check that is not a check.
        ledger.log_event("kill_switch_flatten_failed",
                         f"positions still open after "
                         f"{_KILL_SWITCH_INCYCLE_TRIES} attempts: {names}")
        risk.mark_flatten_pending(f"still open: {names}")
        # Until now the most dangerous state in the system wrote one ledger line
        # and paged nobody.
        flatten_recovery.alert(
            "Repete: KILL SWITCH could not flatten",
            f"The daily-loss kill switch fired and these positions are STILL "
            f"OPEN:\n\n  {names}\n\nAutomatic retries continue on the "
            f"flatten-retry schedule. Clearing HALT cancels them.")
    return True


def _market_context(cfg: dict, broker, ledger: Ledger, positions: dict):
    """Today's news context, the nominated watchlist, and the scan universe.

    Returns `(news_ctx, nominated, scan_symbols)`. Fail-soft throughout: a
    missing or unrefreshable context means the feature is off for this cycle,
    never that the cycle stops. A NOMINATION IS NOT A TRADE — entries still
    need a deterministic strategy signal, the judge, and every rail.

    Extracted from `_run_cycle` in W4-7 (2026-07-29), behaviour unchanged.
    """
    import market_context as market_context_mod
    news_ctx = market_context_mod.load(cfg) or {}
    # Missed-run resilience (2026-07-21): if every hourly news-brain fire was
    # missed today (machine asleep, job dead), self-heal with ONE inline
    # refresh instead of trading blind on news. Fail-soft: any error means
    # no context, exactly as before.
    if not news_ctx and cfg.get("news", {}).get("enabled", False):
        try:
            log.info("no market context today — inline news-brain catch-up")
            news_ctx = market_context_mod.refresh(cfg, broker, ledger=ledger) or {}
        except Exception as e:  # noqa: BLE001 — catch-up must never block the cycle
            log.warning("inline news catch-up failed: %s", e)
            ledger.log_event("degradation",
                             f"news_catchup: inline refresh failed ({e}) — "
                             f"cycle ran without market context")
            news_ctx = {}
        else:
            if not news_ctx:
                ledger.log_event("degradation",
                                 "news_catchup: refresh returned no context — "
                                 "cycle ran without market context")
    nominated = {n["symbol"]: n.get("reason", "")
                 for n in news_ctx.get("nominations", [])
                 if n["symbol"] not in cfg["symbols"]}
    if nominated:
        log.info("News-nominated watchlist today: %s", sorted(nominated))

    # Scan universe: config symbols + today's nominations + any open-position
    # symbol not otherwise covered (exits must always be scanned, even for
    # symbols since removed from config or entered via a past nomination).
    # §24: rotate the universe by date so first refusal on scarce slots
    # circulates instead of permanently favouring whatever sits at the top of
    # config.yaml. Extras are appended AFTER the rotation — held positions must
    # always be scanned for exits, and their order is not a contended resource.
    scan_symbols = risk.scan_order(list(cfg["symbols"]), cfg)
    scan_symbols += [s for s in nominated if s not in scan_symbols]
    scan_symbols += [s for s in positions if s not in scan_symbols]
    return news_ctx, nominated, scan_symbols


def _fetch_and_validate_bars(broker, cfg: dict, ledger: Ledger,
                             scan_symbols: list, completed_bars_only: bool):
    """Fetch every symbol's bars, apply the data rails, and read the regime.

    Returns `(all_bars, entries_blocked_reason, market_regime, regime_label)`,
    or **None** when the cycle must abort because SPY itself is stale.

    Three distinct failure polarities live here, deliberately:
      * a single symbol's fetch RAISES -> skip that symbol, log, carry on
      * a single symbol is STALE       -> drop that symbol
      * SPY is STALE                   -> abort the cycle (None)
      * the two vendors DISAGREE       -> block ENTRIES only; exits still run

    Extracted from `_run_cycle` in W4-7 (2026-07-29), behaviour unchanged.
    """
    # Ensemble needs the full cross-section; lookback sized to the most
    # demanding strategy.
    lookback = strategies.max_lookback_bars(cfg)
    all_bars: dict = {}
    for symbol in scan_symbols:
        try:
            fetched = broker.bars(symbol, cfg["strategy"]["timeframe"], lookback)
            if completed_bars_only:
                fetched = datacheck.drop_forming_bar(fetched)
            if fetched:
                all_bars[symbol] = fetched
        except Exception as e:  # noqa: BLE001 — data failure: skip symbol, log it
            log.error("Data fetch failed for %s: %s", symbol, e)
            ledger.log_event("data_error", f"{symbol}: {e}")

    # --- Freshness guard: never trade on stale data (deterministic rail).
    # SPY stale => the whole feed is suspect: abort the cycle. A single
    # stale symbol => drop just that symbol. ---
    max_age = cfg["risk"].get("max_bar_age_days", 4)
    if not risk.bars_fresh(all_bars.get("SPY", []), max_age):
        last_ts = all_bars["SPY"][-1]["ts"] if all_bars.get("SPY") else "none"
        log.critical("STALE DATA — newest SPY bar is %s (max age %dd). "
                     "Refusing to trade this cycle.", last_ts, max_age)
        ledger.log_event("stale_data_abort",
                         f"newest SPY bar {last_ts}, max_bar_age_days={max_age}")
        return None
    for symbol in list(all_bars):
        if not risk.bars_fresh(all_bars[symbol], max_age):
            ledger.log_event("data_stale",
                             f"{symbol}: newest bar {all_bars[symbol][-1]['ts']}")
            log.warning("%s: stale bars — symbol skipped this cycle", symbol)
            del all_bars[symbol]

    # Second-vendor cross-check (2026-07-21): fresh-LOOKING bars can still be
    # wrong (the 07-16 class). If Alpaca and yfinance disagree on SPY's close,
    # one is lying and we can't know which — entries are blocked this cycle
    # (exits and protective actions never are).
    entries_blocked_reason = datacheck.crosscheck_spy(all_bars.get("SPY", []),
                                                      cfg)
    if entries_blocked_reason:
        ledger.log_event("degradation", entries_blocked_reason)
        log.critical("%s", entries_blocked_reason)

    # Market regime (deterministic, from SPY bars already fetched): tagged onto
    # every decision/judgment so the learning loop can discount off-regime evidence.
    market_regime = regime_mod.compute_regime(all_bars.get("SPY", []),
                                              cfg["learning"]["regime"])
    regime_label = market_regime["label"] if market_regime else None
    log.info("Regime: %s", regime_mod.describe(market_regime))
    return all_bars, entries_blocked_reason, market_regime, regime_label


def _precompute(cfg: dict, all_bars: dict, open_trades: dict,
                positions: dict) -> tuple[dict, dict, dict]:
    """Once-per-cycle work that the per-symbol loop reads but must not repeat.

    Returns `(xs_ctx, earnings_blackouts, blackout_days)`.

    `blackout_days` is returned rather than kept local because the per-symbol
    loop quotes the day count in its hold reason ("earnings within 3d"). The
    first draft of this extraction dropped it and ruff caught the undefined
    name in the closure before any test ran — which is what W4-2 enabled the F
    rules for.

    Two details here are easy to break and both are deliberate:
      * the cross-section includes owners of OPEN positions even when their
        strategy is disabled — otherwise a disabled strategy's exits stop
        working and the book is stranded;
      * the earnings calendar FAILS OPEN. Losing a filter is not losing a
        cycle, and only NEW entries are ever blocked.

    Extracted from `_run_cycle` in W4-7 (2026-07-29), behaviour unchanged.
    """
    open_owners = {rec.get("strategy") or strategies.DEFAULT_OWNER
                   for rec in open_trades.values()}
    xs_ctx = strategies.prepare_cross_sections(cfg, all_bars,
                                               extra_owners=open_owners)

    # PER-STRATEGY param, off by default: block NEW entries in names reporting
    # within that strategy's N days. Deterministic, computed before the loop;
    # exits are never blocked. Per-strategy because the gate evidence split: it
    # helps trend entries (tsmom) and hurts dip-buying (meanrev).
    earnings_blackouts: dict = {}   # strategy name -> set of blacked-out syms
    ebd = {name: params.get("earnings_blackout_days", 0)
           for name, params in strategies.enabled(cfg)}
    if any(ebd.values()):
        try:
            import earnings
            flat = [s for s in cfg["symbols"] if s not in positions]
            for name, days in ebd.items():
                if days:
                    earnings_blackouts[name] = earnings.blackout_symbols(
                        flat, days)
                    if earnings_blackouts[name]:
                        log.info("Earnings blackout [%s, %dd]: %s", name,
                                 days, sorted(earnings_blackouts[name]))
        except Exception as e:  # noqa: BLE001 — filter loss ≠ cycle loss
            log.warning("earnings blackout unavailable (%s) — continuing", e)
    return xs_ctx, earnings_blackouts, ebd


def _finalize_cycle(cfg: dict, ledger: Ledger, memory, broker, account: dict,
                    positions: dict, all_bars: dict, regime_label) -> None:
    """Everything after the last trading decision: learn, measure, stamp,
    publish.

    Nothing here may abort the cycle, and nothing here may change a trading
    decision — the orders are already placed. Every step is either measurement
    or presentation, and the two that touch the outside world are wrapped
    because a cosmetic failure must never turn a completed cycle into a crashed
    one.

    ORDER IS LOAD-BEARING: `cycle_complete` is written AFTER the learning and
    measurement passes and BEFORE the page render. The watchdog keys off that
    event (HEARTBEAT.md), so stamping it earlier would let a cycle that died
    mid-learning report success — the 2026-07-24 incident in reverse.

    Extracted from `_run_cycle` in W4-7 (2026-07-29), behaviour unchanged.
    """
    # --- Learning pass: evaluate fresh closes, resolve due judgments,
    # apply lifecycle transitions. Bounded, embargoed, never raises. ---
    summary = learn.inline_pass(ledger, memory.lessons, memory.judgments, cfg,
                                broker=broker)
    if any(summary.values()):
        log.info("Learning: %s", summary)

    # Post-exit runner tracking: measure what happened AFTER each close
    # (bounded bar fetches; measurement only — see src/postexit.py).
    pe = postexit.run(ledger, broker, cfg)
    if any(pe.values()):
        log.info("Post-exit tracking: %s", pe)

    # Which code just traded? Stamped on every cycle so §26 divergence #7 —
    # production silently 57 commits behind — is reconstructable from the
    # ledger alone, with or without an alert having fired.
    _deploy = check_deploy_drift(ledger)

    # What the open book is worth right now, for the dashboard. `positions` is
    # the fresh broker read this cycle already made (invariant #4) — no extra
    # API call. Display only; never read back into a trading decision.
    try:
        ledger.log_positions_mark(positions)
    except Exception as e:  # noqa: BLE001 — a cosmetic snapshot never kills a cycle
        log.warning("positions mark failed: %s", e)

    import json as _json
    ledger.log_event("cycle_complete",
                     _json.dumps({"equity": account["equity"],
                                  "n_positions": len(positions),
                                  "regime": regime_label,
                                  "sha": _deploy.get("sha"),
                                  "config_dirty": _deploy.get("config_dirty"),
                                  "behind": _deploy.get("behind")}))

    # Degradation SLO: too many fail-open events in one day means the ops
    # error budget is burned — escalate to a human (alert only; HALT stays
    # reserved for the daily-loss kill switch).
    check_degradation_slo(ledger, cfg)
    try:  # page regeneration is cosmetic — never touches the cycle
        import blog
        import dashboard
        dashboard.render(cfg, spy_bars=all_bars.get("SPY"))
        blog.render(cfg)
        # journal.html too (2026-07-28). It used to be rendered ONLY from
        # journal_and_link(), i.e. only when a trade fired, so a stale page had
        # no way to repair itself on a quiet day — and one did not: the
        # published journal showed a single entry, for a trade_id absent from
        # every current store, while memory/journal.jsonl held 17. Rebuilding
        # from the store every cycle makes the page self-correcting.
        journal.render(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("page render failed: %s", e)


def _run_cycle(completed_bars_only: bool = False):
    started = _bootstrap_cycle()
    if started is None:
        return
    cfg, ledger, memory, broker, account, positions, halted = started

    # --- DIVERGENCE #11: ratchet the equity peak ONCE PER CYCLE ---
    #
    # §31 put `update_high_water` inside `risk.pre_trade_checks`, which is only
    # ever called from ONE place (the order loop below) and only when an order
    # is actually attempted. So a cycle that generates no buy and no sell never
    # touched the high-water mark at all.
    #
    # Why that matters, given the peak only ever ratchets UP: equity earned on
    # a quiet day is invisible to it. The book drifts up over a week of holds,
    # nobody trades, the peak stays at last week's value — and then the first
    # 10% drawdown is measured from a stale low peak, so the breaker fires LATE
    # (or, symmetrically, a real high never registers and the bot sits closer to
    # the rail than its equity says it should).
    #
    # It is the same class of error as divergence #10 one level up: the
    # simulator ratcheted only inside the buy branch, live ratchets only inside
    # an order attempt. §40 found the sim version; this is its live twin, and it
    # bites HARDEST on exactly the book this bot runs — 38 symbols with whole
    # days of no entries — while being invisible on the 500-symbol snapshots
    # where something trades on virtually every bar.
    #
    # Reading fresh from the broker (invariant #4), before any decision, is the
    # only placement that makes the mark independent of what the cycle decides
    # to do. `pre_trade_checks` still ratchets per order; this is not a
    # replacement for it but the floor under it, and update_high_water is
    # idempotent-by-max so running both is a no-op on the second call.
    if cfg["risk"].get("max_drawdown_pct"):
        _peak = risk.update_high_water(account["equity"])
        log.info("Equity peak: $%.2f (drawdown %.2f%%)",
                 _peak, risk.drawdown_pct(account["equity"], _peak))

    # --- Kill switch: daily loss limit ---
    if _kill_switch_fired(broker, ledger, account, cfg):
        return

    # Sync broker-side exits (bracket leg fills, flattens, manual closes)
    # into the ledger BEFORE reading open trades.
    reconcile_closed_positions(broker, ledger, memory, cfg, positions)
    # Complement: a broker position with no open ledger buy is a ghost from a
    # crash between order submit and the ledger write — adopt it so it is
    # tracked before we read open trades below.
    adopt_untracked_positions(broker, ledger, cfg, positions)
    record_fill_quality(broker, ledger)

    open_trades = ledger.open_buys()    # trade_id -> record, for closing P&L

    # --- Today's market context (news): judge context + validated watchlist
    # nominations, and the scan universe derived from them. ---
    if halted:
        # Every product of _market_context feeds ENTRIES: news context is judge
        # input for a buy, nominations ARE entry candidates, and the wide scan
        # universe exists to find them. Under an exits-halt none of it can
        # execute, so running it would spend an LLM budget and a news fetch on
        # decisions that are already refused — during a period the operator has
        # declared abnormal, which is the worst time to be doing optional work.
        #
        # SPY is not optional: _fetch_and_validate_bars aborts the cycle on a
        # stale SPY, and it is the benchmark handle_close scores against.
        news_ctx, nominated = {}, {}
        scan_symbols = list(positions)
        if "SPY" not in scan_symbols:
            scan_symbols.append("SPY")
        log.info("HALT (exits): scanning %d held symbol(s) for exits only",
                 len(positions))
    else:
        news_ctx, nominated, scan_symbols = _market_context(
            cfg, broker, ledger, positions)

    market_regime = None   # computed after the ensemble bar fetch (from SPY bars)
    regime_label = None

    def _would_be_brackets(sig, bars, price):
        """Deterministic bracket snapshot for blocked ENTRIES, so the
        counterfactual later replays the same protective exits."""
        if sig.action not in ENTRY_ACTIONS:
            return None, None
        bcfg = cfg["risk"].get("brackets", {})
        prices = risk.bracket_prices(
            price, strategy.atr(bars, bcfg.get("atr_period", 14)), cfg,
            vol_bucket=(market_regime or {}).get("vol"),
            # A short's bracket geometry is INVERTED (stop above entry).
            # Without this the counterfactual would record a long's stop
            # against a refused short, and every judge-calibration number
            # measured off that judgment would be scored against protective
            # exits that could never have been placed. `sig.action` is
            # exactly "buy" or "short" here — the guard above proved it.
            direction=sig.action)
        return prices if prices else (None, None)

    def _process_signal(sig, symbol, bars, price, entry_ts, open_rec,
                        extra_context: str = "", detail_tag: str = "") -> str:
        """One actionable signal through the unchanged pipeline:
        LLM review -> rails -> leg cancel -> execute -> ledger/judgment/recap.
        Returns 'executed' or 'blocked'."""
        # Vendor cross-check verdict (set after the bars fetch): entries are
        # blocked before the LLM even looks — deterministic, and no judge can
        # override a data-integrity stop. Exits pass through untouched.
        if sig.action in ENTRY_ACTIONS and entries_blocked_reason:
            tid = ledger.log_decision(
                symbol, sig.action, sig.reason, sig.indicators, None,
                executed=False,
                detail=f"risk rejection: {entries_blocked_reason[:180]}",
                # Which of the two entry blocks this was: "datacheck" for the
                # vendor divergence this guard was built for (named for its
                # runbook entry, "Vendor divergence (datacheck blocking
                # entries)"), or "halt" for an operator halt in exits mode.
                # Neither is a RiskRejection — this guard is inline — but both
                # ARE rails from the ledger's point of view, and a `rail` field
                # that covered only the rails that happen to raise would read as
                # complete while silently omitting three of them.
                rail=entries_blocked_rail,
                regime=regime_label, strategy=sig.strategy)
            memory.judgments.log_judgment(
                tid, symbol, sig.action, "rails_reject", 1.0, price,
                regime_label, kind="rails", executed=False,
                reasoning=entries_blocked_reason[:200], strategy=sig.strategy)
            return "blocked"
        review = llm.review_signal(
            sig, memory.context_for_llm(symbol=symbol, regime=market_regime,
                                        strategy=sig.strategy, signal=sig,
                                        positions=positions, account=account)
            + (f"\n\n{extra_context}" if extra_context else ""), cfg)
        if review.get("degraded"):
            # The judge was UNREACHABLE, not permissive. Ledger it as a
            # degradation so the SLO counts it and review.py can distinguish
            # "approved" from "approved because the judge was down".
            #
            # `degraded_reason` (2026-07-27) names WHICH failure — absent_key,
            # api or parse. They call for opposite responses, and the old
            # record could not tell them apart: for an absent key it printed
            # the bare `True`.
            ledger.log_event(
                "degradation",
                f"llm_judge[{review.get('degraded_reason', 'unknown')}]: review "
                f"unavailable for {symbol}, proceeding rule-based "
                f"({review['degraded']})")

        # llm.on_unavailable: block — refuse the ENTRY the judge could not judge.
        #
        # ENTRIES ONLY, deliberately. An exit that cannot be reviewed must still
        # run: the judge's only permitted effect is to SHRINK risk, so blocking
        # a sell because the reviewer is down would TRAP an open position and
        # enlarge risk through the reviewer's absence — the precise inversion
        # invariant 2 exists to prevent.
        #
        # Recorded as kind="degraded", never "veto". The judge vetoed nothing;
        # it was never reached. Writing this to the judgment ledger as a veto
        # would attribute a decision to a model that never made one, and every
        # calibration measured off that ledger would inherit the lie.
        if sig.action in ENTRY_ACTIONS and review.get("unavailable_block"):
            blocked_reason = (
                f"judge unavailable "
                f"({review.get('degraded_reason', 'unknown')}) and "
                f"llm.on_unavailable=block")
            tid = ledger.log_decision(
                symbol, sig.action, sig.reason, sig.indicators, review,
                executed=False, detail=blocked_reason,
                regime=regime_label, strategy=sig.strategy)
            memory.judgments.log_judgment(
                tid, symbol, sig.action, "degraded_block", 1.0, price,
                regime_label, kind="degraded", executed=False,
                reasoning=blocked_reason, strategy=sig.strategy)
            log.warning("%s: %s blocked — %s", symbol, sig.action.upper(),
                        blocked_reason)
            return "blocked"

        if review["verdict"] == "veto":
            tid = ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                      review, executed=False, detail="LLM veto",
                                      regime=regime_label, strategy=sig.strategy)
            stop, tp = _would_be_brackets(sig, bars, price)
            memory.judgments.log_judgment(
                tid, symbol, sig.action, "veto", review.get("scale", 1.0),
                price, regime_label, kind="llm", executed=False,
                reasoning=review.get("reasoning", ""),
                stop_price=stop, tp_price=tp, strategy=sig.strategy,
                cited_lessons=review.get("cited_lessons"),
                confidence=review.get("confidence"))
            log.info("%s: %s VETOED — %s", symbol, sig.action, review["reasoning"])
            return "blocked"

        # --- Hard risk rails (not overridable) ---
        # Protective bracket prices are computed BEFORE sizing (deterministic,
        # ATR + config — the LLM never sees them): stop-distance sizing (§8,
        # param-gated) needs the stop, and the same prices are reused at
        # execution so the sized risk and the placed stop always match.
        bracket_prices = None
        if sig.action in ENTRY_ACTIONS:
            bcfg = cfg["risk"].get("brackets", {})
            # `direction=sig.action` on both calls. Inside this branch
            # `sig.action` is exactly "buy" or "short" (ENTRY_ACTIONS), which
            # is precisely the vocabulary risk.py's `direction` parameter
            # takes, and for a buy it is byte-identical to the default.
            #
            # bracket_prices: a short's stop sits ABOVE entry. Sizing reads
            # bracket_prices[0] as its stop below, so passing direction to one
            # and not the other would hand stop-distance sizing a stop on the
            # wrong side of price — which risk._risk_sizing_active refuses,
            # silently dropping the short back to notional sizing.
            bracket_prices = risk.bracket_prices(
                price, strategy.atr(bars, bcfg.get("atr_period", 14)), cfg,
                vol_bucket=(market_regime or {}).get("vol"),
                direction=sig.action)
            full_qty = risk.size_order(account, price, cfg, bars=bars,
                                       strategy=sig.strategy,
                                       stop_price=bracket_prices[0]
                                       if bracket_prices else None,
                                       direction=sig.action)
            qty = int(full_qty * review["scale"])
            # Whole-share truncation can silently delete an order. Both causes
            # used to surface as risk.py's "account too small for caps", which
            # is plainly false on a six-figure account and hid a growing leak.
            # Skipping is correct (the judge may only SHRINK — rounding a
            # 0.5-share intent up to 1 would trade MORE than it sanctioned);
            # only the reporting was wrong.
            if qty <= 0:
                if full_qty <= 0:
                    why = (f"position sizing yields 0 shares: {sig.strategy} "
                           f"sizing budget is below one share at ${price:,.2f} "
                           f"— raise the sizing lever for this strategy or drop "
                           f"the symbol")
                else:
                    why = (f"LLM downsize x{review['scale']} truncated a "
                           f"{full_qty}-share order to 0 at ${price:,.2f} "
                           f"(whole shares only) — trade skipped, not resized up")
                tid = ledger.log_decision(
                    symbol, sig.action, sig.reason, sig.indicators, review,
                    executed=False, detail=f"risk rejection: {why}",
                    # Distinct from pure_checks' `zero_qty`: that one means the
                    # account cannot afford the caps, this one means the judge's
                    # downsize truncated the order away. Same outcome, different
                    # cause, and collapsing them would hide which lever to pull.
                    rail="downsize_zero_qty",
                    regime=regime_label, strategy=sig.strategy)
                # Still a rails rejection: the judge calibration scoreboard must
                # see it, exactly as every other rails block is recorded.
                stop, tp = _would_be_brackets(sig, bars, price)
                memory.judgments.log_judgment(
                    tid, symbol, sig.action, "rails_reject", 1.0, price,
                    regime_label, kind="rails", executed=False, reasoning=why,
                    stop_price=stop, tp_price=tp, strategy=sig.strategy)
                log.warning("%s: %s", symbol, why)
                return "blocked"
        else:
            # abs(): a broker reports a SHORT's qty NEGATIVE (src/broker.py
            # copies Alpaca's `float(p.qty)` verbatim, and the in-cycle view
            # below now matches it). Without abs() a "cover" would compute a
            # negative qty, `pure_checks` would raise `zero_qty`, and the
            # short would be TRAPPED — a rail refusing the exit is the risk-
            # enlarging inversion this codebase refuses everywhere else. A
            # long's qty is already positive, so abs() is a no-op on it.
            qty = abs(int(positions.get(symbol, {}).get("qty", 0)))  # full exit

        try:
            if sig.action in ENTRY_ACTIONS:
                kill = risk.live_kill_blocked(ledger.closed_trades(),
                                              sig.strategy, cfg)
                if kill:
                    raise risk.RiskRejection(kill)
            risk.pre_trade_checks(sig.action, symbol, qty, price, account,
                                  positions, cfg, entry_ts=entry_ts,
                                  regime_label=regime_label,
                                  bars_map=all_bars, open_trades=open_trades,
                                  candidate_stop=(bracket_prices[0]
                                                  if bracket_prices else None),
                                  strategy=sig.strategy)
        except risk.RiskRejection as e:
            tid = ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                      review, executed=False,
                                      detail=f"risk rejection: {e}",
                                      # `rail` defaults to "unattributed" in
                                      # RiskRejection.__init__, so the bare
                                      # raise from `live_kill_blocked` above
                                      # lands there rather than as a null.
                                      # getattr matches backtest.py:874 and
                                      # keeps a logging path from ever being
                                      # the thing that raises.
                                      rail=getattr(e, "rail", "unattributed"),
                                      regime=regime_label, strategy=sig.strategy)
            stop, tp = _would_be_brackets(sig, bars, price)
            memory.judgments.log_judgment(
                tid, symbol, sig.action, "rails_reject", 1.0, price,
                regime_label, kind="rails", executed=False, reasoning=str(e),
                stop_price=stop, tp_price=tp, strategy=sig.strategy)
            log.warning("%s: %s REJECTED by risk rails — %s", symbol, sig.action, e)
            return "blocked"

        # Entry drift guard: last line of defense against acting on a price
        # the live market has left behind (fails OPEN on a quote outage —
        # bars_fresh covers that class). Entries only, never exits.
        if sig.action in ENTRY_ACTIONS:
            try:
                live = broker.latest_price(symbol)
            except Exception as e:  # noqa: BLE001 — quote outage != bad price
                # Fail-open but fail-LOUD: a skipped guard must stay
                # distinguishable from "checked and fine" in the ledger.
                log.warning("%s: drift check skipped, quote unavailable (%s)",
                            symbol, e)
                ledger.log_event("degradation",
                                 f"drift_guard: quote unavailable for "
                                 f"{symbol}, guard skipped ({e})")
                live = None
            if live is not None and not risk.entry_drift_ok(price, live, cfg):
                drift = risk.entry_drift_bps(price, live)
                msg = (f"entry drift {drift:.0f}bps > "
                       f"{cfg['risk']['max_entry_drift_bps']}bps cap "
                       f"(signal ${price:.2f} vs live ${live:.2f})")
                tid = ledger.log_decision(symbol, sig.action, sig.reason,
                                          sig.indicators, review, executed=False,
                                          detail=f"risk rejection: {msg}",
                                          rail="entry_drift",
                                          regime=regime_label, strategy=sig.strategy)
                stop, tp = _would_be_brackets(sig, bars, price)
                memory.judgments.log_judgment(
                    tid, symbol, sig.action, "rails_reject", 1.0, price,
                    regime_label, kind="rails", executed=False, reasoning=msg,
                    stop_price=stop, tp_price=tp, strategy=sig.strategy)
                log.warning("%s: %s REJECTED by drift guard — %s",
                            symbol, sig.action, msg)
                return "blocked"

        # A strategy EXIT of a bracketed position must first cancel the
        # surviving protective legs (they reserve the shares). EXIT_ACTIONS,
        # not "sell": a short's bracket legs reserve its shares exactly as a
        # long's do, so a "cover" left on the old condition would submit its
        # closing BUY while the stop leg still held the position — the
        # double-fill this cancel exists to prevent, on the short side.
        if sig.action in EXIT_ACTIONS and open_rec and \
                (open_rec.get("order") or {}).get("order_class") in ("bracket", "oto"):
            try:
                broker.cancel_open_orders(symbol)
            except Exception as e:  # noqa: BLE001 — never risk a double-exit
                ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                    review, executed=False,
                                    detail=f"leg cancel failed, "
                                           f"{sig.action} skipped: {e}",
                                    regime=regime_label, strategy=sig.strategy)
                log.error("%s: leg cancel failed, skipping %s this cycle — %s",
                          symbol, sig.action, e)
                return "blocked"

        # --- Execute ---
        # Idempotency key: deterministic per symbol/side/day, so a crashed
        # cycle rerun cannot double-submit the same intended order (the
        # broker rejects a duplicate client id).
        coid = (f"ta-{symbol}-{sig.action}-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d')}")
        try:
            if sig.action in ENTRY_ACTIONS and bracket_prices:
                # Protective legs reuse the exact prices sizing saw above. If the
                # bracket submission fails we must NOT fall back to a naked market
                # order: the quantity may have been stop-distance-sized (meanrev),
                # and a young position with no broker-side stop can only be exited
                # by the daily-loss kill switch (the swing guard blocks strategy
                # exits before min_holding_days). Refuse the entry instead.
                #
                # `side`/`entry_price` are passed ONLY for a short. Phase 1 made
                # both REQUIRED for a short (its loss is unbounded, so its stop
                # may not go unchecked) and left `entry_price` OPTIONAL for a
                # long precisely because this call site has never passed one —
                # passing it now would newly activate the long geometry check on
                # the live path, which is the byte-identical promise this group
                # is built around. Longs therefore send exactly the arguments
                # they sent before this commit, positionally and by keyword.
                short_kwargs = ({"side": sig.action, "entry_price": price}
                                if sig.action == "short" else {})
                try:
                    order = broker.bracket_market_order(symbol, qty,
                                                        *bracket_prices,
                                                        client_order_id=coid,
                                                        **short_kwargs)
                except Exception as e:  # noqa: BLE001 — no naked stop-sized entry
                    ledger.log_decision(
                        symbol, sig.action, sig.reason, sig.indicators, review,
                        executed=False,
                        detail=f"bracket unavailable — refusing naked "
                               f"stop-sized entry: {e}",
                        regime=regime_label, strategy=sig.strategy)
                    log.error("%s: bracket order failed (%s) — entry refused, "
                              "no naked position opened", symbol, e)
                    return "blocked"
            else:
                # Sells, and buys with brackets intentionally disabled (plain
                # 1%-notional sizing, no stop leg by design).
                order = broker.market_order(symbol, qty, sig.action,
                                            client_order_id=coid)
        except Exception as e:  # noqa: BLE001
            ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                review, executed=False, detail=f"order error: {e}",
                                regime=regime_label, strategy=sig.strategy)
            log.error("%s: order failed — %s", symbol, e)
            return "blocked"

        risk.record_trade()
        # Keep the in-cycle position view current so max_open_positions and
        # the concentration cap see THIS cycle's entries/exits too.
        if sig.action in ENTRY_ACTIONS:
            # SIGNED, matching what broker.positions() returns for a real
            # short — src/broker.py copies Alpaca's own `float(p.qty)` and
            # `float(p.market_value)`, both NEGATIVE for a short — so the
            # in-cycle view and the broker's view agree on sign, not just on
            # membership.
            #
            # Before this fix a "short" fell into the `else` and POPPED the
            # symbol. Within the same cycle that made Phase 1's net-exposure
            # band read a short book as flat (net_exposure_pct sums signed
            # market_value) and the down-regime gross cap undercount it by
            # the whole position (that cap sums magnitudes over the same
            # dict). Both rails were merged days before anything could emit
            # a short, so nothing would have failed — the rails would simply
            # have been blind to every short opened earlier in the cycle.
            signed_qty = -qty if sig.action == "short" else qty
            positions[symbol] = {"qty": signed_qty,
                                 "market_value": signed_qty * price,
                                 "avg_entry": price, "unrealized_pl": 0.0}
        else:
            positions.pop(symbol, None)
        # Record the game plan on ENTRIES only — an exit has no forward plan,
        # and inventing one would be noise in the record.
        plan = None
        if sig.action in ENTRY_ACTIONS:
            try:
                plan = trade_plan.build(sig, cfg, price, qty, order,
                                        regime_label, review)
                log.info("%s: PLAN\n%s", symbol, trade_plan.to_text(plan))
            except Exception as e:  # noqa: BLE001 — narration never blocks a trade
                log.warning("trade_plan build failed for %s: %s", symbol, e)
        trade_id = ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                       review, executed=True, order=order,
                                       entry_price=price, qty=qty, detail=detail_tag,
                                       regime=regime_label, strategy=sig.strategy,
                                       trade_plan=plan)
        if sig.action in ENTRY_ACTIONS:
            # Mirror this entry into the in-cycle open-trades view so the
            # portfolio-heat cap counts same-cycle entries too — otherwise a
            # later entry this cycle measures heat against the stale
            # cycle-start book. (max_open_positions and the correlation cap
            # already see same-cycle entries via `positions`; this closes the
            # gap.)
            #
            # `sig.action`, not the literal "buy" it used to hardcode. Group A
            # made risk.portfolio_heat pick the short formula off
            # `rec.get("action") == "short"` — and this is the only writer of
            # that key, so hardcoding "buy" left that fix permanently inert: a
            # short's stop sits above entry, the long formula (entry - stop)
            # goes negative, and the max(..., 0.0) clamp zeroed every short's
            # real risk out of the heat cap invisibly.
            open_trades[trade_id] = {
                "symbol": symbol, "action": sig.action, "strategy": sig.strategy,
                "qty": qty, "entry_price": price, "order": order,
                "outcome": None,
            }
        if sig.action in ENTRY_ACTIONS:  # judge accountability: approvals scored on close
            memory.judgments.log_judgment(
                trade_id, symbol, sig.action, review["verdict"],
                review.get("scale", 1.0),
                price, regime_label, kind="llm", executed=True,
                reasoning=review.get("reasoning", ""),
                stop_price=order.get("stop_price"),
                tp_price=order.get("take_profit_price"), strategy=sig.strategy,
                cited_lessons=review.get("cited_lessons"),
                confidence=review.get("confidence"))
        log.info("%s: EXECUTED %s x%d @ ~$%.2f (trade %s, %s)",
                 symbol, sig.action.upper(), qty, price, trade_id, sig.strategy)

        if sig.action in ENTRY_ACTIONS:
            # `sig.action`, not "buy": the public journal and the recap post
            # are this bot's track record, and captioning a short as a buy
            # would misstate the direction of a real trade to readers — and
            # to the news-memory/lesson loop that reads the journal back.
            recap = {"symbol": symbol, "action": sig.action, "qty": qty,
                     "entry_price": price, "strategy_reason": sig.reason,
                     "strategy": sig.strategy, "llm_reasoning": review["reasoning"]}
            link = journal_and_link(
                {"trade_id": trade_id, "symbol": symbol, "action": sig.action,
                 "qty": qty, "entry_price": price, "strategy": sig.strategy,
                 "strategy_reason": sig.reason, "indicators": sig.indicators,
                 "llm_review": review, "order": order,
                 "regime": regime_label}, cfg)
            x_poster.post_recap(recap, cfg, llm.write_x_post(recap, cfg),
                                link=link)
        else:
            # find the open ENTRY this exit closes
            for tid, rec in list(open_trades.items()):
                if rec["symbol"] == symbol:
                    # all_bars is populated in Phase 1, before this closure is
                    # ever called; SPY is the benchmark the scorecard already
                    # uses, so alpha here needs no extra fetch.
                    handle_close(tid, rec, price, ledger, memory, cfg,
                                 bench_bars=all_bars.get("SPY"))
                    open_trades.pop(tid)
                    break
        return "executed"

    # --- Phase 1: fetch, validate, and read the regime off the bars ---
    fetched_ctx = _fetch_and_validate_bars(
        broker, cfg, ledger, scan_symbols, completed_bars_only)
    if fetched_ctx is None:
        return                      # SPY stale — the whole feed is suspect
    all_bars, entries_blocked_reason, market_regime, regime_label = fetched_ctx
    entries_blocked_rail = "datacheck"
    if halted:
        # Reuses the vendor-divergence mechanism rather than adding a second
        # entry-blocking path, because it already means precisely this: entries
        # refused, exits untouched. A parallel implementation would be a second
        # place for the exit exemption to be got wrong.
        #
        # Overrides any datacheck reason rather than appending to it. Both block
        # entries identically, the datacheck verdict is separately recorded as a
        # `degradation` event, and the operator's own halt is the fact that
        # should appear against a refused trade.
        entries_blocked_reason = ("HALT engaged (exits mode) — entries blocked, "
                                  "exits still run")
        entries_blocked_rail = "halt"

    # Chandelier trail maintenance (param-gated; no-op while mult is 0).
    update_trailing_stops(broker, ledger, cfg, open_trades, all_bars)

    # Same-ticker re-entry cooldown (§9 — adopted for meanrev only):
    # symbol -> most recent exit ts, checked per strategy in the entry loop.
    last_exit: dict = {}
    if (cfg["risk"].get("reentry_cooldown") or {}).get("days"):
        for t in ledger.closed_trades():
            ets = t.get("exit_ts")
            if ets and ets > last_exit.get(t["symbol"], ""):
                last_exit[t["symbol"]] = ets

    # --- Phase 2: cross-sectional precompute + the earnings blackout ---
    xs_ctx, earnings_blackouts, ebd = _precompute(
        cfg, all_bars, open_trades, positions)

    # --- Phase 3: per-symbol ensemble loop with position ownership ---
    entries_this_cycle: dict = {}   # strategy -> executed entries this cycle,
                                    # for per-strategy max_entries_per_cycle
                                    # (dip signals cluster on market-wide down
                                    # days; the cap stops correlated pile-ins)
    news_entries = 0                # executed news-nominated entries (hard cap)
    for symbol in scan_symbols:
        bars = all_bars.get(symbol)
        if not bars:
            continue
        price = bars[-1]["close"]
        holding = symbol in positions

        if holding:
            # Exits are OWNER-ONLY: the strategy that opened the position
            # decides when to leave it (even if since disabled); everyone
            # else keeps their hands off.
            entry_ts, open_rec = None, None
            for rec in open_trades.values():
                if rec["symbol"] == symbol:
                    entry_ts, open_rec = position_entry_ts(rec), rec
                    break
            owner = ((open_rec or {}).get("strategy")
                     or strategies.DEFAULT_OWNER)
            if owner not in strategies.REGISTRY:
                ledger.log_event("ensemble_orphan",
                                 f"{symbol}: unknown owner {owner}; bracket legs remain")
                continue
            sig = strategies.generate(owner, symbol, bars, cfg, True,
                                      cross_section=xs_ctx.get(owner),
                                      entry_ts=entry_ts)
            # EXIT_ACTIONS, not "sell": the owning strategy of a SHORT closes
            # it with a "cover", and on the old condition that cover fell into
            # the `else` and was logged as a HOLD — the position's own exit
            # signal silently discarded, every cycle, forever. A "short" or
            # "buy" arriving here still falls to HOLD: this branch only ever
            # exits a position it already owns, it never pyramids into one.
            if sig.action in EXIT_ACTIONS:
                _process_signal(sig, symbol, bars, price, entry_ts, open_rec)
            else:
                ledger.log_decision(symbol, "hold", sig.reason, sig.indicators,
                                    None, executed=False, regime=regime_label,
                                    strategy=owner)
                log.info("%s: HOLD (%s) — %s", symbol, owner, sig.reason)
            continue

        # Flat symbol: consult enabled strategies in priority order; the
        # first ENTRY (buy or short) that survives review + rails takes
        # ownership.
        is_nominated = symbol in nominated
        if is_nominated and news_entries >= cfg.get("news", {}).get(
                "max_news_entries_per_cycle", 1):
            ledger.log_decision(symbol, "hold",
                                "news-nominated entry cap reached this cycle",
                                {}, None, executed=False, regime=regime_label,
                                detail="news-nominated")
            continue
        news_note = ""
        if is_nominated:
            news_note = ("NEWS-NOMINATED SYMBOL — outside the backtested "
                         "universe; nominated because: "
                         f"{nominated[symbol]}. Extra skepticism warranted: "
                         "veto is the default unless the setup is clean.")
        hold_reasons: dict = {}
        entered = False
        for name, params in strategies.enabled(cfg):
            cd = risk.cooldown_days_for(cfg, name)
            if cd and risk.cooldown_blocked(
                    last_exit.get(symbol),
                    datetime.now(timezone.utc).isoformat(), cd):
                hold_reasons[name] = {"reason": f"re-entry cooldown ({cd}d) — "
                                                f"exited {last_exit[symbol][:10]}"}
                continue
            if symbol in earnings_blackouts.get(name, ()):
                hold_reasons[name] = {"reason": "earnings within "
                                                f"{ebd[name]}d — entry "
                                                "blackout"}
                continue
            cap = params.get("max_entries_per_cycle", 0)
            if cap and entries_this_cycle.get(name, 0) >= cap:
                hold_reasons[name] = {"reason": f"max_entries_per_cycle "
                                                f"({cap}) reached this cycle"}
                continue
            sig = strategies.generate(name, symbol, bars, cfg, False,
                                      cross_section=xs_ctx.get(name))
            if sig.action not in ENTRY_ACTIONS:
                hold_reasons[name] = {"reason": sig.reason, **sig.indicators}
                continue
            # §23 relative-volume confirmation. Same rail, same helper, same
            # fail-open semantics as both simulators (risk.rvol_blocked) —
            # four sim/live divergences have already cost rework; this one is
            # a single shared implementation by design. Entries only.
            if risk.rvol_blocked(bars, cfg, name):
                hold_reasons[name] = {"reason": "volume below the relative-"
                                                "volume entry threshold",
                                      **sig.indicators}
                continue
            # A name whose ATR-derived stop would land at or below zero cannot
            # be bracket-protected: risk.brackets() returns None and the caller
            # degrades to a plain market order — an UNPROTECTED position, on the
            # most volatile name in the universe. Refuse the entry instead.
            # Provable no-op as shipped (0 of 61,104 bars); 25 of 803,787 on the
            # wide universe. Same helper in both simulators.
            if risk.unprotectable_entry(price, strategies.atr(bars, 14), cfg):
                hold_reasons[name] = {"reason": "ATR-derived stop would be "
                                                "non-positive — this position "
                                                "could not be protected",
                                      **sig.indicators}
                continue
            if _process_signal(
                    sig, symbol, bars, price, None, None,
                    extra_context=news_note,
                    detail_tag="news-nominated" if is_nominated else "",
            ) == "executed":
                entered = True
                entries_this_cycle[name] = entries_this_cycle.get(name, 0) + 1
                if is_nominated:
                    news_entries += 1
                break  # ownership taken; lower priorities not consulted
        if not entered and hold_reasons:
            # one consolidated hold record per symbol per cycle
            ledger.log_decision(symbol, "hold",
                                f"no entry from {len(hold_reasons)} strategies",
                                hold_reasons, None, executed=False,
                                regime=regime_label)
            log.info("%s: HOLD — %d strategies, no entry", symbol, len(hold_reasons))

    _finalize_cycle(cfg, ledger, memory, broker, account, positions,
                    all_bars, regime_label)
    log.info("Cycle complete.")
    # Reached only when every stage above ran. Every early return in this
    # function falls out as None, which is falsy on purpose — "completed" is
    # something a cycle has to earn, not the default.
    return True


if __name__ == "__main__":
    # --open-cycle: the 09:35 ET pass. Same rails, same strategies, but it
    # reads only COMPLETED daily bars, so it acts on yesterday's close the way
    # the backtester does. Entries that were true at the close no longer wait
    # until 15:45 the next day.
    configure_logging()
    run_cycle(completed_bars_only="--open-cycle" in sys.argv)
