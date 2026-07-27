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
                 exit_reason: str = "strategy_sell"):
    """A position was exited: record outcome, learn, post."""
    entry = open_rec.get("entry_price") or exit_price
    qty = open_rec.get("qty") or 0
    pnl = (exit_price - entry) * qty
    pnl_pct = (exit_price - entry) / entry * 100 if entry else 0.0
    ledger.close_trade(trade_id, exit_price, pnl, pnl_pct, exit_reason=exit_reason)
    log.info("Closed trade %s (%s): P&L $%.2f (%.2f%%)",
             trade_id, exit_reason, pnl, pnl_pct)

    closed = {**open_rec, "exit_price": exit_price, "pnl": round(pnl, 2),
              "pnl_pct": round(pnl_pct, 2), "result": "win" if pnl > 0 else "loss",
              "exit_reason": exit_reason}
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
    for tid, rec in ledger.open_buys().items():
        symbol = rec["symbol"]
        if symbol in positions:
            continue
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
            handle_close(tid, rec, price, ledger, memory, cfg, exit_reason=reason)
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


def _run_cycle(completed_bars_only: bool = False):
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
        return

    # Storage backend is chosen ONCE, before any store is constructed.
    store.configure(cfg)

    ledger = Ledger(cfg["memory"]["ledger_path"])
    # Decision-surface fingerprint: every record this cycle carries the
    # rulebook version, so the track record segments honestly by model.
    ledger.set_model_version(modelver.current_version())
    memory = Memory(cfg, ledger)

    if risk.check_halt():
        log.critical("HALT file present — refusing to trade. Delete HALT to resume.")
        ledger.log_event("halted_cycle_skipped")
        return

    broker = Broker(cfg)
    account = broker.account()          # deterministic state: always from the broker,
    positions = broker.positions()      # never from memory or prior LLM output.
    log.info("Equity: $%.2f | Positions: %s", account["equity"], list(positions) or "none")

    # --- Kill switch: daily loss limit ---
    if risk.daily_loss_breached(account, cfg):
        # Engage HALT and record the breach BEFORE attempting the flatten. A
        # broker error while closing (timeout/API outage) must not leave the
        # daily-loss breach un-halted and unrecorded — otherwise the next
        # scheduled cycle re-enters this path instead of being HALT-blocked.
        risk.engage_halt("daily loss limit breached — flattening all positions")
        ledger.log_event("kill_switch",
                         "daily loss limit breached; HALT engaged; flattening")
        try:
            broker.flatten_all()
        except Exception as e:  # noqa: BLE001 — HALT already set; report + stop
            log.critical("flatten_all failed after kill switch: %s", e)
            ledger.log_event("kill_switch_flatten_failed",
                             f"{e} — positions may remain open; HALT engaged")
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
    # nominations. NOMINATION != TRADE: entries still need a deterministic
    # strategy signal + judge + rails. Stale/missing context = feature off. ---
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

    market_regime = None   # computed after the ensemble bar fetch (from SPY bars)
    regime_label = None

    def _would_be_brackets(sig, bars, price):
        """Deterministic bracket snapshot for blocked buys, so the
        counterfactual later replays the same protective exits."""
        if sig.action != "buy":
            return None, None
        bcfg = cfg["risk"].get("brackets", {})
        prices = risk.bracket_prices(
            price, strategy.atr(bars, bcfg.get("atr_period", 14)), cfg,
            vol_bucket=(market_regime or {}).get("vol"))
        return prices if prices else (None, None)

    def _process_signal(sig, symbol, bars, price, entry_ts, open_rec,
                        extra_context: str = "", detail_tag: str = "") -> str:
        """One actionable signal through the unchanged pipeline:
        LLM review -> rails -> leg cancel -> execute -> ledger/judgment/recap.
        Returns 'executed' or 'blocked'."""
        # Vendor cross-check verdict (set after the bars fetch): entries are
        # blocked before the LLM even looks — deterministic, and no judge can
        # override a data-integrity stop. Exits pass through untouched.
        if sig.action == "buy" and entries_blocked_reason:
            tid = ledger.log_decision(
                symbol, sig.action, sig.reason, sig.indicators, None,
                executed=False,
                detail=f"risk rejection: {entries_blocked_reason[:180]}",
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
            ledger.log_event("degradation",
                             f"llm_judge: review unavailable for {symbol}, "
                             f"proceeding rule-based ({review['degraded']})")
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
        if sig.action == "buy":
            bcfg = cfg["risk"].get("brackets", {})
            bracket_prices = risk.bracket_prices(
                price, strategy.atr(bars, bcfg.get("atr_period", 14)), cfg,
                vol_bucket=(market_regime or {}).get("vol"))
            full_qty = risk.size_order(account, price, cfg, bars=bars,
                                       strategy=sig.strategy,
                                       stop_price=bracket_prices[0]
                                       if bracket_prices else None)
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
            qty = int(positions.get(symbol, {}).get("qty", 0))  # exit full position

        try:
            if sig.action == "buy":
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
        if sig.action == "buy":
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
                                          regime=regime_label, strategy=sig.strategy)
                stop, tp = _would_be_brackets(sig, bars, price)
                memory.judgments.log_judgment(
                    tid, symbol, sig.action, "rails_reject", 1.0, price,
                    regime_label, kind="rails", executed=False, reasoning=msg,
                    stop_price=stop, tp_price=tp, strategy=sig.strategy)
                log.warning("%s: buy REJECTED by drift guard — %s", symbol, msg)
                return "blocked"

        # A strategy sell of a bracketed position must first cancel the
        # surviving protective legs (they reserve the shares).
        if sig.action == "sell" and open_rec and \
                (open_rec.get("order") or {}).get("order_class") in ("bracket", "oto"):
            try:
                broker.cancel_open_orders(symbol)
            except Exception as e:  # noqa: BLE001 — never risk a double-sell
                ledger.log_decision(symbol, sig.action, sig.reason, sig.indicators,
                                    review, executed=False,
                                    detail=f"leg cancel failed, sell skipped: {e}",
                                    regime=regime_label, strategy=sig.strategy)
                log.error("%s: leg cancel failed, skipping sell this cycle — %s",
                          symbol, e)
                return "blocked"

        # --- Execute ---
        # Idempotency key: deterministic per symbol/side/day, so a crashed
        # cycle rerun cannot double-submit the same intended order (the
        # broker rejects a duplicate client id).
        coid = (f"ta-{symbol}-{sig.action}-"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d')}")
        try:
            if sig.action == "buy" and bracket_prices:
                # Protective legs reuse the exact prices sizing saw above. If the
                # bracket submission fails we must NOT fall back to a naked market
                # order: the quantity may have been stop-distance-sized (meanrev),
                # and a young position with no broker-side stop can only be exited
                # by the daily-loss kill switch (the swing guard blocks strategy
                # exits before min_holding_days). Refuse the entry instead.
                try:
                    order = broker.bracket_market_order(symbol, qty,
                                                        *bracket_prices,
                                                        client_order_id=coid)
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
        if sig.action == "buy":
            positions[symbol] = {"qty": qty, "market_value": qty * price,
                                 "avg_entry": price, "unrealized_pl": 0.0}
        else:
            positions.pop(symbol, None)
        # Record the game plan on ENTRIES only — an exit has no forward plan,
        # and inventing one would be noise in the record.
        plan = None
        if sig.action == "buy":
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
        if sig.action == "buy":
            # Mirror this entry into the in-cycle open-trades view so the
            # portfolio-heat cap counts same-cycle entries too — otherwise a
            # later buy this cycle measures heat against the stale cycle-start
            # book. (max_open_positions and the correlation cap already see
            # same-cycle entries via `positions`; this closes the gap.)
            open_trades[trade_id] = {
                "symbol": symbol, "action": "buy", "strategy": sig.strategy,
                "qty": qty, "entry_price": price, "order": order,
                "outcome": None,
            }
        if sig.action == "buy":  # judge accountability: approvals get scored on close
            memory.judgments.log_judgment(
                trade_id, symbol, "buy", review["verdict"], review.get("scale", 1.0),
                price, regime_label, kind="llm", executed=True,
                reasoning=review.get("reasoning", ""),
                stop_price=order.get("stop_price"),
                tp_price=order.get("take_profit_price"), strategy=sig.strategy,
                cited_lessons=review.get("cited_lessons"),
                confidence=review.get("confidence"))
        log.info("%s: EXECUTED %s x%d @ ~$%.2f (trade %s, %s)",
                 symbol, sig.action.upper(), qty, price, trade_id, sig.strategy)

        if sig.action == "buy":
            recap = {"symbol": symbol, "action": "buy", "qty": qty,
                     "entry_price": price, "strategy_reason": sig.reason,
                     "strategy": sig.strategy, "llm_reasoning": review["reasoning"]}
            link = journal_and_link(
                {"trade_id": trade_id, "symbol": symbol, "action": "buy",
                 "qty": qty, "entry_price": price, "strategy": sig.strategy,
                 "strategy_reason": sig.reason, "indicators": sig.indicators,
                 "llm_review": review, "order": order,
                 "regime": regime_label}, cfg)
            x_poster.post_recap(recap, cfg, llm.write_x_post(recap, cfg),
                                link=link)
        else:
            # find the open buy this sell closes
            for tid, rec in list(open_trades.items()):
                if rec["symbol"] == symbol:
                    handle_close(tid, rec, price, ledger, memory, cfg)
                    open_trades.pop(tid)
                    break
        return "executed"

    # --- Phase 1: fetch all bars up front (ensemble needs the full
    # cross-section; lookback sized to the most demanding strategy) ---
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
        return
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

    # --- Phase 2: once-per-cycle cross-sectional precompute (includes
    # disabled strategies that still own open positions — exits must work) ---
    open_owners = {rec.get("strategy") or strategies.DEFAULT_OWNER
                   for rec in open_trades.values()}
    xs_ctx = strategies.prepare_cross_sections(cfg, all_bars,
                                               extra_owners=open_owners)

    # --- Earnings blackout (PER-STRATEGY param, off by default): block NEW
    # entries in names reporting within that strategy's N days. Deterministic,
    # computed before the loop; exits are never blocked; calendar failures
    # fail open. Per-strategy because the gate evidence split: it helps
    # trend entries (tsmom) and hurts dip-buying (meanrev). ---
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
            if sig.action == "sell":
                _process_signal(sig, symbol, bars, price, entry_ts, open_rec)
            else:
                ledger.log_decision(symbol, "hold", sig.reason, sig.indicators,
                                    None, executed=False, regime=regime_label,
                                    strategy=owner)
                log.info("%s: HOLD (%s) — %s", symbol, owner, sig.reason)
            continue

        # Flat symbol: consult enabled strategies in priority order; the
        # first buy that survives review + rails takes ownership.
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
            if sig.action != "buy":
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
    try:  # dashboard/blog regeneration is cosmetic — never touches the cycle
        import blog
        import dashboard
        dashboard.render(cfg, spy_bars=all_bars.get("SPY"))
        blog.render(cfg)
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard/blog render failed: %s", e)
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
