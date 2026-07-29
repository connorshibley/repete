"""Offline backtest harness — replays historical daily bars through the SAME
deterministic code the live bot uses (`strategy.generate_signal`, `strategy.atr`,
`risk.size_order`, `risk.pure_checks`) with a fee/slippage model.

Isolation guarantees:
  * never imports Ledger/Memory/llm/x_poster — the live audit trail, learnings
    and X posting are untouchable from here;
  * the only file it writes is the trials log (memory/backtest_trials.jsonl,
    append-only — every parameter variant tried is recorded, per the
    False-Strategy-Theorem discipline in GUIDE.md);
  * `broker` is imported lazily inside fetch_bars() only when no --bars-file is
    given, and only for READ-ONLY market data.

Anti-look-ahead: signals are computed on the close of bar i and filled at the
OPEN of bar i+1, with slippage charged against you on every fill. Bracket legs
are checked intrabar pessimistically (stop before take-profit when both touch).

Usage:
    python src/backtest.py --bars-file data/bars.json --fast 5 10 --slow 20 30
    python src/backtest.py --symbols SPY --start 2020-01-01 --end 2026-01-01
"""
import argparse
import csv
import gzip
import itertools
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import strategies
import strategy
import risk
import regime as regime_mod
import earnings as earnings_mod
import intraday
import judge_model


# ---------------------------------------------------------------- data models

@dataclass
class SimTrade:
    symbol: str
    entry_ts: str
    entry_price: float
    qty: int
    exit_ts: str = ""
    exit_price: float = 0.0
    exit_reason: str = ""
    fees: float = 0.0

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.qty - self.fees


@dataclass
class SimAccount:
    cash: float
    positions: dict = field(default_factory=dict)
    # symbol -> {"qty", "avg_entry", "entry_ts", "stop", "tp", "trade": SimTrade}

    def equity(self, prices: dict) -> float:
        held = sum(p["qty"] * prices.get(s, p["avg_entry"])
                   for s, p in self.positions.items())
        return self.cash + held

    def account_dict(self, prices: dict) -> dict:
        """Shape risk.size_order / risk.pure_checks expect."""
        eq = self.equity(prices)
        return {"equity": eq, "cash": self.cash, "last_equity": eq,
                "buying_power": self.cash}

    def positions_dict(self, prices: dict) -> dict:
        return {s: {"qty": p["qty"],
                    "market_value": p["qty"] * prices.get(s, p["avg_entry"])}
                for s, p in self.positions.items()}


@dataclass
class Result:
    params: dict
    start: str
    end: str
    n_trades: int
    n_guard_skipped_exits: int
    total_return_pct: float
    buy_hold_return_pct: float
    buy_hold_max_drawdown_pct: float
    avg_deployment_pct: float   # mean % of equity actually invested (rails cap it)
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    n_heat_blocked: int = 0     # entries stopped by the portfolio-heat cap
    n_corr_blocked: int = 0     # entries stopped by the correlation cap
    n_fill_fallback: int = 0    # §25: fills that had no intraday bar and
                                # reverted to the daily open (never silent)
    census: dict = field(default_factory=dict)   # §40: signal -> outcome census
    deployment_zero_pct: float = 0.0   # § 40: share of bars fully in cash
    deployment_median_pct: float = 0.0
    deployment_max_pct: float = 0.0
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    @property
    def symbols_traded(self) -> set:
        """Distinct symbols this run actually opened a position in.

        DERIVED, not stored, on purpose. §27's capacity clause turns on this
        number, and a stored field written at construction time is one more
        thing that can silently disagree with `trades` — which is the exact
        class of bug §13, §20 and §24 all turned out to be. Computed from the
        trade list, it cannot drift.
        """
        return {t.symbol for t in self.trades}

    @property
    def n_symbols_traded(self) -> int:
        return len(self.symbols_traded)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("equity_curve")
        d.pop("trades")
        # asdict() serialises FIELDS only, never properties — without this line
        # every gate report silently omits the reachability number.
        d["n_symbols_traded"] = self.n_symbols_traded
        return d


# ------------------------------------------------------------------- metrics

def max_drawdown(curve: list) -> float:
    """Largest peak-to-trough decline, as a positive percentage."""
    peak, worst = float("-inf"), 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100)
    return worst


def profit_factor(trades: list) -> float:
    """Gross wins / gross losses. inf when there are wins but no losses."""
    wins = sum(t.pnl for t in trades if t.pnl > 0)
    losses = -sum(t.pnl for t in trades if t.pnl < 0)
    if losses == 0:
        return float("inf") if wins > 0 else 0.0
    return wins / losses


def buy_and_hold_drawdown(sym_bars: dict) -> float:
    """Max drawdown of an equal-weighted buy-and-hold of the same universe."""
    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    if not all_ts:
        return 0.0
    first_close: dict = {}
    last_rel: dict = {}
    curve = []
    by_ts = {sym: {b["ts"]: b["close"] for b in bars}
             for sym, bars in sym_bars.items()}
    for ts in all_ts:
        for sym in sym_bars:
            c = by_ts[sym].get(ts)
            if c is not None:
                first_close.setdefault(sym, c)
                last_rel[sym] = c / first_close[sym]
        if last_rel:
            curve.append(sum(last_rel.values()) / len(last_rel))
    return max_drawdown(curve)


def buy_and_hold_return(bars: list, slippage_bps: float, fee: float,
                        cash: float = 100_000.0) -> float:
    """Buy at the second bar's open (same information timing as the strategy's
    first possible fill), sell at the last close. Slippage + fees applied."""
    if len(bars) < 2:
        return 0.0
    buy = bars[1]["open"] * (1 + slippage_bps / 1e4)
    sell = bars[-1]["close"] * (1 - slippage_bps / 1e4)
    qty = int((cash - fee) // buy)
    if qty <= 0:
        return 0.0
    return ((sell - buy) * qty - 2 * fee) / cash * 100


# --------------------------------------------------------------- bar loading

def load_bars_file(path: str) -> dict:
    """JSON ({symbol: [bar,...]} or a bare list for one symbol named FILE) or
    CSV (columns: symbol?,ts,open,high,low,close,volume) -> broker.bars shape.

    `.json.gz` is accepted so the committed gate snapshots stay ~1.8 MB rather
    than ~7.8 MB (see scripts/build_snapshot.py)."""
    if path.endswith((".json", ".json.gz")):
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            data = json.load(f)
        if isinstance(data, list):
            return {"FILE": data}
        return data
    out: dict = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            sym = row.get("symbol", "FILE")
            out.setdefault(sym, []).append({
                "ts": row["ts"], "open": float(row["open"]),
                "high": float(row["high"]), "low": float(row["low"]),
                "close": float(row["close"]), "volume": float(row.get("volume", 0))})
    return out


def fetch_bars(symbols: list, start: str, end: str) -> dict:
    """READ-ONLY market data via Alpaca (lazy import; needs .env keys)."""
    from dotenv import load_dotenv
    load_dotenv()
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    client = StockHistoricalDataClient(os.environ["ALPACA_API_KEY"],
                                       os.environ["ALPACA_SECRET_KEY"])
    resp = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=symbols, timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=datetime.fromisoformat(start), end=datetime.fromisoformat(end)))
    return {sym: [{"ts": b.timestamp.isoformat(), "open": b.open, "high": b.high,
                   "low": b.low, "close": b.close, "volume": b.volume}
                  for b in rows]
            for sym, rows in resp.data.items()}


# ---------------------------------------------------------------- simulation

