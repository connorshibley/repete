"""Phase 4 (2026-08-06): the cycle notices when its universe shrinks.

THE EVENT THIS EXISTS FOR. `memory/ledger.jsonl` contains exactly one
`data_error` across its entire history:

    {"event": "data_error",
     "detail": "QQQ: ('Connection aborted.', RemoteDisconnected(...))",
     "ts": "2026-08-05T19:47:25.555162+00:00"}

QQQ is in the 38-symbol core universe. That cycle then traded on 37 names with
nothing recorded as blocked and no floor anywhere. Divergence #17 — 500 symbols
in simulation against 38 live — re-enacted inside live.

WHAT THIS IS NOT. The total-outage case was already covered before this file
existed, by the stale-SPY abort. Measured 2026-07-31 09:37:50: the laptop lost
DNS, every symbol failed inside 40ms with NameResolutionError, `all_bars` came
back empty, SPY was missing, and the cycle aborted. `test_a_total_outage_still_
aborts_on_spy_rather_than_the_floor` pins that, so the floor cannot quietly
take credit for it.
"""
from datetime import datetime, timedelta, timezone

import pytest
import yaml

import main
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, cycle_env  # noqa: F401

CLOSES = [10] * 6 + [9, 9, 9, 20]


def fresh_bars(closes=CLOSES):
    """Bars ending today, so the freshness rail can be left ARMED.

    `conftest.make_bars` hard-codes January 2026 and the shared `cfg` fixture
    therefore sets `max_bar_age_days: 0` to disable the rail. Two tests here
    are specifically about how the floor interacts with that rail, so they
    cannot use a config where it is switched off.
    """
    today = datetime.now(timezone.utc).replace(
        hour=21, minute=0, second=0, microsecond=0)
    return [{"ts": (today - timedelta(days=len(closes) - 1 - i)).isoformat(),
             "open": c, "high": c, "low": c, "close": c, "volume": 1000}
            for i, c in enumerate(closes)]


class _Ledger:
    def __init__(self):
        self.events = []

    def log_event(self, event, detail):
        self.events.append((event, detail))

    def details(self, event):
        return [d for e, d in self.events if e == event]


class _PartialBroker:
    """Serves bars for everything except `broken`, which raises the exact
    exception 2026-08-05 produced."""

    def __init__(self, broken=(), bars=None):
        self.broken = set(broken)
        self._bars = bars if bars is not None else make_bars(CLOSES)

    def bars(self, symbol, timeframe, limit):
        if symbol in self.broken:
            raise ConnectionError(
                "('Connection aborted.', RemoteDisconnected('Remote end "
                "closed connection without response'))")
        return self._bars


def _fetch(cfg, symbols, broken=()):
    ledger = _Ledger()
    result = main._fetch_and_validate_bars(
        _PartialBroker(broken), cfg, ledger, list(symbols),
        completed_bars_only=False)
    return result, ledger


@pytest.fixture
def universe_cfg(cfg):
    cfg["symbols"] = [f"S{i}" for i in range(9)] + ["SPY"]
    return cfg


def test_a_complete_universe_says_nothing(universe_cfg):
    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"])
    assert ledger.details("universe_truncated") == []
    assert ledger.details("universe_floor_blocked") == []
    assert result[1] is None            # entries_blocked_reason
    assert result[2] == "datacheck"     # the rail label is unchanged


def test_one_lost_symbol_is_recorded_but_blocks_nothing(universe_cfg):
    """The QQQ case, 1-of-10. Visible, not blocking: a symbol is a symbol, and
    blocking every entry over one would be a rail that fires on noise."""
    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"], broken=["S3"])
    truncated = ledger.details("universe_truncated")
    assert len(truncated) == 1
    assert "S3" in truncated[0] and "9/10" in truncated[0]
    assert ledger.details("universe_floor_blocked") == []
    assert result[1] is None
    assert result[2] == "datacheck"


def test_losing_a_quarter_of_the_universe_blocks_entries(universe_cfg):
    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"],
                            broken=["S0", "S1", "S2"])
    blocked = ledger.details("universe_floor_blocked")
    assert len(blocked) == 1
    assert "70%" in blocked[0] and "80%" in blocked[0]
    assert result[1] == blocked[0]
    assert result[2] == "universe"


