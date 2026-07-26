#!/usr/bin/env python3
"""Sanitized, production-SCALE fixture for end-to-end QA of the publisher.

Why this has to exist
---------------------
The live ledger is 11 days and 1 closed trade. `publisher/gates.py` requires
**30 closed trades and 90 days** before checkout opens (invariant #10), so on
real data the entire paid path — checkout, entitlements, webhooks, the paid feed
and the paid dashboard — is **structurally unreachable**. It has never been
exercised against realistic data. That is precisely where defects live.

This generates ~18 months at the live bot's observed decision rate: tens of
thousands of records, dozens of closed trades, every strategy, both regimes,
every judge verdict, plus a subscriber DB covering anon / free / paid /
unsubscribed / expired.

Sanitized by construction
-------------------------
Every email is `@example.invalid` (RFC 2606 — can never route). Every token is a
throwaway. No real key, address, or account number is generated or read. The
data is obviously synthetic: symbols are real tickers because the code parses
them, but every price, fill and P&L is fabricated.

Deterministic
-------------
Seeded; the same seed gives byte-identical output, so a bug found here is
reproducible by anyone with the same command.

    python scripts/qa_fixture.py --out /tmp/qa            # build
    python scripts/qa_fixture.py --out /tmp/qa --summary  # describe, no write
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "XOM", "JNJ",
           "KO", "PG", "JPM", "V", "CAT", "LLY", "GS", "AMD", "XLV", "IWM"]
STRATEGIES = ["tsmom", "meanrev", "ma_crossover"]
REGIMES = ["up/low", "up/high", "down/low", "down/high"]
VERDICTS = ["approve", "downsize", "veto"]

# RFC 2606 reserved TLD: these addresses cannot be resolved or delivered to.
DOMAIN = "example.invalid"


def _guard_not_live(out_dir: str):
    """Refuse to write anywhere near the real track record.

    The live ledger is the one artifact in this project that cannot be
    rebuilt. A fixture generator pointed at it would destroy 11 days of
    forward record with synthetic noise and look like it worked.
    """
    real = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "memory"))
    target = os.path.realpath(out_dir)
    if target == real or target.startswith(real + os.sep):
        raise SystemExit(
            f"REFUSING to write fixture data into the live memory/ directory\n"
            f"  target: {target}\n"
            f"  live:   {real}\n"
            f"This would overwrite the forward track record.")


def build_ledger(rng: random.Random, days: int, start: datetime) -> list[dict]:
    """~18 months of cycles at the live bot's observed rate (~56 decisions/day).

    Shapes mirror src/ledger.py exactly: decision / outcome / event /
    fill_quality, with the same keys the publisher and dashboard read.
    """
    records: list[dict] = []
    open_trades: list[dict] = []
    equity = 100_000.0
    n_closed = 0

    for d in range(days):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:                      # weekdays only, like the bot
            continue
        regime = rng.choice(REGIMES)

        # --- exits first, mirroring reconcile-then-trade cycle order ---
        for t in list(open_trades):
            age = (day - datetime.fromisoformat(t["ts"])).days
            if age >= rng.randint(3, 25):
                exit_px = t["entry_price"] * (1 + rng.uniform(-0.09, 0.13))
                pnl = (exit_px - t["entry_price"]) * t["qty"]
                equity += pnl
                records.append({
                    "type": "outcome", "trade_id": t["trade_id"],
                    "exit_price": round(exit_px, 2), "pnl": round(pnl, 2),
                    "pnl_pct": round((exit_px / t["entry_price"] - 1) * 100, 3),
                    "result": "win" if pnl > 0 else "loss",
                    "exit_reason": rng.choice(
                        ["strategy_sell", "stop_loss", "take_profit"]),
                    "ts": day.replace(hour=19, minute=45).isoformat(),
                })
                open_trades.remove(t)
                n_closed += 1

        # --- the day's decisions: mostly holds, like the real ledger ---
        for i in range(rng.randint(45, 62)):
            sym = rng.choice(SYMBOLS)
            strat = rng.choice(STRATEGIES)
            ts = day.replace(hour=19, minute=45,
                             second=rng.randint(0, 59)).isoformat()
            roll = rng.random()

            if roll < 0.72:                          # hold
                records.append({
                    "type": "decision", "trade_id": f"h{d:04d}{i:03d}",
                    "regime": regime, "strategy": strat, "symbol": sym,
                    "action": "hold", "strategy_reason": "no entry conditions",
                    "indicators": {"rsi2": round(rng.uniform(5, 95), 1)},
                    "llm_review": None, "executed": False, "detail": "",
                    "order": None, "entry_price": None, "qty": None,
                    "outcome": None, "ts": ts,
                })
                continue

            verdict = rng.choices(VERDICTS, weights=[46, 52, 2])[0]
            review = {
                "verdict": verdict,
                "scale": 1.0 if verdict == "approve" else rng.choice([.4, .5, .6, .7]),
                "confidence": rng.choice(["low", "medium", "high"]),
                "reasoning": f"{sym} {strat} setup assessed against {regime} regime.",
                "bull_case": f"{sym} trend intact; volume supports continuation.",
                "bear_case": f"{sym} extended; {regime} regime argues caution.",
            }
            price = round(rng.uniform(45, 620), 2)

            if verdict == "veto" or roll > 0.93:      # blocked entry
                records.append({
                    "type": "decision", "trade_id": f"b{d:04d}{i:03d}",
                    "regime": regime, "strategy": strat, "symbol": sym,
                    "action": "buy", "strategy_reason": f"{strat} entry signal",
                    "indicators": {"rsi2": round(rng.uniform(2, 15), 1)},
                    "llm_review": review, "executed": False,
                    "detail": "LLM veto" if verdict == "veto"
                              else "risk rejection: max open positions reached (8)",
                    "order": None, "entry_price": None, "qty": None,
                    "outcome": None, "ts": ts,
                })
                continue

            if len(open_trades) >= 8:
                continue
            qty = max(1, int(2000 / price))
            tid = f"t{d:04d}{i:03d}"
            rec = {
                "type": "decision", "trade_id": tid, "regime": regime,
                "strategy": strat, "symbol": sym, "action": "buy",
                "strategy_reason": f"{strat} entry signal on {sym}",
                "indicators": {"rsi2": round(rng.uniform(2, 15), 1)},
                "llm_review": review, "executed": True, "detail": "",
                "order": {"id": f"o-{tid}", "stop_price": round(price * .94, 2)},
                "entry_price": price, "qty": qty,
                "trade_plan": {"thesis": f"{strat} continuation",
                               "expected_hold_days": rng.randint(3, 20)},
                "outcome": None, "ts": ts,
            }
            records.append(rec)
            open_trades.append(rec)
            records.append({
                "type": "fill_quality", "trade_id": tid, "symbol": sym,
                "side": "buy", "signal_price": price,
                "filled_avg_price": round(price * (1 + rng.uniform(-.001, .003)), 2),
                "slippage_bps": round(rng.uniform(-8, 32), 2), "ts": ts,
            })

        records.append({
            "type": "event", "event": "cycle_complete",
            "detail": json.dumps({"equity": round(equity, 2),
                                  "n_positions": len(open_trades),
                                  "regime": regime, "sha": "f" * 40,
                                  "config_dirty": False, "behind": 0}),
            "ts": day.replace(hour=19, minute=50).isoformat(),
        })

    if open_trades:
        records.append({
            "type": "event", "event": "positions_mark",
            "detail": json.dumps({
                t["symbol"]: {"qty": t["qty"], "avg_entry": t["entry_price"],
                              "market_value": round(t["entry_price"] * t["qty"] * 1.02, 2),
                              "unrealized_pl": round(t["entry_price"] * t["qty"] * .02, 2)}
                for t in open_trades}),
            "ts": records[-1]["ts"],
        })
    return records


def build_subscribers(db_path: str, rng: random.Random) -> dict:
    """Every role the app can serve, so no tier is tested by extrapolation."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from publisher.subscribers import SubscriberDB

    store = SubscriberDB(db_path)
    roles = {
        "free": [f"free{i}@{DOMAIN}" for i in range(1, 4)],
        "paid": [f"paid{i}@{DOMAIN}" for i in range(1, 4)],
        "unsubscribed": [f"gone1@{DOMAIN}"],
        "expired": [f"expired1@{DOMAIN}"],
    }
    for email in sum(roles.values(), []):
        store.upsert_subscriber(email)
    for email in roles["paid"]:
        store.grant(email, "paid", source="qa-fixture",
                    expires=datetime.now(timezone.utc) + timedelta(days=30))
    for email in roles["expired"]:
        store.grant(email, "paid", source="qa-fixture",
                    expires=datetime.now(timezone.utc) - timedelta(days=1))
    for email in roles["unsubscribed"]:
        store.unsubscribe(email)
    return roles


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="fixture directory (NOT memory/)")
    p.add_argument("--days", type=int, default=550, help="~18 months")
    p.add_argument("--seed", type=int, default=20260725)
    p.add_argument("--summary", action="store_true", help="describe, do not write")
    args = p.parse_args()

    _guard_not_live(args.out)
    rng = random.Random(args.seed)
    start = datetime(2025, 1, 6, tzinfo=timezone.utc)
    records = build_ledger(rng, args.days, start)

    n_dec = sum(1 for r in records if r["type"] == "decision")
    n_exec = sum(1 for r in records if r["type"] == "decision" and r["executed"])
    n_closed = sum(1 for r in records if r["type"] == "outcome")
    span = ((datetime.fromisoformat(records[-1]["ts"])
             - datetime.fromisoformat(records[0]["ts"])).days)

    print(f"fixture: {len(records):,} records | {n_dec:,} decisions | "
          f"{n_exec:,} executed | {n_closed} closed | {span} days")
    print(f"  revenue-gate thresholds: 30 closed / 90 days -> "
          f"{'OPEN' if n_closed >= 30 and span >= 90 else 'CLOSED'}")
    if args.summary:
        return 0

    os.makedirs(args.out, exist_ok=True)
    led = os.path.join(args.out, "ledger.jsonl")
    with open(led, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    for name in ("lessons.jsonl", "judgments.jsonl", "postexit.jsonl"):
        open(os.path.join(args.out, name), "a").close()

    pub_dir = os.path.join(args.out, "publisher_data")
    os.makedirs(pub_dir, exist_ok=True)
    roles = build_subscribers(os.path.join(pub_dir, "subscribers.db"), rng)

    print(f"  wrote {led}")
    print(f"  subscribers: " + ", ".join(f"{k}={len(v)}" for k, v in roles.items()))
    print(f"  all emails @{DOMAIN} (RFC 2606 — undeliverable by construction)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