def _cal_days(entry_ts: str, now_ts: str) -> int:
    return (datetime.fromisoformat(now_ts) - datetime.fromisoformat(entry_ts)).days


BRACKET_KEYS = ("stop_atr_mult", "take_profit_atr_mult",
                "stop_atr_mult_high_vol", "trailing_atr_mult")


_RCFG_DEFAULT = {"sma_period": 50, "vol_period": 20,
                 "vol_low": 0.15, "vol_high": 0.25}


def regime_label_series(spy_bars: list[dict], rcfg: dict) -> dict:
    """ts -> full regime label (e.g. "down/low") from SPY history up to each
    bar (no look-ahead). Used by the down-regime exposure-cap candidate."""
    out = {}
    for i in range(len(spy_bars)):
        r = regime_mod.compute_regime(spy_bars[:i + 1], rcfg)
        if r:
            out[spy_bars[i]["ts"]] = r["label"]
    return out


def measured_slippage_bps(records: list[dict], min_fills: int = 10,
                          sane_bps: float = 100.0) -> float | None:
    """Median measured slippage from fill_quality ledger records, for
    calibrating the backtest cost model to reality. Fills with |bps| beyond
    sane_bps are artifacts (e.g. the stale-signal-price era) and excluded;
    returns None until min_fills clean measurements exist."""
    slips = sorted(r["slippage_bps"] for r in records
                   if r.get("type") == "fill_quality"
                   and abs(r.get("slippage_bps", 0)) <= sane_bps)
    if len(slips) < min_fills:
        return None
    mid = len(slips) // 2
    med = slips[mid] if len(slips) % 2 else (slips[mid - 1] + slips[mid]) / 2
    return round(max(med, 0.0), 2)  # never model negative cost


def vol_bucket_series(spy_bars: list[dict], rcfg: dict) -> dict:
    """ts -> "low"|"mid"|"high" from SPY history up to each bar (no
    look-ahead). Empty dict when SPY isn't in the tested universe — the
    vol-conditioned stop then fails open to the base multiplier."""
    out = {}
    for i in range(len(spy_bars)):
        r = regime_mod.compute_regime(spy_bars[:i + 1], rcfg)
        if r:
            out[spy_bars[i]["ts"]] = r["vol"]
    return out


