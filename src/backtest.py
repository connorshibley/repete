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
    equity_curve: list = field(default_factory=list)
    trades: list = field(default_factory=list)

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("equity_curve")
        d.pop("trades")
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
    CSV (columns: symbol?,ts,open,high,low,close,volume) -> broker.bars shape."""
    if path.endswith(".json"):
        with open(path) as f:
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
                "stop_atr_mult_high_vol")


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

    smod = strategies.REGISTRY[strategy_name]
    needs_xs = smod.NEEDS_CROSS_SECTION

    bt = cfg.get("backtest", {})
    slip = bt.get("slippage_bps", 5)
    fee = bt.get("fee_per_trade_usd", 0.0)
    min_days = cfg["risk"].get("min_holding_days", 0)

    # Per-date vol bucket from SPY (only needed for the vol-conditioned stop;
    # empty => bracket_prices falls back to the base multiplier).
    vol_series = (vol_bucket_series(sym_bars.get("SPY", []),
                                    cfg.get("learning", {}).get(
                                        "regime", {"sma_period": 50,
                                                   "vol_period": 20,
                                                   "vol_low": 0.15,
                                                   "vol_high": 0.25}))
                  if bcfg.get("stop_atr_mult_high_vol") else {})

    acct = SimAccount(cash=start_cash)
    closed: list = []
    curve: list = []
    deployment: list = []
    guard_skips = 0
    fills_by_date: dict = {}
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
        closed.append(t)

    for ts in all_ts:
        today = {sym: sym_bars[sym][idx[sym][ts]]
                 for sym in sym_bars if ts in idx[sym]}
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
                if fills_by_date.get(date, 0) >= cfg["risk"]["max_trades_per_day"]:
                    continue  # rate limit across symbols, mirroring the live bot
                fill = bar["open"] * (1 + slip / 1e4)
                qty = risk.size_order(acct.account_dict(last_close), fill, cfg)
                try:
                    risk.pure_checks("buy", sym, qty, fill,
                                     acct.account_dict(last_close),
                                     acct.positions_dict(last_close), cfg)
                except risk.RiskRejection:
                    continue
                i = idx[sym][ts]
                hist = sym_bars[sym][:i + 1]
                prices = risk.bracket_prices(
                    fill, strategy.atr(hist, bcfg.get("atr_period", 14)), cfg,
                    vol_bucket=vol_series.get(ts))
                stop, tp = prices if prices else (None, None)
                acct.cash -= qty * fill + fee
                acct.positions[sym] = {
                    "qty": qty, "avg_entry": fill, "entry_ts": ts,
                    "stop": stop, "tp": tp,
                    "trade": SimTrade(sym, ts, fill, qty, fees=fee)}
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
            elif sym in acct.positions:
                _exit(sym, bar["open"], ts, "strategy_sell")
                fills_by_date[date] = fills_by_date.get(date, 0) + 1
        pending = still_pending

        # 2. Bracket legs, intrabar, pessimistic: stop wins when both touch.
        for sym in list(acct.positions):
            if sym not in today:
                continue
            bar, pos = today[sym], acct.positions[sym]
            if pos["stop"] is not None and bar["low"] <= pos["stop"]:
                _exit(sym, pos["stop"], ts, "stop_loss")
            elif pos["tp"] is not None and bar["high"] >= pos["tp"]:
                _exit(sym, pos["tp"], ts, "take_profit")

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
                # earnings blackout — same rule as the live filter in main.py
                if blackout_days and earnings and earnings_mod.next_within(
                        earnings.get(sym, []), ts, blackout_days):
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
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
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
    p.add_argument("--slippage-bps", type=float, default=bt.get("slippage_bps", 5))
    p.add_argument("--fee", type=float, default=bt.get("fee_per_trade_usd", 0.0))
    p.add_argument("--trials-path",
                   default=bt.get("trials_path", "memory/backtest_trials.jsonl"))
    args = p.parse_args()

    cfg.setdefault("backtest", {})
    cfg["backtest"]["slippage_bps"] = args.slippage_bps
    cfg["backtest"]["fee_per_trade_usd"] = args.fee

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
