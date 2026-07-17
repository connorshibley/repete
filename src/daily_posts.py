"""Morning plan / evening review X posts — narrative around the daily cycle.

    python src/daily_posts.py --plan     (scheduled 9:35 AM ET weekdays)
    python src/daily_posts.py --review   (scheduled 4:20 PM ET weekdays)

STRICTLY READ-ONLY with respect to trading: the plan mode runs the strategy
code to see what it WOULD watch but places no orders, writes no decision
records, and cannot influence the 3:45 cycle. Posts go through
x_poster.post_text (paper disclosure + dry_run + never-raise). Every post is
mirrored to the ledger as an event. Engagement metrics are never read —
learning stays outcome-based, per the project's core rule.
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone

import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from broker import Broker
from ledger import Ledger
import llm
import regime as regime_mod
import risk
import strategies
import x_poster

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(),
              logging.FileHandler("logs/agent.log", mode="a")],
)
log = logging.getLogger("daily")


# ---- facts gathering (I/O) ----

def gather_plan_facts(cfg: dict, broker) -> dict | None:
    """Read-only pre-market scan: what would the enabled strategies buy on
    current data? None => don't post (stale data or nothing to say)."""
    lookback = strategies.max_lookback_bars(cfg)
    max_age = cfg["risk"].get("max_bar_age_days", 4)
    all_bars: dict = {}
    for symbol in cfg["symbols"]:
        try:
            bars = broker.bars(symbol, cfg["strategy"]["timeframe"], lookback)
            if bars and risk.bars_fresh(bars, max_age):
                all_bars[symbol] = bars
        except Exception as e:  # noqa: BLE001
            log.warning("plan scan: bars failed for %s: %s", symbol, e)
    if not risk.bars_fresh(all_bars.get("SPY", []), max_age):
        log.warning("plan scan: SPY stale — skipping the plan post")
        return None

    market_regime = regime_mod.compute_regime(all_bars.get("SPY", []),
                                              cfg["learning"]["regime"])
    positions = broker.positions()
    xs_ctx = strategies.prepare_cross_sections(cfg, all_bars)
    setups = []
    for symbol, bars in all_bars.items():
        if symbol in positions:
            continue
        for name, _params in strategies.enabled(cfg):
            sig = strategies.generate(name, symbol, bars, cfg, False,
                                      cross_section=xs_ctx.get(name))
            if sig.action == "buy":
                setups.append({"symbol": symbol, "strategy": name,
                               "reason": sig.reason})
                break
    return {"kind": "plan",
            "setups": setups[:5],
            "n_setups": len(setups),
            "n_positions": len(positions),
            "positions": sorted(positions),
            "regime": regime_mod.describe(market_regime),
            "note": "watchlist only — the 3:45 PM ET cycle decides"}


def gather_review_facts(cfg: dict, broker, records: list[dict]) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    todays = [r for r in records if r.get("ts", "").startswith(today)]
    decisions = [r for r in todays if r.get("type") == "decision"]
    executed = [r for r in decisions if r.get("executed")]
    vetoes = [r for r in decisions
              if (r.get("llm_review") or {}).get("verdict") == "veto"]
    outcomes = [r for r in todays if r.get("type") == "outcome"]
    pnl_today = round(sum(o.get("pnl") or 0.0 for o in outcomes), 2)
    try:
        equity = broker.account()["equity"]
    except Exception as e:  # noqa: BLE001
        log.warning("review: account read failed: %s", e)
        equity = None
    return {"kind": "review",
            "trades": [{"symbol": r["symbol"], "action": r["action"],
                        "strategy": r.get("strategy")} for r in executed],
            "n_executed": len(executed),
            "n_vetoes": len(vetoes),
            "n_holds": sum(1 for r in decisions if r["action"] == "hold"),
            "closed_today": len(outcomes),
            "realized_pnl_today": pnl_today,
            "equity": equity,
            "halted": risk.check_halt()}


# ---- templates (pure, offline fallbacks) ----

def plan_template(f: dict) -> str:
    if f["setups"]:
        watch = ", ".join(f"{s['symbol']} ({s['strategy']})"
                          for s in f["setups"])
        head = f"Pre-market scan: watching {watch}."
    else:
        head = "Pre-market scan: no fresh setups."
    return (f"[PAPER] {head} Holding {f['n_positions']} position"
            f"{'s' if f['n_positions'] != 1 else ''}. "
            f"Regime {f['regime']}. Decisions at the 3:45 PM ET cycle.")[:275]


def review_template(f: dict) -> str:
    if f["halted"]:
        act = "HALT engaged — no trading"
    elif f["trades"]:
        act = "Today: " + ", ".join(f"{t['action'].upper()} {t['symbol']}"
                                    for t in f["trades"])
    else:
        act = "No trades today"
    bits = [act]
    if f["n_vetoes"]:
        bits.append(f"{f['n_vetoes']} judge veto"
                    f"{'es' if f['n_vetoes'] != 1 else ''}")
    if f["closed_today"]:
        bits.append(f"{f['closed_today']} closed "
                    f"({f['realized_pnl_today']:+.2f} realized)")
    text = "[PAPER] " + "; ".join(bits) + "."
    if f["equity"] is not None:
        text += f" Equity ${f['equity']:,.0f}."
    return text[:275]


# ---- entry point ----

def run(mode: str):
    load_dotenv()
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    ledger = Ledger(cfg["memory"]["ledger_path"])
    broker = Broker(cfg)

    if mode == "plan":
        if risk.check_halt():
            log.warning("HALT present — skipping the plan post")
            ledger.log_event("plan_post_skipped", "HALT present")
            return
        facts = gather_plan_facts(cfg, broker)
        if facts is None:
            ledger.log_event("plan_post_skipped", "stale or missing data")
            return
        text = llm.write_daily_post("plan", facts, cfg) or plan_template(facts)
        event = "plan_post"
    else:
        facts = gather_review_facts(cfg, broker, ledger.all_records())
        text = (llm.write_daily_post("review", facts, cfg)
                or review_template(facts))
        event = "review_post"

    x_poster.post_text(text, cfg)
    ledger.log_event(event, text)

    try:  # keep the dashboard current at every scheduled touchpoint
        import dashboard
        dashboard.render(cfg)
    except Exception as e:  # noqa: BLE001 — cosmetic, never blocks
        log.warning("dashboard render failed: %s", e)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Morning plan / evening review post")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--review", action="store_true")
    args = p.parse_args()
    run("plan" if args.plan else "review")
