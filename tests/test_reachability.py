"""Reachability metric (§27 clause (e)) — Result.symbols_traded.

§27 is a CAPACITY claim: "the book reaches names it currently cannot." That
claim is decided by a single number, so the number has to be trustworthy.

The property is DERIVED from `trades` rather than stored, and these tests pin
that choice: a stored counter written at construction time is exactly the kind
of thing that drifted in §13 (rails missing from the sim), §20 (a limit that
measured nothing) and §24 (allocation decided by config order). A derived value
cannot disagree with the trade list, because it *is* the trade list.
"""
from dataclasses import dataclass

import backtest as bt


@dataclass
class FakeTrade:
    symbol: str
    pnl: float = 1.0


def result(symbols) -> bt.Result:
    return bt.Result(
        params={}, start="2024-01-01", end="2024-06-01",
        n_trades=len(symbols), n_guard_skipped_exits=0,
        total_return_pct=0.0, buy_hold_return_pct=0.0,
        buy_hold_max_drawdown_pct=0.0, avg_deployment_pct=0.0,
        win_rate=0.0, profit_factor=0.0, max_drawdown_pct=0.0,
        trades=[FakeTrade(s) for s in symbols])


# ---------------- the number itself ----------------

def test_counts_distinct_symbols_not_trades():
    """Three trades in one name is reach of ONE. Conflating the two would let a
    higher trade count masquerade as wider reach — the precise confusion §27
    exists to avoid."""
    r = result(["AAPL", "AAPL", "AAPL"])
    assert r.n_trades == 3
    assert r.n_symbols_traded == 1
    assert r.symbols_traded == {"AAPL"}


def test_counts_each_distinct_name_once():
    r = result(["SPY", "QQQ", "LLY", "SPY"])
    assert r.n_symbols_traded == 3
    assert r.symbols_traded == {"SPY", "QQQ", "LLY"}


def test_no_trades_is_zero_reach_not_an_error():
    r = result([])
    assert r.n_symbols_traded == 0
    assert r.symbols_traded == set()


# ---------------- it cannot drift ----------------

def test_reachability_follows_the_trade_list():
    """THE load-bearing test for the derived-not-stored choice. Mutate `trades`
    and the count must follow; a stored field would keep the stale value."""
    r = result(["SPY"])
    assert r.n_symbols_traded == 1
    r.trades.append(FakeTrade("LLY"))
    assert r.n_symbols_traded == 2, "reachability went stale against trades"


# ---------------- it survives serialisation ----------------

def test_summary_includes_reachability():
    """asdict() serialises fields only. If this regresses, every §27 gate report
    silently loses the clause-(e) number rather than failing loudly."""
    s = result(["SPY", "QQQ"]).summary()
    assert s["n_symbols_traded"] == 2


def test_summary_still_drops_the_heavy_payloads():
    s = result(["SPY"]).summary()
    assert "trades" not in s and "equity_curve" not in s


# ---------------- it is real against the simulator ----------------

def test_a_real_run_reports_reachability_within_its_universe():
    from datetime import date, timedelta

    def synth(symbols, n=320, seed=7):
        out = {}
        for k, sym in enumerate(symbols):
            px, rows, d, st = 100.0 + 7 * k, [], date(2024, 1, 1), seed + k
            for _ in range(n):
                st = (st * 1103515245 + 12345) % (2 ** 31)
                px = max(5.0, px + ((st % 1000) / 1000.0 - 0.48) * 2.2)
                rows.append({"ts": f"{d.isoformat()}T21:00:00+00:00",
                             "open": round(px * 0.998, 4), "high": round(px * 1.02, 4),
                             "low": round(px * 0.98, 4), "close": round(px, 4),
                             "volume": 1_000_000})
                d += timedelta(days=1)
            out[sym] = rows
        return out

    cfg = bt.load_config()
    picks = [s for s in cfg["symbols"] if s in ("SPY", "AAPL", "MSFT", "XOM")]
    r = bt.simulate_ensemble(synth(picks), cfg, 100_000.0)

    assert r.symbols_traded <= set(picks), "traded a symbol outside the universe"
    assert r.n_symbols_traded == len({t.symbol for t in r.trades})
    assert r.n_symbols_traded <= r.n_trades