def test_the_floor_is_not_a_degradation_event(universe_cfg):
    """`degradation` is counted against ops.max_degradations_per_day, which is
    the budget for FAIL-OPEN events. This rail fails CLOSED; spending the
    fail-open allowance on it would let a data outage quietly consume the
    budget meant for something else."""
    _, ledger = _fetch(universe_cfg, universe_cfg["symbols"],
                       broken=["S0", "S1", "S2"])
    assert ledger.details("degradation") == []


def test_the_boundary_is_exactly_the_configured_fraction(universe_cfg):
    """8-of-10 is 80%, which is NOT below the 0.8 floor. 7-of-10 is."""
    universe_cfg["risk"]["min_universe_fraction"] = 0.8
    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"],
                            broken=["S0", "S1"])
    assert ledger.details("universe_floor_blocked") == []
    assert result[2] == "datacheck"

    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"],
                            broken=["S0", "S1", "S2"])
    assert len(ledger.details("universe_floor_blocked")) == 1
    assert result[2] == "universe"


def test_zero_disables_the_floor(universe_cfg):
    universe_cfg["risk"]["min_universe_fraction"] = 0
    result, ledger = _fetch(universe_cfg, universe_cfg["symbols"],
                            broken=["S0", "S1", "S2", "S4", "S5"])
    assert ledger.details("universe_floor_blocked") == []
    assert result[2] == "datacheck"
    # Tier 1 still reports, because visibility is not the thing being disabled.
    assert len(ledger.details("universe_truncated")) == 1


def test_the_denominator_is_what_was_requested_not_a_constant(universe_cfg):
    """`scan_symbols` legitimately varies with open positions and news
    nominations (main.py's _news_and_watchlist appends both). A hard-coded
    denominator would drift out of true and read as a floor while measuring
    something else."""
    requested = universe_cfg["symbols"] + ["NEWS1", "NEWS2", "NEWS3",
                                           "NEWS4", "NEWS5"]
    # 3 of 15 lost is 80% — above the floor, even though 3 of 10 was below it.
    result, ledger = _fetch(universe_cfg, requested,
                            broken=["S0", "S1", "S2"])
    assert ledger.details("universe_floor_blocked") == []
    assert "12/15" in ledger.details("universe_truncated")[0]


def test_a_symbol_returning_empty_bars_counts_as_lost(universe_cfg):
    """A fetch that raises and a fetch that returns nothing have the same
    effect on the cross-section, so they get the same treatment."""
    class _Empty(_PartialBroker):
        def bars(self, symbol, timeframe, limit):
            if symbol in self.broken:
                return []
            return self._bars

    ledger = _Ledger()
    main._fetch_and_validate_bars(_Empty(broken=["S0", "S1", "S2"]),
                                 universe_cfg, ledger,
                                 list(universe_cfg["symbols"]),
                                 completed_bars_only=False)
    assert len(ledger.details("universe_floor_blocked")) == 1
    assert ledger.details("data_error") == []   # nothing raised


def test_a_total_outage_still_aborts_on_spy_rather_than_the_floor(universe_cfg):
    """2026-07-31: DNS died and every symbol failed inside 40ms. The cycle must
    abort outright — the floor only blocks entries, and 'entries blocked, exits
    run' is the wrong answer when the feed is entirely gone.

    The freshness rail is ARMED here (the shared fixture disables it, because
    its bars are hard-coded to January). Without that this test would pass for
    the wrong reason: with the rail off, SPY-missing sails through and the
    FLOOR catches the outage, which is exactly the credit-taking this file's
    docstring says not to allow.
    """
    universe_cfg["risk"]["max_bar_age_days"] = 4
    ledger = _Ledger()
    result = main._fetch_and_validate_bars(
        _PartialBroker(broken=universe_cfg["symbols"], bars=fresh_bars()),
        universe_cfg, ledger, list(universe_cfg["symbols"]),
        completed_bars_only=False)
    assert result is None
    assert len(ledger.details("stale_data_abort")) == 1
    assert ledger.details("universe_floor_blocked") == []


