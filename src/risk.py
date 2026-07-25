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
import math
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


# A corrupt counter must not silently un-cap the day's trading. Absent file =
# genuinely no trades yet (fine); unparseable file = we do not know, so the
# rail assumes the worst. Same polarity as preflight: state integrity fails SAFE.
_TRADECOUNT_UNKNOWN = 10 ** 6


def _trades_today() -> int:
    try:
        with open(_TRADECOUNT_FILE) as f:
            data = json.load(f)
        return data.get(str(date.today()), 0)
    except FileNotFoundError:
        return 0                      # first run today — no trades yet
    except (json.JSONDecodeError, ValueError, OSError) as e:
        log.critical("trade counter unreadable (%s) — treating the daily cap "
                     "as REACHED until it is repaired", e)
        return _TRADECOUNT_UNKNOWN    # fail CLOSED: the cap keeps binding


def record_trade():
    counts = {}
    try:
        with open(_TRADECOUNT_FILE) as f:
            counts = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass                          # a fresh count is written below regardless
    key = str(date.today())
    counts = {key: counts.get(key, 0) + 1}   # keep only today
    os.makedirs(os.path.dirname(_TRADECOUNT_FILE), exist_ok=True)
    # Atomic: write a temp file, fsync, then rename. `open(..., "w")` truncates
    # first, so a crash mid-write previously left an empty/partial file — which
    # the reader then swallowed as 0 and the daily cap stopped binding.
    tmp = f"{_TRADECOUNT_FILE}.tmp{os.getpid()}"
    try:
        with open(tmp, "w") as f:
            json.dump(counts, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _TRADECOUNT_FILE)
    finally:
        if os.path.exists(tmp):       # rename failed — never leave litter
            os.unlink(tmp)


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


