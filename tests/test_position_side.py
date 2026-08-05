"""The signal contract carries POSITION DIRECTION, not just "a position exists".

`generate(symbol, bars, params, holding: bool, ...)` says only THAT a position
is open. For a strategy with a short leg that is not enough to choose an exit:
the owner-only exit branch in main.py calls `generate(owner, ..., True, ...)`
for any held name, and a long-biased strategy asked about a SHORT with only
`holding=True` to go on answers `"sell"`. `"sell"` is in EXIT_ACTIONS, so every
rail lets it through, and `broker.market_order` maps it to `OrderSide.SELL` —
which DOUBLES the short instead of covering it. Same shape as the Phase 2-B
`market_order` fallthrough, one layer up, and reachable the moment a short
exists.

This file pins the three pieces that close it:

  1. `base.side_of_qty` — the ONE derivation of "which way does this point",
     from the signed quantity the broker already reports.
  2. `strategies.generate`'s dispatch — a module that DECLARES
     `NEEDS_POSITION_SIDE` receives the side; one that does not is called with
     exactly the arguments it was called with before, so no existing strategy's
     signature moved.
  3. main.py's wiring — the value that arrives at the owning strategy is the
     side of the position the BROKER reports, on both the long and short side.

Nothing shorts yet and `xsmom.generate` deliberately ignores the value it now
receives, so every assertion here is a rehearsal — which is why each one has a
long-side twin. Without the twin you cannot tell "the short case is right" from
"the long case broke and this number happened to match".
"""
import types

import yaml

import main
import strategies
from strategies.base import Signal, side_of_qty

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, BUY_CLOSES


# ---------------------------------------------------------------------------
# 1. side_of_qty — boundary pairs on the sign, and the zero case that is
#    neither side.
# ---------------------------------------------------------------------------

def test_a_positive_quantity_is_a_long():
    assert side_of_qty(10) == "long"


def test_a_negative_quantity_is_a_short():
    assert side_of_qty(-10) == "short"


def test_the_smallest_positive_and_negative_quantities_still_split():
    """The pair that matters at the boundary: a fractional share on either
    side of zero must not collapse into the same answer through an int cast."""
    assert side_of_qty(0.0001) == "long"
    assert side_of_qty(-0.0001) == "short"


def test_zero_is_neither_side():
    """A flat book is not a long one. `qty == 0` reaches here from a fully
    exited position whose record has not been dropped yet; answering "long"
    would tell a strategy it holds something it does not."""
    assert side_of_qty(0) is None
    assert side_of_qty(0.0) is None


def test_a_missing_quantity_is_neither_side():
    assert side_of_qty(None) is None


def test_a_string_quantity_is_read_by_value_not_by_truthiness():
    """Alpaca's own fields arrive as strings on some code paths. `"-5"` is
    truthy, so any implementation that tested the value rather than parsing it
    would call this a long — the pair proves the parse happens."""
    assert side_of_qty("-5") == "short"
    assert side_of_qty("5") == "long"


# ---------------------------------------------------------------------------
# 2. Dispatch — DECLARED, not inferred, and the two optional kwargs compose.
# ---------------------------------------------------------------------------

def _fake_module(name, **flags):
    """A duck-typed strategy module that records exactly which keyword
    arguments the dispatch handed it."""
    seen = {}

    def generate(symbol, bars, params, holding, **kwargs):
        seen.clear()
        # `cross_section` is passed unconditionally to every module and is not
        # what these tests are about; dropping it here keeps each assertion a
        # statement about the OPTIONAL kwargs only.
        kwargs.pop("cross_section", None)
        seen.update(kwargs)
        return Signal(symbol, "hold", "recorded", {}, name)

    mod = types.SimpleNamespace(NAME=name, NEEDS_CROSS_SECTION=False,
                                required_lookback=lambda p: 1,
                                generate=generate, seen=seen, **flags)
    return mod


def _dispatch(monkeypatch, mod, **kwargs):
    monkeypatch.setitem(strategies.REGISTRY, "probe", mod)
    cfg = {"strategies": {"probe": {"enabled": True}}}
    strategies.generate("probe", "AAPL", [], cfg, True, **kwargs)
    return mod.seen


def test_a_module_that_declares_needs_position_side_receives_it(monkeypatch):
    mod = _fake_module("probe", NEEDS_POSITION_SIDE=True)
    seen = _dispatch(monkeypatch, mod, position_side="short")
    assert seen == {"position_side": "short"}


def test_a_module_that_does_not_declare_it_is_called_without_it(monkeypatch):
    """The load-bearing half. Every strategy in REGISTRY today except xsmom
    declares nothing, so this is the proof that adding the flag cannot move
    ma_crossover, tsmom, meanrev, donchian, lowvol or reclaim: they are called
    with the identical argument list, not with an extra kwarg they ignore."""
    mod = _fake_module("probe")
    seen = _dispatch(monkeypatch, mod, position_side="short")
    assert seen == {}


