"""Hard risk rails — deterministic, enforced in code, NOT overridable by the LLM.

This module implements the controls the research identified as
non-negotiable (Knight Capital post-mortems, FINRA 2026 AI-agent
guidance, the Agentic Trading survey's 'supervisory controls'):

  * fixed-fractional position sizing
  * per-order dollar cap
  * max position concentration
  * max trades per day (order-rate circuit breaker)
  * daily loss limit -> flatten everything + write a HALT file
  * HALT file check: if present, the bot refuses to trade until you delete it
  * SWING GUARD: exits are blocked until a position is min_holding_days old,
    which makes same-day round trips (day trading) structurally impossible.
    The kill switch (flatten_all) is exempt — safety overrides the guard.
"""
import os
import json
import logging
from datetime import date, datetime, timezone

log = logging.getLogger("risk")

HALT_FILE = "HALT"
_TRADECOUNT_FILE = "memory/.trade_counts.json"


class RiskRejection(Exception):
    """Raised when a hard rail blocks an order."""


def check_halt() -> bool:
    return os.path.exists(HALT_FILE)


def engage_halt(reason: str):
    with open(HALT_FILE, "w") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} — {reason}\n"
                "Delete this file to re-enable trading (after you understand what happened).\n")
    log.critical("HALT ENGAGED: %s", reason)


def _trades_today() -> int:
    try:
        with open(_TRADECOUNT_FILE) as f:
            data = json.load(f)
        return data.get(str(date.today()), 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def record_trade():
    counts = {}
    try:
        with open(_TRADECOUNT_FILE) as f:
            counts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    key = str(date.today())
    counts = {key: counts.get(key, 0) + 1}   # keep only today
    os.makedirs(os.path.dirname(_TRADECOUNT_FILE), exist_ok=True)
    with open(_TRADECOUNT_FILE, "w") as f:
        json.dump(counts, f)


def daily_loss_breached(account: dict, cfg: dict) -> bool:
    """True if today's P&L is below the configured loss limit."""
    limit_pct = cfg["risk"]["daily_loss_limit_pct"]
    last = account["last_equity"]
    if last <= 0:
        return False
    pnl_pct = (account["equity"] - last) / last * 100
    if pnl_pct <= -limit_pct:
        log.critical("Daily loss limit breached: %.2f%% (limit -%.2f%%)", pnl_pct, limit_pct)
        return True
    return False


def size_order(account: dict, price: float, cfg: dict) -> int:
    """Fixed-fractional sizing, then clamp by every cap. Returns whole-share qty."""
    r = cfg["risk"]
    equity = account["equity"]

    dollars = equity * r["risk_per_trade_pct"] / 100          # fixed fractional
    dollars = min(dollars, r["max_order_value_usd"])           # per-order cap
    dollars = min(dollars, equity * r["max_position_pct"] / 100)  # concentration cap
    dollars = min(dollars, account["buying_power"])

    qty = int(dollars // price)
    return max(qty, 0)


def bracket_prices(entry_price: float, atr_value: float | None,
                   cfg: dict,
                   vol_bucket: str | None = None) -> tuple[float, float | None] | None:
    """Deterministic stop/take-profit prices for a bracket entry.

    stop = entry − stop_atr_mult·ATR; tp = entry + take_profit_atr_mult·ATR
    (tp omitted when the multiplier is 0). When `stop_atr_mult_high_vol` is
    configured and the market vol regime at entry is "high", that wider
    multiplier is used instead (fixed 2×ATR whipsaws in high-vol tape) —
    absent/0 keeps the single fixed multiplier. Returns None — caller
    degrades to a plain market order — when brackets are disabled, ATR is
    unavailable, or the stop would be non-positive. Computed AFTER the LLM
    review and the pre-trade rails; the LLM never sees these numbers.
    """
    b = cfg["risk"].get("brackets", {})
    if not b.get("enabled") or not atr_value or atr_value <= 0:
        return None
    mult = b["stop_atr_mult"]
    high_mult = b.get("stop_atr_mult_high_vol", 0)
    if high_mult and vol_bucket == "high":
        mult = high_mult
    stop = round(entry_price - mult * atr_value, 2)
    if stop <= 0:
        log.warning("bracket stop would be non-positive (entry %.2f, ATR %.2f) "
                    "— falling back to plain market order", entry_price, atr_value)
        return None
    tp_mult = b.get("take_profit_atr_mult", 0)
    tp = round(entry_price + tp_mult * atr_value, 2) if tp_mult else None
    return stop, tp


def bars_fresh(bars: list[dict], max_age_days: int) -> bool:
    """True when the newest bar is at most max_age_days calendar days old.

    Guards against the failure mode where a data API quietly returns a
    months-old window (it happened): trading decisions must never be
    computed from stale bars. max_age_days <= 0 disables the check.
    """
    if max_age_days <= 0:
        return True
    if not bars:
        return False
    last = datetime.fromisoformat(bars[-1]["ts"])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - last
    return age.days <= max_age_days


def swing_guard(entry_ts: str | None, cfg: dict):
    """Block exits on positions younger than min_holding_days (no day trading)."""
    min_days = cfg["risk"].get("min_holding_days", 0)
    if min_days <= 0 or not entry_ts:
        return
    entered = datetime.fromisoformat(entry_ts)
    age_days = (datetime.now(timezone.utc) - entered).days
    if age_days < min_days:
        raise RiskRejection(
            f"swing guard: position is {age_days}d old, minimum holding period is "
            f"{min_days}d — this bot does not day trade")


def pure_checks(action: str, symbol: str, qty: int, price: float,
                account: dict, positions: dict, cfg: dict):
    """The side-effect-free subset of the rails (no HALT/trade-count file I/O).

    Shared with the offline backtester so simulated fills obey the exact same
    caps as live orders. Raises RiskRejection with a reason.
    """
    r = cfg["risk"]

    if qty <= 0:
        raise RiskRejection("computed quantity is zero (account too small for caps)")

    order_value = qty * price
    if order_value > r["max_order_value_usd"]:
        raise RiskRejection(f"order value ${order_value:,.0f} exceeds cap ${r['max_order_value_usd']:,}")

    if action == "buy":
        if len(positions) >= r["max_open_positions"] and symbol not in positions:
            raise RiskRejection(f"max open positions reached ({r['max_open_positions']})")
        existing = positions.get(symbol, {}).get("market_value", 0.0)
        if existing + order_value > account["equity"] * r["max_position_pct"] / 100:
            raise RiskRejection(f"would exceed {r['max_position_pct']}% concentration cap on {symbol}")

    if action == "sell" and symbol not in positions:
        raise RiskRejection(f"no position in {symbol} to sell (state desync guard)")


def pre_trade_checks(action: str, symbol: str, qty: int, price: float,
                     account: dict, positions: dict, cfg: dict,
                     entry_ts: str | None = None):
    """Last-stage gate every order must pass. Raises RiskRejection with a reason."""
    if check_halt():
        raise RiskRejection("HALT file present — trading disabled")
    if _trades_today() >= cfg["risk"]["max_trades_per_day"]:
        raise RiskRejection(f"max trades per day reached ({cfg['risk']['max_trades_per_day']})")

    pure_checks(action, symbol, qty, price, account, positions, cfg)

    if action == "sell":
        swing_guard(entry_ts, cfg)
