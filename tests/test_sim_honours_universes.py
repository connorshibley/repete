"""DIVERGENCE #17 — the simulator must enter only what the live bot may enter.

`strategies.in_universe` has gated live entries since PR #90 introduced
per-strategy universes (`main.py:1685`). `simulate_ensemble` had no counterpart,
so every strategy could enter every symbol in the snapshot: on a 500-name wide
snapshot the incumbent traded 500 names in the simulator and 38 live.

It hid because the only two universe-scoped strategies — `xsmom` and `reclaim`
— are BOTH cross-sectional, and `prepare_one` scopes their ranking, so
`generate` answered "insufficient history for ranking" outside their universe
and the filter looked like it was working. That is a coincidence of those two
being cross-sectional, not a filter. A per-symbol strategy with a `universe:`
key would have traded the whole snapshot, silently.

Two properties, and the second is the one that is easy to break while fixing
the first:

  1. ENTRIES are filtered — the set the simulator may enter equals the set
     `in_universe` allows, for every enabled strategy.
  2. EXITS ARE NOT. `in_universe`'s docstring is explicit: exits route to the
     OWNING strategy regardless of universe, or a position whose symbol left a
     universe has no strategy willing to close it — a rail refusing to close
     risk, which is the inversion this codebase refuses everywhere.
"""
import pytest

import backtest as bt
import strategies

from test_ensemble_sim import synth_bars


def _cfg_with(universe_key=None):
    """The shipped config, with ma_crossover optionally scoped to a universe it
    cannot match. ma_crossover is chosen deliberately: it is NOT cross-sectional,
    so `prepare_one` cannot mask a missing filter for it — it is precisely the
    case the coincidence above was hiding."""
    cfg = bt.load_config()
    for name in ("tsmom", "meanrev", "xsmom", "donchian", "reclaim"):
        cfg["strategies"][name]["enabled"] = False
    cfg["strategies"]["ma_crossover"]["enabled"] = True
    if universe_key is not None:
        cfg["strategies"]["ma_crossover"]["universe"] = universe_key
    return cfg


@pytest.fixture
def bars():
    """Five symbols from the CORE universe, in config order (contention is
    resolved first-come by symbol, so order is part of the experiment)."""
    cfg = bt.load_config()
    picks = ["SPY", "AAPL", "MSFT", "XOM", "JNJ"]
    ordered = [s for s in cfg["symbols"] if s in picks]
    assert len(ordered) == len(picks)
    return synth_bars(ordered)


# ---------------------------------------------------------------------------
# 1. Entries are filtered.
# ---------------------------------------------------------------------------

def test_a_strategy_scoped_to_the_sector_map_cannot_enter_outside_it(bars):
    """`universe: sectors` scopes ma_crossover to the `sectors:` map. Four of
    the five fixture symbols ARE in that map; SPY is not, because the map
    deliberately contains no ETFs. So the assertion is a SUBSET, not "traded
    nothing" — an earlier draft asserted zero trades on the assumption that none
    of the five was in the map, and the test failed on the real config rather
    than on the code. The subset form is what the divergence is about anyway."""
    cfg = _cfg_with("sectors")
    allowed = strategies.sector_universe(cfg)
    assert "SPY" not in allowed and "AAPL" in allowed, "fixture premise moved"
    res = bt.simulate_ensemble(bars, cfg, 100_000.0)
    entered = {t.symbol for t in res.trades}
    assert "SPY" not in entered
    assert entered <= allowed, f"entered outside the sector map: {entered - allowed}"


def test_the_same_strategy_on_its_own_universe_still_trades(bars):
    """The twin, and the load-bearing half: without it, "traded nothing" is
    equally explained by a filter that refuses everything."""
    res = bt.simulate_ensemble(bars, _cfg_with(), 100_000.0)
    assert res.n_trades > 0


def test_an_unrecognised_universe_key_enters_nothing(bars):
    """`universe_for` returns the EMPTY set for a typo — chosen so a mistake
    makes a strategy trade NOTHING rather than the wrong universe. The
    simulator has to inherit that polarity, not just the happy path."""
    res = bt.simulate_ensemble(bars, _cfg_with("typo"), 100_000.0)
    assert res.n_trades == 0


def test_the_simulator_enters_only_what_in_universe_allows(bars):
    """The property the divergence is ABOUT, stated directly and against
    `in_universe` ITSELF — the same function main.py calls, not a copy of its
    logic, because a copy is how the two sides drifted apart in the first place.

    The fixture includes SPY, which `xsmom` may not enter (it is in `etfs:`),
    so the enterable set is a strict subset of the bars handed in — without
    that this could pass with no filtering at all."""
    cfg = bt.load_config()
    res = bt.simulate_ensemble(bars, cfg, 100_000.0)
    assert res.trades, "fixture traded nothing — the assertion would be vacuous"

    enterable = {s for s in bars
                 for name, _ in strategies.enabled(cfg)
                 if strategies.in_universe(cfg, name, s)}
    entered = {t.symbol for t in res.trades}
    assert entered <= enterable, f"entered outside the universe: {entered - enterable}"


def test_a_symbol_outside_every_enabled_universe_is_never_entered(bars):
    """The sharp version: hand the simulator a symbol that IS in the bars and is
    in NO enabled strategy's universe. It must never be traded, and the run must
    otherwise behave — the filter is a skip, not an abort."""
    cfg = bt.load_config()
    extra = dict(bars)
    extra["ZZZZ"] = next(iter(bars.values()))
    assert not any(strategies.in_universe(cfg, n, "ZZZZ")
                   for n, _ in strategies.enabled(cfg))
    res = bt.simulate_ensemble(extra, cfg, 100_000.0)
    assert "ZZZZ" not in {t.symbol for t in res.trades}
    assert res.n_trades > 0, "the rest of the book must still trade"


# ---------------------------------------------------------------------------
# 2. Exits are NOT filtered.
# ---------------------------------------------------------------------------

def test_a_position_held_when_its_universe_closes_still_exits_on_its_signal(
        bars, monkeypatch):
    """THE ENTRIES-ONLY PROOF, and the half that is easy to break while fixing
    the other one.

    `in_universe` starts answering False partway through the run, so no NEW
    entry is allowed after the cutoff — but positions already open must still
    receive their owner's exit signal. If the filter had been placed where the
    exit path could see it, those positions would be stranded and every one of
    them would close as `end_of_data` instead of `strategy_sell`.

    So the assertion is not "some exit happened" — end_of_data is an exit. It is
    that a STRATEGY-DRIVEN exit happened after entries were cut off."""
    real = strategies.in_universe
    calls = {"n": 0}

    def cutoff(cfg, name, symbol):
        calls["n"] += 1
        return real(cfg, name, symbol) if calls["n"] <= 200 else False

    monkeypatch.setattr(bt.strategies, "in_universe", cutoff)
    res = bt.simulate_ensemble(bars, _cfg_with(), 100_000.0)

    assert calls["n"] > 200, "the cutoff was never reached — test is vacuous"
    assert res.trades, "nothing was entered before the cutoff — test is vacuous"
    reasons = [t.exit_reason for t in res.trades]
    assert "strategy_sell" in reasons, (
        f"every position was stranded to its forced close: {set(reasons)} — "
        f"the universe filter is gating EXITS, not just entries")