def simulate(sym_bars: dict, cfg: dict, params: dict | None = None,
             start_cash: float = 100_000.0,
             strategy_name: str = "ma_crossover",
             earnings: dict | None = None) -> Result:
    """Replay bars through the live strategy/risk code for ONE named strategy.
    `params` overlays that strategy's settings (plus bracket multipliers)
    onto a COPY of cfg — the real config is never mutated.
    `earnings` maps symbol -> sorted ISO earnings dates; only consulted when
    the earnings_blackout_days param/config is set (mirrors the live filter).
    """
    params = params or {}
    cfg = json.loads(json.dumps(cfg))  # deep copy — never mutate the caller's config
    smap = cfg.setdefault("strategies", {})
    sparams = smap.setdefault(strategy_name, {"enabled": True})
    if strategy_name == "ma_crossover":  # legacy defaults from strategy: section
        sparams.setdefault("fast_period", cfg["strategy"].get("fast_period", 10))
        sparams.setdefault("slow_period", cfg["strategy"].get("slow_period", 30))
    for k, v in params.items():
        if k not in BRACKET_KEYS and k != "strategy":
            sparams[k] = v
    bcfg = cfg["risk"].setdefault("brackets", {})
    if "stop_atr_mult" in params:
        bcfg.update(enabled=params["stop_atr_mult"] > 0,
                    stop_atr_mult=params["stop_atr_mult"])
        bcfg.setdefault("atr_period", 14)
    if "take_profit_atr_mult" in params:
        bcfg["take_profit_atr_mult"] = params["take_profit_atr_mult"]
    if "stop_atr_mult_high_vol" in params:
        bcfg["stop_atr_mult_high_vol"] = params["stop_atr_mult_high_vol"]
    if "trailing_atr_mult" in params:
        bcfg["trailing_atr_mult"] = params["trailing_atr_mult"]
    t_strats = bcfg.get("trailing_strategies")
    trail_on = (bcfg.get("trailing_atr_mult", 0) > 0
                and (not t_strats or strategy_name in t_strats))
    cooldown_days = risk.cooldown_days_for(cfg, strategy_name)

    smod = strategies.REGISTRY[strategy_name]
    needs_xs = smod.NEEDS_CROSS_SECTION

    bt = cfg.get("backtest", {})
    slip = bt.get("slippage_bps", 5)
    fee = bt.get("fee_per_trade_usd", 0.0)
    min_days = cfg["risk"].get("min_holding_days", 0)

    # Per-date vol bucket from SPY (only needed for the vol-conditioned stop;
    # empty => bracket_prices falls back to the base multiplier).
    rcfg = cfg.get("learning", {}).get("regime", _RCFG_DEFAULT)
    vol_series = (vol_bucket_series(sym_bars.get("SPY", []), rcfg)
                  if bcfg.get("stop_atr_mult_high_vol") else {})
    regime_labels = (regime_label_series(sym_bars.get("SPY", []), rcfg)
                     if (cfg["risk"].get("regime_exposure") or {}).get("enabled")
                     else {})

    acct = SimAccount(cash=start_cash)
    closed: list = []
    curve: list = []
    deployment: list = []
    guard_skips = 0
    n_heat_blocked = 0      # §13: rails the sim previously ignored
    n_corr_blocked = 0
    sim_peak = 0.0          # §31: running equity high-water for the drawdown rail
    fills_by_date: dict = {}
    last_exit: dict = {}  # symbol -> exit ts (re-entry cooldown, §9)
    pending: list = []  # (symbol, action) to fill at that symbol's next open

    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    idx = {sym: {b["ts"]: i for i, b in enumerate(bars)}
           for sym, bars in sym_bars.items()}
    last_close: dict = {}

    def _exit(sym, price, ts, reason):
        pos = acct.positions.pop(sym)
        fill = price * (1 - slip / 1e4)
        acct.cash += pos["qty"] * fill - fee
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, fill, reason
        t.fees += fee
        last_exit[sym] = ts
        closed.append(t)

    # §22: caller order is honoured here (a test pins it). §24 rotates that
    # order per DAY when enabled — same shared helper live uses.
    base_order = list(sym_bars)

    for ts in all_ts:
        today = {sym: sym_bars[sym][idx[sym][ts]]
                 for sym in risk.scan_order(base_order, cfg, ts)
                 if ts in idx[sym]}
        for sym, bar in today.items():
            last_close[sym] = bar["close"]

        # 1. Fill orders queued on the previous bar, at this bar's open.
        still_pending = []
        for sym, action in pending:
            if sym not in today:
                still_pending.append((sym, action))
                continue
            bar = today[sym]
            date = ts[:10]
            if action == "buy":
                # §29: 0 disables, exactly as pre_trade_checks does live. If
                # only one of the two honoured 0 this would be divergence #9.
                _cap_day = cfg["risk"].get("max_trades_per_day") or 0
                if _cap_day and fills_by_date.get(date, 0) >= _cap_day:
                    continue  # rate limit across symbols, mirroring the live bot
                fill = bar["open"] * (1 + slip / 1e4)
                i = idx[sym][ts]
                hist = sym_bars[sym][:i + 1]
                # Bracket prices BEFORE sizing (mirrors the live pipeline) so
                # stop-distance sizing (§8) sees the same stop it will place.
                prices = risk.bracket_prices(
                    fill, strategy.atr(hist, bcfg.get("atr_period", 14)), cfg,
                    vol_bucket=vol_series.get(ts))
                stop, tp = prices if prices else (None, None)
                qty = risk.size_order(acct.account_dict(last_close), fill, cfg,
                                      bars=hist,
                                      strategy=strategy_name,
                                      stop_price=stop)
                # §29 / divergence #8: live cuts 53.5% of buys before they are
                # placed. Off by default, so every gate run before 2026-07-26
                # reproduces byte-identically.
                qty = judge_model.apply(qty, sym, ts, cfg)
                try:
                    # Single-strategy sim: every open position belongs to the
                    # strategy under test, so its slot allocation (§13) is
                    # exactly len(positions).
                    # §31: the sim keeps its OWN running peak — live reads a
                    # file, the sim cannot. The ARITHMETIC is shared
                    # (risk.drawdown_pct), so the two cannot disagree about
                    # what a drawdown is, only about where the peak came from.
                    sim_peak = max(sim_peak, acct.equity(last_close))
                    risk.pure_checks("buy", sym, qty, fill,
                                     acct.account_dict(last_close),
                                     acct.positions_dict(last_close), cfg,
                                     regime_label=regime_labels.get(ts),
                                     strategy=strategy_name,
                                     strategy_open=len(acct.positions),
                                     peak_equity=sim_peak)
                    # The heat + correlation caps live in pre_trade_checks,
                    # which the sim cannot call (it does HALT/trade-count file
                    # I/O). They were therefore NEVER simulated: every gate
                    # through §12 was measured with fewer rails than live runs
                    # with, so the sim overstated achievable trade count. Apply
                    # them here so sim and live agree.
                    _sim_open_trades = {
                        s: {"qty": p["qty"], "entry_price": p["avg_entry"],
                            "order": {"stop_price": p.get("stop")}}
                        for s, p in acct.positions.items()}
                    heat_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
                    if heat_pct:
                        eq = acct.equity(last_close)
                        heat = risk.portfolio_heat(_sim_open_trades, cfg, eq)
                        new_risk = (qty * max(fill - stop, 0.0) if stop
                                    else eq * cfg["risk"].get(
                                        "risk_per_trade_pct", 1.0) / 100)
                        if heat + new_risk > eq * heat_pct / 100:
                            n_heat_blocked += 1
                            continue
                    ccfg = cfg["risk"].get("correlation_cap") or {}
                    if ccfg.get("enabled") and acct.positions:
                        open_bars = {s: sym_bars[s][:idx[s][ts] + 1]
                                     for s in acct.positions
                                     if s != sym and ts in idx.get(s, {})}
                        n_corr = risk.correlated_position_count(
                            hist, open_bars, int(ccfg.get("lookback", 60)),
                            float(ccfg.get("threshold", 0.85)))
                        if n_corr >= int(ccfg.get("max_correlated", 2)):
                            n_corr_blocked += 1
                            continue
                except risk.RiskRejection:
                    continue
                acct.cash -= qty * fill + fee
                acct.positions[sym] = {
                    "qty": qty, "avg_entry": fill, "entry_ts": ts,
                    "stop": stop, "tp": tp, "hw": fill,
                    "trade": SimTrade(sym, ts, fill, qty, fees=fee)}
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
            elif sym in acct.positions:
                _exit(sym, bar["open"], ts, "strategy_sell")
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
        pending = still_pending

        # 2. Bracket legs, intrabar, pessimistic: stop wins when both touch.
        # The chandelier trail (§7) ratchets AFTER the exit check, from data
        # through today — so today's high can never raise a stop that then
        # fires on today's own low (no intrabar look-ahead; matches the live
        # once-per-cycle update timing).
        for sym in list(acct.positions):
            if sym not in today:
                continue
            bar, pos = today[sym], acct.positions[sym]
            if pos["stop"] is not None and bar["low"] <= pos["stop"]:
                _exit(sym, pos["stop"], ts, "stop_loss")
                continue
            if pos["tp"] is not None and bar["high"] >= pos["tp"]:
                _exit(sym, pos["tp"], ts, "take_profit")
                continue
            if trail_on:
                pos["hw"] = max(pos.get("hw", pos["avg_entry"]), bar["high"])
                hist = sym_bars[sym][:idx[sym][ts] + 1]
                t_stop = risk.trail_stop(
                    pos["hw"], strategy.atr(hist, bcfg.get("atr_period", 14)),
                    cfg, strategy=strategy_name)
                if t_stop is not None and (pos["stop"] is None
                                           or t_stop > pos["stop"]):
                    pos["stop"] = t_stop

        # 3. Signals on data up to and including this bar's close.
        xs_ctx = None
        if needs_xs:
            hist_by_sym = {s: sym_bars[s][:idx[s][ts] + 1]
                           for s in sym_bars if ts in idx[s]}
            xs_ctx = smod.prepare(hist_by_sym, sparams)
        entry_cap = sparams.get("max_entries_per_cycle", 0)
        blackout_days = sparams.get("earnings_blackout_days", 0)
        buys_queued_today = 0
        for sym, bar in today.items():
            i = idx[sym][ts]
            hist = sym_bars[sym][:i + 1]
            holding = sym in acct.positions
            entry_ts = acct.positions[sym]["entry_ts"] if holding else None
            sig = strategies.generate(strategy_name, sym, hist, cfg, holding,
                                      cross_section=xs_ctx, entry_ts=entry_ts)
            if sig.action == "buy" and not holding:
                # re-entry cooldown (§9) — same calendar-day rule as main.py
                if cooldown_days and risk.cooldown_blocked(
                        last_exit.get(sym), ts, cooldown_days):
                    continue
                # earnings blackout — same rule as the live filter in main.py
                if blackout_days and earnings and earnings_mod.next_within(
                        earnings.get(sym, []), ts, blackout_days):
                    continue
                # §23 relative-volume confirmation (entries only; fails open)
                if risk.rvol_blocked(hist, cfg, strategy_name):
                    continue
                # Unprotectable entry (2026-07-27): an ATR-derived stop at or
                # below zero means brackets() returns None and the position runs
                # with NO stop. Same helper as live and the ensemble.
                if risk.unprotectable_entry(
                        hist[-1]["close"], strategies.atr(hist, 14), cfg):
                    continue
                # per-cycle entry cap, first-come in symbol order — the same
                # semantics as the live loop in main.py
                if entry_cap and buys_queued_today >= entry_cap:
                    continue
                pending.append((sym, "buy"))
                buys_queued_today += 1
            elif sig.action == "sell" and holding:
                # sim swing guard — same calendar-day semantics as risk.swing_guard
                if min_days and _cal_days(acct.positions[sym]["entry_ts"], ts) < min_days:
                    guard_skips += 1
                    continue
                pending.append((sym, "sell"))

        eq = acct.equity(last_close)
        curve.append(round(eq, 2))
        deployment.append((eq - acct.cash) / eq if eq > 0 else 0.0)

    # Liquidate leftovers at the final close so the metrics see every trade.
    final_ts = all_ts[-1] if all_ts else ""
    for sym in list(acct.positions):
        _exit(sym, last_close[sym], final_ts, "end_of_data")

    total_ret = (acct.cash - start_cash) / start_cash * 100
    wins = [t for t in closed if t.pnl > 0]
    bh = (sum(buy_and_hold_return(b, slip, fee, start_cash / len(sym_bars))
              for b in sym_bars.values()) / len(sym_bars)) if sym_bars else 0.0

    return Result(
        params={**params, "strategy": strategy_name},
        start=all_ts[0] if all_ts else "", end=final_ts,
        n_trades=len(closed), n_guard_skipped_exits=guard_skips,
        n_heat_blocked=n_heat_blocked, n_corr_blocked=n_corr_blocked,
        total_return_pct=round(total_ret, 3),
        buy_hold_return_pct=round(bh, 3),
        buy_hold_max_drawdown_pct=round(buy_and_hold_drawdown(sym_bars), 3),
        avg_deployment_pct=round(100 * sum(deployment) / len(deployment), 2)
        if deployment else 0.0,
        win_rate=round(len(wins) / len(closed), 3) if closed else 0.0,
        profit_factor=round(profit_factor(closed), 3),
        max_drawdown_pct=round(max_drawdown(curve), 3),
        equity_curve=curve, trades=closed)


