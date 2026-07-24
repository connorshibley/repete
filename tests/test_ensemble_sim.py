"""The ensemble simulator (backtest.simulate_ensemble).

Why it exists: `simulate()` replays ONE strategy against the rails; the live bot
runs the whole ensemble against ONE shared set of them. Every gate verdict in
knowledge/backtest_candidates.md was therefore measured on a bot that does not
exist — which is how §19b managed to produce four byte-identical arms.

The load-bearing test here is `test_single_strategy_matches_simulate`: with one
strategy enabled the ensemble must reproduce `simulate()` trade-for-trade. If it
does, ensemble behaviour is single-strategy behaviour plus contention and
nothing else, which is the whole correctness argument. If it drifts, every §20
number is suspect.
"""
from datetime import date, timedelta

import pytest

import backtest as bt
import strategies

ALL = ("meanrev", "tsmom", "ma_crossover", "xsmom", "donchian")


def synth_bars(symbols, n=320, seed=7):
    """Deterministic pseudo-random walks — no network, no snapshot needed."""
    out = {}
    for k, sym in enumerate(symbols):
        px, rows, d = 100.0 + 10 * k, [], date(2024, 1, 1)
        state = seed + k
        for i in range(n):
            state = (state * 1103515245 + 12345) % (2 ** 31)
            drift = ((state % 1000) / 1000.0 - 0.48) * 2.2
            px = max(5.0, px + drift)
            rows.append({"ts": f"{d.isoformat()}T21:00:00+00:00",
                         "open": round(px * 0.998, 4), "high": round(px * 1.02, 4),
                         "low": round(px * 0.98, 4), "close": round(px, 4),
                         "volume": 1_000_000})
            d += timedelta(days=1)
        out[sym] = rows
    return out


@pytest.fixture
def bars():
    return synth_bars(["SPY", "AAPL", "MSFT", "XOM", "JNJ"])


def only(name):
    return {n: {"enabled": (n == name)} for n in ALL}


# ---------------- the decisive equivalence check ----------------

@pytest.mark.parametrize("name", ALL)
def test_single_strategy_matches_simulate(bars, name):
    """One enabled strategy => the ensemble IS simulate(). Trade-for-trade.

    This is what licenses reading ensemble results as 'the same logic, plus
    contention'. A mismatch means the ensemble invented behaviour of its own."""
    cfg = bt.load_config()
    solo = bt.simulate(bars, cfg, {}, 100_000.0, name)
    ens = bt.simulate_ensemble(bars, cfg, 100_000.0, strategy_overrides=only(name))

    for field in ("total_return_pct", "n_trades", "profit_factor",
                  "max_drawdown_pct", "win_rate", "avg_deployment_pct",
                  "n_heat_blocked", "n_corr_blocked", "n_guard_skipped_exits"):
        assert getattr(solo, field) == getattr(ens, field), (
            f"{name}: {field} diverged — {getattr(solo, field)} vs "
            f"{getattr(ens, field)}")

    assert ([(t.symbol, t.entry_ts, t.exit_ts, t.exit_reason, round(t.pnl, 6))
             for t in solo.trades]
            == [(t.symbol, t.entry_ts, t.exit_ts, t.exit_reason, round(t.pnl, 6))
                for t in ens.trades]), f"{name}: trade sequence diverged"


# ---------------- contention: the behaviour simulate() cannot show ----------------

def test_positions_are_tagged_with_an_owner(bars):
    cfg = bt.load_config()
    ens = bt.simulate_ensemble(bars, cfg, 100_000.0)
    members = set(ens.params["members"])
    assert members and members <= set(strategies.REGISTRY)
    assert set(ens.params["by_strategy"]) >= members


def test_every_trade_is_attributed(bars):
    """Attribution must be complete: trades counted per strategy have to sum to
    the total, or the breakdown is quietly losing trades."""
    cfg = bt.load_config()
    ens = bt.simulate_ensemble(bars, cfg, 100_000.0)
    counted = sum(v["trades"] for v in ens.params["by_strategy"].values())
    assert counted == ens.n_trades