def test_a_stale_symbol_counts_against_the_floor_too(universe_cfg):
    """Dropped-for-staleness and failed-to-fetch shrink the cross-section
    identically. The floor measures the universe actually traded, not the
    reason each name left it."""
    universe_cfg["risk"]["max_bar_age_days"] = 4
    stale = make_bars(CLOSES)            # hard-coded January 2026
    fresh = fresh_bars()

    class _Mixed:
        def bars(self, symbol, timeframe, limit):
            return stale if symbol in {"S0", "S1", "S2"} else fresh

    ledger = _Ledger()
    result = main._fetch_and_validate_bars(
        _Mixed(), universe_cfg, ledger, list(universe_cfg["symbols"]),
        completed_bars_only=False)
    assert len(ledger.details("data_stale")) == 3
    assert len(ledger.details("universe_floor_blocked")) == 1
    assert result[2] == "universe"


# --------------------------------------------------------------------------
# End to end. The floor's whole promise is "entries blocked, EXITS STILL RUN",
# and that is asserted at the broker rather than at the flag: a set
# `entries_blocked_reason` is not evidence that nothing reached market_order,
# and it is certainly not evidence that an exit did.
# --------------------------------------------------------------------------

BUY_CLOSES = [10] * 6 + [9, 9, 9, 20]      # crosses ABOVE: an entry signal
SELL_CLOSES = BUY_CLOSES + [20, 4, 4]      # ...then back below: an exit signal
OLD_ENTRY = "2026-01-05T21:00:00+00:00"    # past min_holding_days


class _TruncatingBroker(FakeCycleBroker):
    """Every symbol works except `broken`, which fails the way 2026-08-05 did."""

    def __init__(self, bars, broken=(), **kw):
        super().__init__(bars, **kw)
        self.broken = set(broken)

    def bars(self, symbol, timeframe, limit):
        if symbol in self.broken:
            raise ConnectionError("('Connection aborted.', RemoteDisconnected())")
        return self._bars


def _floor_env(cfg):
    """Nine filler names plus SPY, so losing three is 70% and trips the floor.

    The config is re-dumped because `cycle_env` writes config.yaml when the
    fixture is built and `run_cycle` reads it back off disk — a mutation of the
    dict alone reaches nothing.
    """
    cfg["symbols"] = [f"S{i}" for i in range(9)] + ["SPY"]
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    return ["S0", "S1", "S2"]


def _events(cfg):
    return [r.get("event") for r in
            Ledger(cfg["memory"]["ledger_path"]).all_records()
            if r.get("type") == "event"]


def test_a_tripped_floor_opens_nothing(cycle_env):
    cfg, install = cycle_env
    broken = _floor_env(cfg)
    broker = install(_TruncatingBroker(make_bars(BUY_CLOSES), broken=broken))

    main.run_cycle()

    assert broker.submitted == []
    assert "universe_floor_blocked" in _events(cfg)


def test_the_same_bars_DO_enter_with_a_whole_universe(cycle_env):
    """The contrast that makes the test above mean something: without the lost
    symbols these bars buy. Otherwise 'no order' could just be a signal that
    never fired."""
    cfg, install = cycle_env
    _floor_env(cfg)
    broker = install(_TruncatingBroker(make_bars(BUY_CLOSES)))

    main.run_cycle()

    assert len(broker.submitted) >= 1
    assert broker.submitted[0]["side"] == "buy"
    assert "universe_floor_blocked" not in _events(cfg)


def test_exits_still_execute_while_the_floor_blocks_entries(cycle_env):
    """The promise, asserted at the broker. If this ever goes red the floor is
    a kill switch that strands the book, which is worse than the truncation it
    was built to catch."""
    cfg, install = cycle_env
    broken = _floor_env(cfg)

    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("SPY", "buy", "seeded", {}, None, executed=True,
                     order={"id": "seed-1", "symbol": "SPY", "qty": 50},
                     entry_price=20.0, qty=50, entry_ts=OLD_ENTRY,
                     strategy="ma_crossover")
    held = {"SPY": {"qty": 50, "market_value": 1000.0,
                    "avg_entry": 20.0, "unrealized_pl": 0.0}}
    broker = install(_TruncatingBroker(make_bars(SELL_CLOSES), broken=broken,
                                       positions=held))

    main.run_cycle()

    assert "universe_floor_blocked" in _events(cfg)
    sides = [o["side"] for o in broker.submitted]
    assert "sell" in sides, f"the floor blocked an EXIT — submitted {sides}"
    assert "buy" not in sides