def realized_annual_vol(bars: list[dict], period: int) -> float | None:
    """Annualized stdev of the last `period` daily log returns. None if thin."""
    closes = [b["close"] for b in bars if b.get("close")]
    if len(closes) < period + 1:
        return None
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - period, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((x - mean) ** 2 for x in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(252)


def vol_scale(bars: list[dict] | None, vcfg: dict,
              strategy: str | None = None) -> float:
    """Volatility-target sizing multiplier: target_vol / realized_vol, clamped.
    1.0 whenever disabled, scoped to other strategies, or data is too thin.
    Adopted 2026-07-18 for meanrev only (frozen-snapshot gate; hurt tsmom)."""
    if not vcfg.get("enabled") or not bars:
        return 1.0
    strats = vcfg.get("strategies")
    if strats and strategy not in strats:
        return 1.0
    realized = realized_annual_vol(bars, vcfg.get("period", 20))
    if not realized or realized <= 0:
        return 1.0
    scale = vcfg.get("annual_vol", 0.20) / realized
    return min(max(scale, vcfg.get("min_scale", 0.5)), vcfg.get("max_scale", 1.5))


def _risk_sizing_active(rcfg: dict, strategy: str | None,
                        price: float, stop_price: float | None) -> bool:
    """Stop-distance sizing applies only when enabled, scoped to this
    strategy, and an actual protective stop is known below the price."""
    if not rcfg.get("enabled") or not stop_price or stop_price >= price:
        return False
    strats = rcfg.get("strategies")
    return not strats or strategy in strats


def size_order(account: dict, price: float, cfg: dict,
               bars: list[dict] | None = None,
               strategy: str | None = None,
               stop_price: float | None = None) -> int:
    """Position sizing, then clamp by every cap. Returns whole-share qty.

    Two modes (per-strategy, gate-decided — see backtest_candidates.md §8):
      * notional (default): equity × risk_per_trade_pct, optionally scaled by
        the vol_target multiplier;
      * risk-based (risk_sizing.enabled + stop known): equity × risk_pct /
        stop-distance fraction, so every trade risks the same equity slice
        entry-to-stop. Replaces vol_target for that strategy — both normalize
        by realized vol and compounding them would double-count.
    """
    r = cfg["risk"]
    equity = account["equity"]

    rscfg = r.get("risk_sizing") or {}
    if _risk_sizing_active(rscfg, strategy, price, stop_price):
        stop_frac = (price - stop_price) / price
        dollars = equity * rscfg.get("risk_pct", 0.1) / 100 / stop_frac
    else:
        dollars = equity * r["risk_per_trade_pct"] / 100      # fixed fractional
        dollars *= vol_scale(bars, r.get("vol_target") or {}, strategy)
    dollars = min(dollars, r["max_order_value_usd"])           # per-order cap
    dollars = min(dollars, equity * r["max_position_pct"] / 100)  # concentration cap
    dollars = min(dollars, account["buying_power"])

    qty = int(dollars // price)
    return max(qty, 0)


def trail_stop(high_water: float, atr_value: float | None,
               cfg: dict, strategy: str | None = None) -> float | None:
    """Chandelier trail level: high-water-since-entry − trailing_atr_mult·ATR.
    None when the trail is off (mult 0/absent), scoped to other strategies
    (trailing_strategies — gate 2026-07-19 adopted it for tsmom ONLY), or ATR
    is unavailable. The caller RATCHETS — an existing stop is only ever
    replaced by a HIGHER one (backtest_candidates.md §7)."""
    b = cfg["risk"].get("brackets") or {}
    mult = b.get("trailing_atr_mult", 0)
    if not mult or not atr_value or atr_value <= 0:
        return None
    strats = b.get("trailing_strategies")
    if strats and strategy not in strats:
        return None
    return round(high_water - mult * atr_value, 2)


def rvol_blocked(bars: list, cfg: dict, strategy: str | None = None) -> bool:
    """§23: block an ENTRY whose bar lacks relative-volume confirmation.

    ONE implementation shared by live (`main.py`) and both simulators, on
    purpose. Four sim/live divergences have already cost real rework (missing
    rails §13, global counters §19b, fill timing §19a, symbol order §22); a
    filter re-implemented per call site would be the fifth.

    FAILS OPEN — a `None` rvol (short history, or a missing/zero volume
    baseline) permits the entry. A data gap must not silently halt all trading;
    the freshness and cross-check rails own that failure class, not this one.

    Threshold is per-strategy first, then global, then off:
        strategies.<name>.min_rvol  ->  risk.min_rvol  ->  0 (disabled)
    Never applied to exits: a position must always be able to leave.
    """
    threshold = None
    if strategy:
        threshold = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("min_rvol")
    if threshold is None:
        threshold = (cfg.get("risk") or {}).get("min_rvol", 0)
    if not threshold:
        return False
    period = int((cfg.get("risk") or {}).get("rvol_period", 20))
    # Local import: strategies/base.py imports nothing from risk, so this is
    # acyclic, but keeping it local keeps risk.py importable in isolation.
    from strategies.base import rvol as _rvol
    value = _rvol(bars, period)
    if value is None:
        return False                      # fail open — see docstring
    return value < float(threshold)


def cooldown_days_for(cfg: dict, strategy: str | None) -> int:
    """Re-entry cooldown days that apply to entries by this strategy
    (0 = none). Per-strategy scope — the 2026-07-19 gate adopted the
    cooldown for meanrev only (§9)."""
    ccfg = cfg["risk"].get("reentry_cooldown") or {}
    strats = ccfg.get("strategies")
    if strats and strategy not in strats:
        return 0
    return ccfg.get("days", 0)


def cooldown_blocked(last_exit_ts: str | None, now_ts: str,
                     cooldown_days: int) -> bool:
    """True when a NEW entry is still inside the same-ticker re-entry
    cooldown after that symbol's last exit (gate candidate, §9).
    Calendar days, matching the swing guard's semantics."""
    if cooldown_days <= 0 or not last_exit_ts:
        return False
    days = (datetime.fromisoformat(now_ts)
            - datetime.fromisoformat(last_exit_ts)).days
    return days < cooldown_days


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


def entry_drift_bps(signal_price: float, live_price: float) -> float:
    """Absolute drift between the price the signal saw and the live market,
    in basis points."""
    return abs(live_price - signal_price) / signal_price * 1e4


def entry_drift_ok(signal_price: float, live_price: float, cfg: dict) -> bool:
    """Order-level defense in depth behind bars_fresh (2026-07-21): even with
    fresh-looking bars, never ENTER when the live market has moved more than
    risk.max_entry_drift_bps away from the signal price — every 2026-07-16
    stale-bars fill (142-1725bps of phantom slippage) would have been blocked
    here. Entries only; exits are never blocked. <=0/absent disables."""
    cap = cfg["risk"].get("max_entry_drift_bps", 0)
    if cap <= 0 or signal_price <= 0:
        return True
    return entry_drift_bps(signal_price, live_price) <= cap


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


def portfolio_heat(open_trades: dict, cfg: dict, equity: float) -> float:
    """Total open stop-risk in dollars (2026-07-21): what the whole book
    loses if EVERY open stop is hit. Per position: qty x (entry - stop) from
    the recorded bracket stop; a position with no recorded stop contributes
    the standard per-trade risk fraction of equity (conservative fallback)."""
    heat = 0.0
    per_trade = equity * cfg["risk"].get("risk_per_trade_pct", 1.0) / 100
    for rec in (open_trades or {}).values():
        qty = rec.get("qty") or 0
        entry = rec.get("entry_price") or 0.0
        stop = (rec.get("order") or {}).get("stop_price")
        if stop and entry and qty:
            heat += qty * max(entry - stop, 0.0)
        elif qty:
            heat += per_trade
    return heat


def strategy_live_stats(closed_trades: list[dict], strategy: str) -> dict:
    """Live closed-trade count and profit factor for one strategy."""
    trades = [t for t in closed_trades if t.get("strategy") == strategy]
    gross_win = sum(t["pnl"] for t in trades if t.get("pnl", 0) > 0)
    gross_loss = -sum(t["pnl"] for t in trades if t.get("pnl", 0) < 0)
    pf = (gross_win / gross_loss if gross_loss > 0
          else (float("inf") if gross_win > 0 else None))
    return {"n": len(trades), "pf": pf}


def live_kill_blocked(closed_trades: list[dict], strategy: str | None,
                      cfg: dict) -> str | None:
    """Pre-registered live kill criteria (2026-07-21, enterprise-hardening):
    the live-side mirror of the backtest enablement gate. A strategy whose
    LIVE record over >= min_trades closed trades shows PF < pf_below stops
    ENTERING (exits always still run). Registered before any strategy is
    near the threshold, so it can't be argued with later. Returns the
    rejection reason, or None."""
    kcfg = cfg["risk"].get("live_kill") or {}
    if not kcfg.get("enabled") or not strategy:
        return None
    s = strategy_live_stats(closed_trades, strategy)
    min_trades = int(kcfg.get("min_trades", 15))
    pf_below = float(kcfg.get("pf_below", 0.8))
    if s["n"] >= min_trades and s["pf"] is not None and s["pf"] < pf_below:
        return (f"live kill: {strategy} PF {s['pf']:.2f} over {s['n']} live "
                f"closed trades is below {pf_below} (pre-registered criteria "
                f"— entries retired, exits unaffected)")
    return None


def daily_returns(bars: list[dict]) -> list[float]:
    """Close-to-close simple returns, oldest first."""
    closes = [b["close"] for b in bars]
    return [(b - a) / a for a, b in zip(closes, closes[1:]) if a]


def dated_daily_returns(bars: list[dict]) -> dict:
    """Close-to-close returns keyed by the (later) bar's calendar date, so two
    symbols with different-length histories align by DATE, not list position.
    Tail-aligning raw return lists (as the old correlation path did) pairs
    mismatched sessions whenever bar counts differ (trading halt, recent
    listing, per-symbol stale-drop)."""
    out: dict = {}
    prev = None
    for b in bars or []:
        c = b.get("close")
        day = (b.get("ts") or "")[:10]
        if prev is not None and prev > 0 and c is not None and day:
            out[day] = (c - prev) / prev
        prev = c
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 3:
        return None
    xs, ys = xs[-n:], ys[-n:]
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def correlated_position_count(cand_bars: list[dict], open_bars_map: dict,
                              lookback: int, threshold: float) -> int:
    """How many open positions co-move with the candidate (correlation heat,
    2026-07-21 — 'co-moving names are one bet'). Pairwise Pearson correlation
    of daily returns over the last `lookback` bars; positions with missing or
    too-short bars are skipped (fail-open per symbol)."""
    cand = dated_daily_returns(cand_bars)
    count = 0
    for bars in open_bars_map.values():
        other = dated_daily_returns(bars or [])
        shared = sorted(set(cand) & set(other))[-lookback:]
        if len(shared) < 3:
            continue  # too little overlap — fail open per position
        rho = _pearson([cand[d] for d in shared], [other[d] for d in shared])
        if rho is not None and rho >= threshold:
            count += 1
    return count


def pure_checks(action: str, symbol: str, qty: int, price: float,
                account: dict, positions: dict, cfg: dict,
                regime_label: str | None = None,
                strategy: str | None = None,
                strategy_open: int | None = None):
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
        # Per-strategy slot allocation (§13). The global cap above is still a
        # hard ceiling over the whole book; this only ever makes a strategy's
        # own limit TIGHTER. Rationale (§12 + live evidence): a single shared
        # pool lets a slow trend strategy hold every slot for weeks and starve a
        # fast mean-reversion one, which is what stalled evidence velocity.
        # Absent config, behaviour is exactly as before.
        strat_cap = ((cfg.get("strategies") or {}).get(strategy) or {}
                     ).get("max_open_positions") if strategy else None
        if (strat_cap and strategy_open is not None
                and strategy_open >= strat_cap and symbol not in positions):
            raise RiskRejection(
                f"max open positions for {strategy} reached ({strat_cap})")
        existing = positions.get(symbol, {}).get("market_value", 0.0)
        if existing + order_value > account["equity"] * r["max_position_pct"] / 100:
            raise RiskRejection(f"would exceed {r['max_position_pct']}% concentration cap on {symbol}")

        recfg = r.get("regime_exposure") or {}
        if (recfg.get("enabled") and regime_label
                and regime_label.startswith("down")):
            gross = sum(p.get("market_value", 0.0) for p in positions.values())
            cap = account["equity"] * recfg.get("down_max_gross_pct", 50) / 100
            if gross + order_value > cap:
                raise RiskRejection(
                    f"down-regime exposure cap: gross ${gross + order_value:,.0f} "
                    f"would exceed {recfg.get('down_max_gross_pct', 50)}% of equity")

    if action == "sell" and symbol not in positions:
        raise RiskRejection(f"no position in {symbol} to sell (state desync guard)")


def pre_trade_checks(action: str, symbol: str, qty: int, price: float,
                     account: dict, positions: dict, cfg: dict,
                     entry_ts: str | None = None,
                     regime_label: str | None = None,
                     bars_map: dict | None = None,
                     open_trades: dict | None = None,
                     candidate_stop: float | None = None,
                     strategy: str | None = None):
    """Last-stage gate every order must pass. Raises RiskRejection with a reason."""
    if check_halt():
        raise RiskRejection("HALT file present — trading disabled")
    if _trades_today() >= cfg["risk"]["max_trades_per_day"]:
        raise RiskRejection(f"max trades per day reached ({cfg['risk']['max_trades_per_day']})")

    # Count THIS strategy's currently-open positions for its slot allocation.
    strategy_open = None
    if strategy and open_trades is not None:
        strategy_open = sum(1 for rec in open_trades.values()
                            if rec.get("strategy") == strategy)
    pure_checks(action, symbol, qty, price, account, positions, cfg,
                regime_label=regime_label, strategy=strategy,
                strategy_open=strategy_open)

    # Portfolio heat cap (2026-07-21): total open stop-risk plus this entry's
    # risk must stay under max_portfolio_heat_pct of equity. Entries only.
    heat_cap_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
    if action == "buy" and heat_cap_pct > 0 and open_trades is not None:
        heat = portfolio_heat(open_trades, cfg, account["equity"])
        new_risk = (qty * max(price - candidate_stop, 0.0) if candidate_stop
                    else account["equity"]
                    * cfg["risk"].get("risk_per_trade_pct", 1.0) / 100)
        cap = account["equity"] * heat_cap_pct / 100
        if heat + new_risk > cap:
            raise RiskRejection(
                f"portfolio heat cap: open stop-risk ${heat:,.0f} + this "
                f"trade's ${new_risk:,.0f} would exceed "
                f"{heat_cap_pct}% of equity (${cap:,.0f})")

    # Correlation heat cap (entries only; needs the cycle's bars). Fail-open
    # when bars are unavailable — the per-symbol cap above still applies.
    ccfg = cfg["risk"].get("correlation_cap") or {}
    if (action == "buy" and ccfg.get("enabled") and bars_map
            and bars_map.get(symbol)):
        open_bars = {s: bars_map.get(s) for s in positions if s != symbol}
        n = correlated_position_count(bars_map[symbol], open_bars,
                                      int(ccfg.get("lookback", 60)),
                                      float(ccfg.get("threshold", 0.85)))
        if n >= int(ccfg.get("max_correlated", 2)):
            raise RiskRejection(
                f"correlation cap: {n} open positions already move with "
                f"{symbol} (corr>={ccfg.get('threshold', 0.85)}) — "
                f"co-moving names are one bet")

    if action == "sell":
        swing_guard(entry_ts, cfg)