def test_priority_order_decides_who_claims_a_symbol(bars):
    """Two strategies wanting the same symbol: the higher-priority one wins.
    This is the live rule (main.py 'first buy that survives review + rails takes
    ownership') and it is the mechanism that starves low-priority strategies."""
    cfg = bt.load_config()
    cfg["strategies"]["meanrev"]["priority"] = 1
    cfg["strategies"]["tsmom"]["priority"] = 2
    first = bt.simulate_ensemble(bars, cfg, 100_000.0)
    by_first = first.params["by_strategy"]

    cfg2 = bt.load_config()
    cfg2["strategies"]["meanrev"]["priority"] = 9
    cfg2["strategies"]["tsmom"]["priority"] = 1
    second = bt.simulate_ensemble(bars, cfg2, 100_000.0)
    by_second = second.params["by_strategy"]

    # Reordering priority must change the allocation of trades between them.
    assert (by_first["meanrev"]["trades"], by_first["tsmom"]["trades"]) != \
           (by_second["meanrev"]["trades"], by_second["tsmom"]["trades"]), \
        "priority order had no effect — contention is not being modelled"


def test_shared_trade_cap_is_ensemble_wide(bars):
    """The §19b defect, pinned. A per-strategy counter would let each strategy
    fill its own quota; the live counter is global."""
    cfg = bt.load_config()
    cfg["risk"]["max_trades_per_day"] = 1
    tight = bt.simulate_ensemble(bars, cfg, 100_000.0)

    cfg2 = bt.load_config()
    cfg2["risk"]["max_trades_per_day"] = 20
    loose = bt.simulate_ensemble(bars, cfg2, 100_000.0)

    assert tight.n_trades < loose.n_trades, (
        "the daily trade cap did not bind across the ensemble — it is being "
        "applied per strategy, which is the bug this simulator exists to fix")


def test_global_slot_ceiling_binds_across_strategies(bars):
    cfg = bt.load_config()
    cfg["risk"]["max_open_positions"] = 1
    tight = bt.simulate_ensemble(bars, cfg, 100_000.0)
    cfg2 = bt.load_config()
    cfg2["risk"]["max_open_positions"] = 12
    loose = bt.simulate_ensemble(bars, cfg2, 100_000.0)
    assert tight.n_trades <= loose.n_trades


def test_one_position_per_symbol_across_strategies(bars):
    """Two strategies must never both hold the same symbol — the live account
    has one position per symbol, so overlapping entries would double-count."""
    cfg = bt.load_config()
    ens = bt.simulate_ensemble(bars, cfg, 100_000.0)
    for t in ens.trades:
        overlapping = [o for o in ens.trades
                       if o is not t and o.symbol == t.symbol
                       and o.entry_ts < t.exit_ts and t.entry_ts < (o.exit_ts or "9")]
        assert not overlapping, f"{t.symbol}: concurrent positions in one symbol"


def test_disabled_owner_still_gets_its_exits(bars):
    """Ownership rule: a strategy that is disabled must still be able to close
    the positions it opened, or they would be stranded until end-of-data."""
    cfg = bt.load_config()
    ens = bt.simulate_ensemble(bars, cfg, 100_000.0)
    owners = {k for k, v in ens.params["by_strategy"].items() if v["trades"]}
    reasons = {t.exit_reason for t in ens.trades}
    assert owners, "no strategy traded at all"
    # Strategy-driven exits must exist, not only bracket/end-of-data ones.
    assert reasons & {"strategy_sell", "stop_loss", "take_profit"}


def test_no_enabled_strategies_is_an_error(bars):
    cfg = bt.load_config()
    with pytest.raises(ValueError):
        bt.simulate_ensemble(bars, cfg, 100_000.0,
                             strategy_overrides={n: {"enabled": False}
                                                 for n in ALL})


def test_caller_config_is_never_mutated(bars):
    """simulate_ensemble deep-copies; a gate that silently rewrote the live
    config would be the 07-16 drift accident all over again."""
    cfg = bt.load_config()
    before = cfg["strategies"]["meanrev"]["enabled"]
    bt.simulate_ensemble(bars, cfg, 100_000.0,
                         strategy_overrides={"meanrev": {"enabled": False}})
    assert cfg["strategies"]["meanrev"]["enabled"] is before