# ------------------------------------------------------- ensemble simulation

def simulate_ensemble(sym_bars: dict, cfg: dict, start_cash: float = 100_000.0,
                      earnings: dict | None = None,
                      strategy_overrides: dict | None = None,
                      hour_index: dict | None = None,
                      fill_hour: int | None = None,
                      credit: dict | None = None) -> Result:
    """Replay bars through ALL enabled strategies at once, sharing the rails.

    Why this exists (2026-07-23)
    ----------------------------
    `simulate()` above replays ONE strategy in isolation. The live bot runs the
    whole ensemble against a SINGLE set of rails — one cash balance, one global
    `max_open_positions` ceiling, one `max_trades_per_day` counter, one
    portfolio-heat budget, one correlation cap. So every gate verdict in
    `knowledge/backtest_candidates.md` describes a bot that does not exist.

    That is not a hypothetical. §19b tried to gate `max_trades_per_day` and got
    four BYTE-IDENTICAL arms, because `risk._trades_today()` counts the whole
    ensemble while the simulator was watching one strategy that never came near
    the cap. It is the third sim/live divergence found in two days, after the
    missing heat/correlation caps (§13) and the fill-timing gap (§19a).

    Relationship to simulate()
    --------------------------
    This is a SIBLING, not a replacement. `simulate()` stays byte-stable so the
    §14–§19 numbers remain re-derivable. The correctness argument is that with
    exactly one strategy enabled, this function must reproduce `simulate()`
    trade-for-trade — `tests/test_ensemble_sim.py` asserts precisely that, for
    each strategy. Ensemble behaviour is then single-strategy behaviour plus
    contention, and nothing else.

    Shared vs per-strategy
    ----------------------
    shared:        cash/equity, global max_open_positions, the per-day trade
                   counter, portfolio heat, the correlation cap
    per-strategy:  params, re-entry cooldown (§9), the chandelier trailing gate
                   (§7, tsmom only), and the §13 slot allocation

    `strategy_overrides` maps strategy name -> params to overlay (and may set
    `enabled`), so a gate can vary one strategy's settings — or turn one off —
    without touching the live config.
    """
    cfg = json.loads(json.dumps(cfg))    # never mutate the caller's config
    smap = cfg.setdefault("strategies", {})
    for name, over in (strategy_overrides or {}).items():
        smap.setdefault(name, {"enabled": True}).update(over)
    # Legacy ma_crossover defaults live in the `strategy:` section.
    if "ma_crossover" in smap:
        smap["ma_crossover"].setdefault(
            "fast_period", cfg["strategy"].get("fast_period", 10))
        smap["ma_crossover"].setdefault(
            "slow_period", cfg["strategy"].get("slow_period", 30))

    # Entry order = live priority order. Contention on a symbol is resolved
    # first-come in this order, exactly as main.py's loop does.
    active = strategies.enabled(cfg)
    if not active:
        raise ValueError("no enabled strategies — nothing to simulate")

    bcfg = cfg["risk"].setdefault("brackets", {})
    t_strats = bcfg.get("trailing_strategies")
    trail_mult_on = bcfg.get("trailing_atr_mult", 0) > 0

    def _trails(owner: str) -> bool:
        """Per-POSITION now, not per-run: the chandelier is gated to tsmom, so
        a shared run has to ask who owns the position."""
        return trail_mult_on and (not t_strats or owner in t_strats)

    cooldowns = {n: risk.cooldown_days_for(cfg, n) for n, _ in active}

    bt = cfg.get("backtest", {})
    slip = bt.get("slippage_bps", 5)
    fee = bt.get("fee_per_trade_usd", 0.0)
    min_days = cfg["risk"].get("min_holding_days", 0)

    rcfg = cfg.get("learning", {}).get("regime", _RCFG_DEFAULT)
    vol_series = (vol_bucket_series(sym_bars.get("SPY", []), rcfg)
                  if bcfg.get("stop_atr_mult_high_vol") else {})
    regime_labels = (regime_label_series(sym_bars.get("SPY", []), rcfg)
                     if (cfg["risk"].get("regime_exposure") or {}).get("enabled")
                     else {})

    acct = SimAccount(cash=start_cash)
    closed: list = []
    curve: list = []
    deployment: list = []
    guard_skips = 0
    n_heat_blocked = 0
    n_corr_blocked = 0
    # §40 BLOCK CENSUS. `signals` is the denominator that did not exist before:
    # every buy a strategy actually emitted. Each one then either executes or
    # lands in exactly one `blocked` bucket, and the two must sum back to
    # `signals` — asserted at the end of the run, not merely tested.
    #
    # Without a denominator, "blocked 40 times" cannot be read against anything.
    # Without per-rail buckets the reason was discarded at the one moment it was
    # known: every RiskRejection funnelled into a single counter. Across four
    # periods this bot sits ~91% in cash and nobody could say why.
    #
    # Only simulate_ensemble is instrumented — it is what every gate runs.
    census = {"signals": 0, "executed": 0, "blocked": {}}

    def _blocked(reason: str):
        census["blocked"][reason] = census["blocked"].get(reason, 0) + 1

    sim_peak = 0.0          # §31: running equity high-water for the drawdown rail
    fills_by_date: dict = {}
    last_exit: dict = {}          # (strategy, symbol) -> exit ts; cooldown is
                                  # per-strategy, matching risk.cooldown_days_for
    pending: list = []            # (symbol, action, owner)
    by_strategy: dict = {n: {"trades": 0, "pnl": 0.0, "blocked": 0}
                         for n, _ in active}

    # SYMBOL ORDER IS LOAD-BEARING (found 2026-07-23, the hard way). Contention
    # is resolved first-come in symbol order, so whoever is scanned first gets
    # first refusal on the shared slots and the daily fill budget. Live builds
    # its scan list as `list(cfg["symbols"])` + extras (main.py), i.e. CONFIG
    # ORDER — while a snapshot loaded from disk arrives alphabetically sorted.
    # Running the same config in the two orders moved OOS return by 2.66pp.
    # Mirror live: config order first, then anything else the caller passed.
    scan_order = [s for s in cfg.get("symbols", []) if s in sym_bars]
    scan_order += [s for s in sym_bars if s not in set(scan_order)]

    all_ts = sorted({b["ts"] for bars in sym_bars.values() for b in bars})
    idx = {sym: {b["ts"]: i for i, b in enumerate(bars)}
           for sym, bars in sym_bars.items()}
    last_close: dict = {}

    # §25 intraday fill timing. `fill_hour` is None for every pre-§25 caller,
    # so this path is inert and results stay byte-identical (a test pins that).
    # When set, fills land at that ET hour's open instead of the daily open —
    # and a missing hourly bar falls back to the daily open and is COUNTED,
    # never silently substituted, so thin coverage cannot masquerade as a result.
    n_fill_fallback = 0

    def _fill_open(sym, bar, ts):
        nonlocal n_fill_fallback
        if fill_hour is None or hour_index is None:
            return bar["open"]
        px = intraday.fill_price(hour_index, sym, ts[:10], fill_hour)
        if px is None:
            n_fill_fallback += 1
            return bar["open"]
        return px

    def _exit(sym, price, ts, reason):
        pos = acct.positions.pop(sym)
        fill = price * (1 - slip / 1e4)
        acct.cash += pos["qty"] * fill - fee
        t = pos["trade"]
        t.exit_ts, t.exit_price, t.exit_reason = ts, fill, reason
        t.fees += fee
        owner = pos["owner"]
        last_exit[(owner, sym)] = ts
        stats = by_strategy.setdefault(owner, {"trades": 0, "pnl": 0.0,
                                               "blocked": 0})
        stats["trades"] += 1
        stats["pnl"] += t.pnl
        closed.append(t)

    for ts in all_ts:
        # §24: rotate that config order per DAY, via the same shared helper
        # live calls, so first refusal circulates instead of being fixed by
        # position in config.yaml.
        today = {sym: sym_bars[sym][idx[sym][ts]]
                 for sym in risk.scan_order(scan_order, cfg, ts)
                 if ts in idx[sym]}
        for sym, bar in today.items():
            last_close[sym] = bar["close"]

        # 1. Fill orders queued on the previous bar, at this bar's open.
        still_pending = []
        for sym, action, owner in pending:
            if sym not in today:
                still_pending.append((sym, action, owner))
                continue
            bar = today[sym]
            date = ts[:10]
            if action == "buy":
                # SHARED rate limit — the whole point of this function. One
                # strategy exhausting the daily cap blocks the others.
                # §29: 0 disables, exactly as pre_trade_checks does live. If
                # only one of the two honoured 0 this would be divergence #9.
                _cap_day = cfg["risk"].get("max_trades_per_day") or 0
                if _cap_day and fills_by_date.get(date, 0) >= _cap_day:
                    by_strategy[owner]["blocked"] += 1
                    _blocked("daily_cap")
                    continue
                if sym in acct.positions:
                    # Counted, not silent: this was the one drop the old
                    # counters ignored entirely, so a book full of contended
                    # names looked identical to no signals at all.
                    _blocked("already_held")
                    continue          # another strategy already claimed it
                fill = _fill_open(sym, bar, ts) * (1 + slip / 1e4)
                i = idx[sym][ts]
                hist = sym_bars[sym][:i + 1]
                prices = risk.bracket_prices(
                    fill, strategy.atr(hist, bcfg.get("atr_period", 14)), cfg,
                    vol_bucket=vol_series.get(ts))
                stop, tp = prices if prices else (None, None)
                qty = risk.size_order(acct.account_dict(last_close), fill, cfg,
                                      bars=hist, strategy=owner,
                                      stop_price=stop)
                # §29 / divergence #8 — see the single-strategy path above.
                qty = judge_model.apply(qty, sym, ts, cfg)
                try:
                    # §13 slot allocation counts only THIS strategy's positions;
                    # the global ceiling inside pure_checks sees all of them.
                    strategy_open = sum(1 for p in acct.positions.values()
                                        if p["owner"] == owner)
                    sim_peak = max(sim_peak, acct.equity(last_close))   # §31
                    risk.pure_checks("buy", sym, qty, fill,
                                     acct.account_dict(last_close),
                                     acct.positions_dict(last_close), cfg,
                                     regime_label=regime_labels.get(ts),
                                     strategy=owner,
                                     strategy_open=strategy_open,
                                     peak_equity=sim_peak)
                    _sim_open_trades = {
                        s: {"qty": p["qty"], "entry_price": p["avg_entry"],
                            "order": {"stop_price": p.get("stop")}}
                        for s, p in acct.positions.items()}
                    heat_pct = cfg["risk"].get("max_portfolio_heat_pct", 0)
                    if heat_pct:
                        eq = acct.equity(last_close)
                        heat = risk.portfolio_heat(_sim_open_trades, cfg, eq)
                        new_risk = (qty * max(fill - stop, 0.0) if stop
                                    else eq * cfg["risk"].get(
                                        "risk_per_trade_pct", 1.0) / 100)
                        if heat + new_risk > eq * heat_pct / 100:
                            n_heat_blocked += 1
                            by_strategy[owner]["blocked"] += 1
                            _blocked("heat")
                            continue
                    ccfg = cfg["risk"].get("correlation_cap") or {}
                    if ccfg.get("enabled") and acct.positions:
                        open_bars = {s: sym_bars[s][:idx[s][ts] + 1]
                                     for s in acct.positions
                                     if s != sym and ts in idx.get(s, {})}
                        n_corr = risk.correlated_position_count(
                            hist, open_bars, int(ccfg.get("lookback", 60)),
                            float(ccfg.get("threshold", 0.85)))
                        if n_corr >= int(ccfg.get("max_correlated", 2)):
                            n_corr_blocked += 1
                            by_strategy[owner]["blocked"] += 1
                            _blocked("correlation")
                            continue
                except risk.RiskRejection as e:
                    # §40: `e.rail` names which rail fired. Until 2026-07-29 this
                    # handler discarded it, collapsing regime, position cap,
                    # drawdown, cash and zero-quantity into one number. The
                    # reason existed at the moment of the block and was dropped.
                    by_strategy[owner]["blocked"] += 1
                    _blocked(getattr(e, "rail", "unattributed"))
                    continue
                census["executed"] += 1
                acct.cash -= qty * fill + fee
                acct.positions[sym] = {
                    "qty": qty, "avg_entry": fill, "entry_ts": ts,
                    "stop": stop, "tp": tp, "hw": fill, "owner": owner,
                    "trade": SimTrade(sym, ts, fill, qty, fees=fee)}
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
            elif sym in acct.positions:
                _exit(sym, _fill_open(sym, bar, ts), ts, "strategy_sell")
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
        pending = still_pending

        # 2. Bracket legs, intrabar, pessimistic: stop wins when both touch.
        for sym in list(acct.positions):
            if sym not in today:
                continue
            bar, pos = today[sym], acct.positions[sym]
            if pos["stop"] is not None and bar["low"] <= pos["stop"]:
                _exit(sym, pos["stop"], ts, "stop_loss")
                continue
            if pos["tp"] is not None and bar["high"] >= pos["tp"]:
                _exit(sym, pos["tp"], ts, "take_profit")
                continue
            if _trails(pos["owner"]):
                pos["hw"] = max(pos.get("hw", pos["avg_entry"]), bar["high"])
                hist = sym_bars[sym][:idx[sym][ts] + 1]
                t_stop = risk.trail_stop(
                    pos["hw"], strategy.atr(hist, bcfg.get("atr_period", 14)),
                    cfg, strategy=pos["owner"])
                if t_stop is not None and (pos["stop"] is None
                                           or t_stop > pos["stop"]):
                    pos["stop"] = t_stop

        # 3. Signals, per strategy, in live priority order.
        xs_ctx: dict = {}
        for name, sparams in active:
            smod = strategies.REGISTRY[name]
            if smod.NEEDS_CROSS_SECTION:
                hist_by_sym = {s: sym_bars[s][:idx[s][ts] + 1]
                               for s in sym_bars if ts in idx[s]}
                xs_ctx[name] = smod.prepare(hist_by_sym, sparams)

        # ONE pass over symbols, mirroring the live cycle's structure exactly
        # (main.py "Phase 3: per-symbol ensemble loop with position ownership"):
        # a held symbol is judged ONLY by its owner; a flat symbol is offered to
        # each enabled strategy in priority order and the first buy claims it.
        #
        # The ordering is load-bearing, not cosmetic. `fills_by_date` counts
        # exits as well as entries, so interleaving buys and sells in symbol
        # order — rather than draining all exits first — decides which orders
        # fit under the shared daily cap. An earlier draft split the two passes
        # and silently changed the results.
        buys_queued = {name: 0 for name, _ in active}
        for sym, bar in today.items():
            i = idx[sym][ts]
            hist = sym_bars[sym][:i + 1]

            if sym in acct.positions:
                pos = acct.positions[sym]
                owner = pos["owner"]
                if owner not in strategies.REGISTRY:
                    continue          # orphaned owner: bracket legs still apply
                sig = strategies.generate(owner, sym, hist, cfg, True,
                                          cross_section=xs_ctx.get(owner),
                                          entry_ts=pos["entry_ts"])
                if sig.action == "sell":
                    if min_days and _cal_days(pos["entry_ts"], ts) < min_days:
                        guard_skips += 1
                        continue
                    pending.append((sym, "sell", owner))
                continue

            for name, sparams in active:
                sig = strategies.generate(name, sym, hist, cfg, False,
                                          cross_section=xs_ctx.get(name),
                                          entry_ts=None)
                if sig.action != "buy":
                    continue
                census["signals"] += 1      # §40 denominator
                cd = cooldowns.get(name, 0)
                if cd and risk.cooldown_blocked(last_exit.get((name, sym)),
                                                ts, cd):
                    _blocked("cooldown")
                    continue
                blackout_days = sparams.get("earnings_blackout_days", 0)
                if blackout_days and earnings and earnings_mod.next_within(
                        earnings.get(sym, []), ts, blackout_days):
                    _blocked("earnings_blackout")
                    continue
                # §23 relative-volume confirmation (entries only; fails open).
                # `continue` not `break`: this symbol failed volume confirmation
                # for THIS strategy, but a lower-priority strategy may still
                # claim it — only an accepted buy consumes the symbol.
                if risk.rvol_blocked(hist, cfg, name):
                    _blocked("rvol")
                    continue
                # §31 cross-asset credit gate (entries only; fails open).
                # `break`, not `continue`: unlike rvol this is a MARKET-WIDE
                # condition with no per-strategy component, so no other
                # strategy could claim this symbol today either. Continuing
                # would re-evaluate an identical boolean for every remaining
                # strategy and reach the same answer.
                if risk.credit_blocked(credit, ts, cfg):
                    _blocked("credit")
                    break
                # Unprotectable entry (2026-07-27). `continue`, not `break`:
                # this is a PER-SYMBOL property, so another strategy scanning a
                # different symbol is unaffected — unlike the market-wide credit
                # gate above.
                if risk.unprotectable_entry(
                        hist[-1]["close"], strategies.atr(hist, 14), cfg):
                    _blocked("unprotectable_entry")
                    continue
                entry_cap = sparams.get("max_entries_per_cycle", 0)
                if entry_cap and buys_queued[name] >= entry_cap:
                    _blocked("entry_cap")
                    continue
                pending.append((sym, "buy", name))
                buys_queued[name] += 1
                break                 # first strategy to claim the symbol wins

        eq = acct.equity(last_close)
        curve.append(round(eq, 2))
        deployment.append((eq - acct.cash) / eq if eq > 0 else 0.0)

    final_ts = all_ts[-1] if all_ts else ""
    for sym in list(acct.positions):
        _exit(sym, last_close[sym], final_ts, "end_of_data")

    # §40: the census must ACCOUNT for every signal. A missing one means a drop
    # path exists that nobody is counting — which is precisely the state this
    # census was built to end, so it fails loudly rather than balancing quietly.
    # A buy queued on the last bar, or one whose symbol stopped printing bars,
    # never reaches the fill path. It is a real signal that produced no trade
    # and no block, and it is the drop path the balance check caught on its
    # very first run — which is the whole argument for asserting the sum rather
    # than trusting the buckets.
    for _sym, _action, _owner in pending:
        if _action == "buy":
            _blocked("expired_unfilled")

    _seen = census["executed"] + sum(census["blocked"].values())
    if _seen != census["signals"]:
        # Never balance quietly. An unexplained gap means a drop path exists
        # that nobody counts, which is the exact state this census ends.
        census["blocked"]["unaccounted"] = census["signals"] - _seen
        print(f"  !! block census does not balance: {census['signals']} "
              f"signals, {_seen} accounted for", file=sys.stderr)

    _dep = sorted(deployment)
    _n = len(_dep)

    total_ret = (acct.cash - start_cash) / start_cash * 100
    wins = [t for t in closed if t.pnl > 0]
    bh = (sum(buy_and_hold_return(b, slip, fee, start_cash / len(sym_bars))
              for b in sym_bars.values()) / len(sym_bars)) if sym_bars else 0.0

    return Result(
        params={"strategy": "ENSEMBLE",
                "members": [n for n, _ in active],
                "by_strategy": {k: {"trades": v["trades"],
                                    "pnl": round(v["pnl"], 2),
                                    "blocked": v["blocked"]}
                                for k, v in by_strategy.items()}},
        start=all_ts[0] if all_ts else "", end=final_ts,
        n_trades=len(closed), n_guard_skipped_exits=guard_skips,
        n_heat_blocked=n_heat_blocked, n_corr_blocked=n_corr_blocked,
        census=census,
        deployment_zero_pct=round(
            100 * sum(1 for d in _dep if d < 1e-9) / _n, 2) if _n else 0.0,
        deployment_median_pct=round(100 * _dep[_n // 2], 2) if _n else 0.0,
        deployment_max_pct=round(100 * _dep[-1], 2) if _n else 0.0,
        n_fill_fallback=n_fill_fallback,
        total_return_pct=round(total_ret, 3),
        buy_hold_return_pct=round(bh, 3),
        buy_hold_max_drawdown_pct=round(buy_and_hold_drawdown(sym_bars), 3),
        avg_deployment_pct=round(100 * sum(deployment) / len(deployment), 2)
        if deployment else 0.0,
        win_rate=round(len(wins) / len(closed), 3) if closed else 0.0,
        profit_factor=round(profit_factor(closed), 3),
        max_drawdown_pct=round(max_drawdown(curve), 3),
        equity_curve=curve, trades=closed)


# -------------------------------------------------------------- walk-forward

def load_config(path: str = "config.yaml") -> dict:
    """The live config, for callers that drive simulate() directly (e.g.
    scripts/gate_compare.py). simulate() deep-copies before touching it."""
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def append_trial(path: str, record: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    record["logged_at"] = datetime.now(timezone.utc).isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def walk_forward(sym_bars: dict, cfg: dict, grid: list, split: float,
                 trials_path: str, start_cash: float = 100_000.0,
                 strategy_name: str = "ma_crossover",
                 earnings: dict | None = None) -> dict:
    """In-sample grid search, out-of-sample validation of the single winner.
    EVERY variant tried is appended to the trials log — the honest audit trail
    against the False Strategy Theorem (the more variants you try, the better
    the best one looks by luck alone)."""
    is_bars = {s: b[:int(len(b) * split)] for s, b in sym_bars.items()}
    oos_bars = {s: b[int(len(b) * split):] for s, b in sym_bars.items()}

    results = []
    for params in grid:
        r = simulate(is_bars, cfg, params, start_cash, strategy_name,
                     earnings=earnings)
        results.append(r)
        append_trial(trials_path, {"phase": "in_sample", **r.summary()})

    best = max(results, key=lambda r: (r.total_return_pct, r.profit_factor))
    best_params = {k: v for k, v in best.params.items() if k != "strategy"}
    oos = simulate(oos_bars, cfg, best_params, start_cash, strategy_name,
                   earnings=earnings)
    append_trial(trials_path, {"phase": "oos", **oos.summary()})

    return {"n_variants": len(grid), "best_params": best.params,
            "is_result": best.summary(), "oos_result": oos.summary()}


def enablement_gate(oos: dict) -> tuple[bool, list[str]]:
    """May a strategy be enabled live? The OOS result must clear every bar.
    Params must be the in-sample winner, never re-picked after seeing OOS
    (the trials log makes violations visible)."""
    reasons = []
    if oos["total_return_pct"] <= 0:
        reasons.append(f"OOS return {oos['total_return_pct']:+.2f}% not positive")
    if oos["n_trades"] < 15:
        reasons.append(f"only {oos['n_trades']} OOS trades (need >=15)")
    if oos["profit_factor"] < 1.3:
        reasons.append(f"OOS profit factor {oos['profit_factor']} < 1.3")
    bh = oos["buy_hold_return_pct"]
    beats_bh = oos["total_return_pct"] >= bh
    # risk-adjusted escape hatch: most of the return at half the drawdown
    risk_adjusted = (oos["total_return_pct"] >= 0.7 * bh
                     and oos["max_drawdown_pct"]
                     <= 0.5 * oos["buy_hold_max_drawdown_pct"])
    # exposure-matched benchmark: the risk rails cap deployment at a few
    # percent of equity, so total-equity return can't fairly race a 100%%-
    # deployed B&H. Compare against B&H scaled to the SAME average exposure.
    deploy_frac = oos.get("avg_deployment_pct", 100.0) / 100.0
    exposure_bench = bh * deploy_frac
    exposure_matched = (deploy_frac < 0.5
                        and oos["total_return_pct"] >= exposure_bench)
    if not (beats_bh or risk_adjusted or exposure_matched):
        reasons.append(f"OOS return {oos['total_return_pct']:+.2f}% beats neither "
                       f"B&H {bh:+.2f}%, the risk-adjusted bar "
                       f"(dd {oos['max_drawdown_pct']:.1f}% vs B&H dd "
                       f"{oos['buy_hold_max_drawdown_pct']:.1f}%), nor the "
                       f"exposure-matched benchmark {exposure_bench:+.2f}% "
                       f"(at {oos.get('avg_deployment_pct', 100):.1f}% avg deployment)")
    return (not reasons), reasons


def build_grid(fasts, slows, stop_mults, tp_mults) -> list:
    grid = [{"fast_period": f, "slow_period": s,
             "stop_atr_mult": sm, "take_profit_atr_mult": tm}
            for f, s, sm, tm in itertools.product(fasts, slows, stop_mults, tp_mults)
            if f < s]
    if not grid:
        raise SystemExit("empty parameter grid (need fast < slow)")
    return grid


def _coerce(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def build_generic_grid(param_args: list, stop_mults, tp_mults) -> list:
    """Cross product of --param NAME v1 v2 ... lists plus bracket multipliers."""
    axes = {}
    for entry in param_args:
        name, *values = entry
        if not values:
            raise SystemExit(f"--param {name} needs at least one value")
        axes[name] = [_coerce(v) for v in values]
    axes["stop_atr_mult"] = list(stop_mults)
    axes["take_profit_atr_mult"] = list(tp_mults)
    names = list(axes)
    return [dict(zip(names, combo))
            for combo in itertools.product(*axes.values())]


# ----------------------------------------------------------------------- CLI

def main():
    cfg = load_config()
    bt = cfg.get("backtest", {})

    p = argparse.ArgumentParser(description="Offline backtest (never touches the live ledger)")
    p.add_argument("--strategy", default="ma_crossover",
                   choices=sorted(strategies.REGISTRY),
                   help="which strategy to walk-forward")
    p.add_argument("--param", nargs="+", action="append", default=[],
                   metavar=("NAME", "VALUES"),
                   help="grid values for a strategy param, e.g. "
                        "--param momentum_bars 63 126 252 (repeatable)")
    p.add_argument("--symbols", nargs="+", default=cfg["symbols"])
    p.add_argument("--bars-file", help="JSON/CSV bars; skips all network access")
    p.add_argument("--earnings-file",
                   help="JSON {symbol: [iso dates]} — frozen earnings "
                        "calendar for earnings_blackout_days variants "
                        "(omit to fetch via yfinance, cached)")
    p.add_argument("--start"), p.add_argument("--end")
    p.add_argument("--fast", nargs="+", type=int,
                   default=[cfg["strategy"]["fast_period"]])
    p.add_argument("--slow", nargs="+", type=int,
                   default=[cfg["strategy"]["slow_period"]])
    p.add_argument("--stop-mult", nargs="+", type=float,
                   default=[cfg["risk"].get("brackets", {}).get("stop_atr_mult", 0)])
    p.add_argument("--tp-mult", nargs="+", type=float,
                   default=[cfg["risk"].get("brackets", {}).get("take_profit_atr_mult", 0)])
    p.add_argument("--split", type=float, default=bt.get("walk_forward_split", 0.7))
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--slippage-bps", type=float, default=None,
                   help="cost model bps; default = median measured fill "
                        "slippage from the ledger when >=10 clean fills "
                        "exist, else config backtest.slippage_bps")
    p.add_argument("--vol-target", action="store_true",
                   help="CANDIDATE: enable risk.vol_target sizing for this run")
    p.add_argument("--regime-cap", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: enable down-regime gross exposure cap at PCT")
    p.add_argument("--trail-mult", type=float, default=None, metavar="MULT",
                   help="CANDIDATE §7: chandelier trailing stop at MULT x ATR")
    p.add_argument("--risk-sizing", type=float, default=None, metavar="PCT",
                   help="CANDIDATE §8: stop-distance sizing at PCT equity "
                        "risk per trade (replaces vol_target for the run)")
    p.add_argument("--cooldown-days", type=int, default=None, metavar="N",
                   help="CANDIDATE §9: same-ticker re-entry cooldown, N days")
    # Deployment / capacity levers (§12-§13). These live in cfg["risk"] and had
    # no flags, so the ONLY way to test them was editing the live config.yaml —
    # exactly the setup that produced the 07-16 drift accident. Flags keep
    # exploration off the live config.
    p.add_argument("--risk-per-trade", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: risk.risk_per_trade_pct — the NOTIONAL "
                        "sizing lever (binds tsmom/ma_crossover)")
    p.add_argument("--max-order-value", type=float, default=None, metavar="USD",
                   help="CANDIDATE: risk.max_order_value_usd — the per-order "
                        "cap (binds meanrev's risk-sizing path)")
    p.add_argument("--max-position-pct", type=float, default=None, metavar="PCT",
                   help="CANDIDATE: risk.max_position_pct concentration cap")
    p.add_argument("--max-positions", type=int, default=None, metavar="N",
                   help="CANDIDATE: risk.max_open_positions — slot count "
                        "(the live evidence-velocity bottleneck)")
    p.add_argument("--max-trades-day", type=int, default=None, metavar="N",
                   help="CANDIDATE: risk.max_trades_per_day rate limit")
    p.add_argument("--judge-model", action="store_true",
                   help="CANDIDATE §29: simulate the LLM judge's downsizing. "
                        "OFF by default so pre-2026-07-26 gates reproduce; "
                        "every number in backtest_candidates.md before that "
                        "date was produced WITHOUT it (divergence #8)")
    p.add_argument("--judge-salt", default=None, metavar="S",
                   help="redraw the simulated judge's verdicts. For testing "
                        "that a result survives a different draw — NOT for "
                        "shopping until one looks good")
    p.add_argument("--strategy-max-positions", type=int, default=None,
                   metavar="N",
                   help="CANDIDATE §13: per-strategy slot allocation for the "
                        "strategy under test (global cap still applies)")
    p.add_argument("--fee", type=float, default=bt.get("fee_per_trade_usd", 0.0))
    p.add_argument("--trials-path",
                   default=bt.get("trials_path", "memory/backtest_trials.jsonl"))
    args = p.parse_args()

    cfg.setdefault("backtest", {})
    if args.slippage_bps is not None:
        slip_resolved, slip_src = args.slippage_bps, "explicit flag"
    else:
        measured = None
        try:
            from ledger import Ledger
            measured = measured_slippage_bps(
                Ledger(cfg["memory"]["ledger_path"]).all_records())
        except Exception:  # noqa: BLE001 — calibration is best-effort
            pass
        slip_resolved = (measured if measured is not None
                         else bt.get("slippage_bps", 5))
        slip_src = ("measured from ledger fills" if measured is not None
                    else "config default (need >=10 clean measured fills)")
    print(f"(cost model: slippage {slip_resolved} bps — {slip_src})")
    cfg["backtest"]["slippage_bps"] = slip_resolved
    cfg["backtest"]["fee_per_trade_usd"] = args.fee
    if args.vol_target:
        cfg["risk"].setdefault("vol_target", {}).update(
            enabled=True, strategies=None)  # flag = test THIS strategy
        print(f"(CANDIDATE on: vol_target {cfg['risk']['vol_target']})")
    if args.regime_cap is not None:
        cfg["risk"]["regime_exposure"] = {"enabled": True,
                                          "down_max_gross_pct": args.regime_cap}
        print(f"(CANDIDATE on: regime_exposure cap {args.regime_cap}%)")
    if args.trail_mult is not None:
        cfg["risk"].setdefault("brackets", {})["trailing_atr_mult"] = args.trail_mult
        print(f"(CANDIDATE on: chandelier trail {args.trail_mult}x ATR)")
    if args.risk_sizing is not None:
        # Pre-registered (§8): risk-based sizing REPLACES vol_target for the
        # run — both normalize by realized vol; stacking them double-counts.
        cfg["risk"]["risk_sizing"] = {"enabled": True,
                                      "risk_pct": args.risk_sizing,
                                      "strategies": None}
        cfg["risk"]["vol_target"] = {"enabled": False}
        print(f"(CANDIDATE on: risk-based sizing {args.risk_sizing}%/trade, "
              "vol_target off for this run)")
    if args.cooldown_days is not None:
        cfg["risk"]["reentry_cooldown"] = {"days": args.cooldown_days,
                                           "strategies": None}
        print(f"(CANDIDATE on: re-entry cooldown {args.cooldown_days}d)")
    for flag, key, label in (
            (args.risk_per_trade, "risk_per_trade_pct", "notional risk/trade %"),
            (args.max_order_value, "max_order_value_usd", "per-order cap $"),
            (args.max_position_pct, "max_position_pct", "concentration cap %"),
            (args.max_positions, "max_open_positions", "position slots"),
            (args.max_trades_day, "max_trades_per_day", "trades/day")):
        if flag is not None:
            cfg["risk"][key] = flag
            print(f"(CANDIDATE on: {label} = {flag})")
    if args.judge_model:
        cfg.setdefault("backtest", {}).setdefault("judge_model", {})[
            "enabled"] = True
        cal = judge_model.load_calibration()
        print(f"(CANDIDATE §29 on: simulated judge — cuts "
              f"{cal['downsize_rate']:.1%} of buys, mean scale "
              f"{cal['mean_scale']:.3f}, n={cal['n_judged_buys']} live)")
    if args.judge_salt:
        cfg.setdefault("backtest", {}).setdefault("judge_model", {})[
            "salt"] = args.judge_salt
        print(f"(judge draw re-salted: {args.judge_salt})")
    if args.strategy_max_positions is not None:
        cfg.setdefault("strategies", {}).setdefault(args.strategy, {})[
            "max_open_positions"] = args.strategy_max_positions
        # The per-strategy allocation can only TIGHTEN; lift the global ceiling
        # so it is the allocation under test rather than the old global 5.
        cfg["risk"]["max_open_positions"] = max(
            cfg["risk"]["max_open_positions"], args.strategy_max_positions)
        print(f"(CANDIDATE §13 on: {args.strategy} slot allocation = "
              f"{args.strategy_max_positions})")

    if args.bars_file:
        sym_bars = load_bars_file(args.bars_file)
    elif args.start and args.end:
        sym_bars = fetch_bars(args.symbols, args.start, args.end)
    else:
        p.error("need --bars-file, or --start and --end")

    if args.strategy == "ma_crossover" and not args.param:
        grid = build_grid(args.fast, args.slow, args.stop_mult, args.tp_mult)
    else:
        grid = build_generic_grid(args.param, args.stop_mult, args.tp_mult)

    # Earnings calendar: only needed when the grid varies the blackout.
    earnings_data = None
    if any(p.get("earnings_blackout_days") for p in grid):
        if args.earnings_file:
            with open(args.earnings_file) as f:
                earnings_data = json.load(f)
        else:
            import earnings as earnings_mod_cli
            earnings_data = {s: earnings_mod_cli.get_dates(s)
                             for s in sym_bars}
            frozen = "memory/earnings_snapshot.json"
            with open(frozen, "w") as f:
                json.dump(earnings_data, f)
            print(f"(earnings calendar fetched + frozen to {frozen} — pass "
                  f"--earnings-file {frozen} to reproduce)\n")

    print(f"Walk-forward [{args.strategy}]: {len(grid)} variant(s), split "
          f"{args.split:.0%} IS / {1 - args.split:.0%} OOS, "
          f"{len(sym_bars)} symbol(s)\n")
    out = walk_forward(sym_bars, cfg, grid, args.split, args.trials_path,
                       args.cash, strategy_name=args.strategy,
                       earnings=earnings_data)

    is_r, oos_r = out["is_result"], out["oos_result"]
    print(f"Best in-sample params: {out['best_params']}")
    print(f"  IS : return {is_r['total_return_pct']:+.2f}%  vs B&H "
          f"{is_r['buy_hold_return_pct']:+.2f}%  | trades {is_r['n_trades']}  "
          f"win {is_r['win_rate']:.0%}  PF {is_r['profit_factor']}  "
          f"maxDD {is_r['max_drawdown_pct']:.1f}%")
    print(f"  OOS: return {oos_r['total_return_pct']:+.2f}%  vs B&H "
          f"{oos_r['buy_hold_return_pct']:+.2f}%  | trades {oos_r['n_trades']}  "
          f"win {oos_r['win_rate']:.0%}  PF {oos_r['profit_factor']}  "
          f"maxDD {oos_r['max_drawdown_pct']:.1f}%")
    print(f"\nThe OOS line is the honest one. All {out['n_variants']} variant(s) "
          f"logged to {args.trials_path}.")
    ok, reasons = enablement_gate(oos_r)
    if ok:
        print(f"ENABLEMENT GATE: PASS — {args.strategy} may be enabled in "
              "config.yaml with the best-IS params above.")
    else:
        print(f"ENABLEMENT GATE: FAIL — {args.strategy} stays disabled:")
        for r in reasons:
            print(f"  - {r}")


if __name__ == "__main__":
    main()