def test_the_two_optional_kwargs_compose_rather_than_exclude(monkeypatch):
    """Four combinations exist once there are two independent flags. The
    dispatch used to be an if/else over one of them; an if/elif over both is
    the shape that silently drops the second."""
    mod = _fake_module("probe", NEEDS_ENTRY_TS=True, NEEDS_POSITION_SIDE=True)
    seen = _dispatch(monkeypatch, mod, entry_ts="2026-08-05", position_side="short")
    assert seen == {"entry_ts": "2026-08-05", "position_side": "short"}


def test_entry_ts_alone_still_arrives_alone(monkeypatch):
    """The twin of the compose test: meanrev and reclaim declare only
    NEEDS_ENTRY_TS, and must keep being called with only that."""
    mod = _fake_module("probe", NEEDS_ENTRY_TS=True)
    seen = _dispatch(monkeypatch, mod, entry_ts="2026-08-05", position_side="short")
    assert seen == {"entry_ts": "2026-08-05"}


def test_xsmom_is_the_strategy_that_declares_it():
    """Pins the declaration to the module that will carry the short leg. If a
    future rename or rewrite drops the flag, xsmom starts receiving None for
    every held position and its cover branch goes dead — silently, because a
    missing kwarg with a default is not an error."""
    assert getattr(strategies.REGISTRY["xsmom"], "NEEDS_POSITION_SIDE", False)


def test_no_other_shipped_strategy_declares_it_yet():
    """The mirror. Any strategy that starts declaring this must also start
    reading it; a flag that is set but unread is the dead-guard shape that
    `short_bottom_fraction` already cost this repo one PR."""
    others = [n for n, m in strategies.REGISTRY.items()
              if n != "xsmom" and getattr(m, "NEEDS_POSITION_SIDE", False)]
    assert others == []


def test_xsmom_ignores_the_side_until_the_short_leg_lands():
    """xsmom accepts `position_side` and must produce the SAME signal for a
    held position whichever side is passed — this change is a no-op for every
    signal it emits, and this is what says so mechanically rather than in a
    commit message."""
    from strategies import xsmom
    ctx = {"ranks": {"AAPL": 9, "MSFT": 0, "NVDA": 1, "AMD": 2},
           "returns": {"AAPL": -0.2, "MSFT": 0.5, "NVDA": 0.4, "AMD": 0.3},
           "n": 10}
    params = {"rank_lookback_bars": 231, "buy_top_fraction": 0.25,
              "exit_below_fraction": 0.50}
    as_long = xsmom.generate("AAPL", [], params, True, ctx, position_side="long")
    as_short = xsmom.generate("AAPL", [], params, True, ctx, position_side="short")
    as_none = xsmom.generate("AAPL", [], params, True, ctx)
    assert as_long.action == as_short.action == as_none.action == "sell"
    assert as_long.reason == as_short.reason == as_none.reason


# ---------------------------------------------------------------------------
# 3. main.py's wiring — what actually reaches the owning strategy.
# ---------------------------------------------------------------------------

def _run_cycle_capturing_side(tmp_path, monkeypatch, cfg, qty):
    """Run one cycle holding a position of signed size `qty` and return the
    `position_side` the owner-only exit branch handed to the strategy."""
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    bars = make_bars(BUY_CLOSES)
    price = bars[-1]["close"]
    positions = {"AAPL": {"qty": qty, "market_value": qty * price,
                          "avg_entry": price, "unrealized_pl": 0.0}}
    broker = FakeCycleBroker(bars, positions=positions)
    monkeypatch.setattr(main, "Broker", lambda c: broker)

    captured = []
    real = strategies.generate

    def spy(name, symbol, bars_, cfg_, holding, **kwargs):
        if holding:
            captured.append(kwargs.get("position_side"))
        return real(name, symbol, bars_, cfg_, holding, **kwargs)

    monkeypatch.setattr(main.strategies, "generate", spy)
    main.run_cycle()
    return captured


def test_a_held_short_reaches_its_owner_as_a_short(tmp_path, monkeypatch, cfg):
    """The whole point. The broker reports a short's qty NEGATIVE (main.py's
    own in-cycle view was made to match that sign in Phase 2-C), and that sign
    is what has to arrive at the strategy deciding the exit."""
    sides = _run_cycle_capturing_side(tmp_path, monkeypatch, cfg, -5)
    assert sides and set(sides) == {"short"}


def test_a_held_long_reaches_its_owner_as_a_long(tmp_path, monkeypatch, cfg):
    """The twin, and the regression pin: nothing shorts yet, so this is the
    only case that runs in production today and it must be unchanged."""
    sides = _run_cycle_capturing_side(tmp_path, monkeypatch, cfg, 5)
    assert sides and set(sides) == {"long"}
