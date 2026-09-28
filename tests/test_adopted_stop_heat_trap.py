"""The §41 heat trap, armed for real by an adopted position (2026-09-08→28).

The chain, from the live ledger and broker log:
  1. 2026-09-07 was Labor Day. The 09:35 cycle sent a META bracket market
     order (stop leg at $579.37); with the market shut, it queued.
  2. The 15:45 cycle's reconcile saw no META position and an order with
     nothing filled, and wrote it off as `entry_unfilled` — while it was
     still live.
  3. It filled at the 09-08 open. adopt_untracked_positions brought it back
     with `order={"id": None, "adopted": True}`: no stop, no order_class,
     although the $579.37 leg was still resting at the broker.
  4. portfolio_heat charged the stopless position equity x 8% ($7,971)
     against a 4% cap ($3,986). Every entry was refused for three weeks, and
     each refusal read as an ordinary risk rejection.

Each test pins one link. Offline: real Ledger on tmp_path, stub brokers.
"""
import pytest

import health
import main
import risk
from ledger import Ledger
from memory import Memory

META_LEG = {"id": "62385ce1", "symbol": "META", "qty": 6.0, "stop_price": 579.37}


class StopBroker:
    """Only what these paths read: resting stop legs, and order status."""

    def __init__(self, legs=(), orders=None, explode=False):
        self.legs = list(legs)
        self.orders = orders or {}
        self.explode = explode

    def open_stop_orders(self):
        if self.explode:
            raise RuntimeError("broker down")
        return list(self.legs)

    def get_order(self, order_id):
        return self.orders[order_id]

    def closed_orders(self, symbol, limit=20):
        return []

    def bars(self, *a, **k):
        return []

    def last_price(self, symbol):
        return None


@pytest.fixture
def env(tmp_path, cfg):
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    cfg["risk"]["risk_per_trade_pct"] = 8.0       # the shipped, inverted ratio
    cfg["risk"]["max_portfolio_heat_pct"] = 4.0
    return Ledger(cfg["memory"]["ledger_path"]), cfg


def _events(ledger, name):
    return [r for r in ledger.all_records()
            if r["type"] == "event" and r["event"] == name]


# ---- link 2: a queued order is not a dead one ------------------------------

def _pending_entry(ledger):
    return ledger.log_decision(
        "META", "buy", "crossover", {}, None, executed=True,
        order={"id": "entry-1", "symbol": "META", "order_class": "bracket",
               "stop_price": 579.37, "take_profit_price": None},
        entry_price=616.31, qty=6, strategy="ma_crossover")


@pytest.mark.parametrize("status", ["OrderStatus.ACCEPTED", "accepted",
                                    "OrderStatus.NEW", "pending_new", "held"])
def test_a_still_live_entry_order_is_kept_open(env, status):
    ledger, cfg = env
    tid = _pending_entry(ledger)
    broker = StopBroker(orders={"entry-1": {"id": "entry-1", "status": status,
                                            "filled_qty": 0, "legs": []}})
    main.reconcile_closed_positions(broker, ledger, Memory(cfg, ledger), cfg,
                                    positions={})
    assert tid in ledger.open_buys(), f"a {status} order was written off"
    assert ledger.closed_trades() == []
    assert len(_events(ledger, "entry_pending")) == 1


@pytest.mark.parametrize("status", ["OrderStatus.CANCELED", "expired",
                                    "OrderStatus.REJECTED", "done_for_day"])
def test_a_dead_entry_order_is_still_written_off(env, status):
    """The old behaviour survives for every order that really cannot fill."""
    ledger, cfg = env
    _pending_entry(ledger)
    broker = StopBroker(orders={"entry-1": {"id": "entry-1", "status": status,
                                            "filled_qty": 0, "legs": []}})
    main.reconcile_closed_positions(broker, ledger, Memory(cfg, ledger), cfg,
                                    positions={})
    assert ledger.open_buys() == {}
    assert ledger.closed_trades()[0]["exit_reason"] == "entry_unfilled"


def test_live_status_match_is_exact_not_substring():
    assert main._order_still_live("OrderStatus.NEW")
    assert not main._order_still_live("renewed")
    assert not main._order_still_live("OrderStatus.CANCELED")
    assert not main._order_still_live(None)


# ---- link 3: adoption reads the resting leg --------------------------------

def _adopt(ledger, cfg, broker):
    main.adopt_untracked_positions(
        broker, ledger, cfg,
        positions={"META": {"qty": 6, "avg_entry": 616.31,
                            "market_value": 4334.04}})
    return next(iter(ledger.open_buys().values()))


def test_adoption_records_the_resting_broker_stop(env):
    ledger, cfg = env
    rec = _adopt(ledger, cfg, StopBroker(legs=[META_LEG]))
    assert rec["order"]["stop_price"] == 579.37
    assert rec["order"]["order_class"] == "oto"
    assert rec["order"]["stop_leg_id"] == "62385ce1"
    assert rec["order"]["adopted"] is True and rec["order"]["id"] is None


def test_adoption_takes_the_tightest_leg(env):
    ledger, cfg = env
    legs = [dict(META_LEG, id="old", stop_price=560.0), META_LEG]
    assert _adopt(ledger, cfg, StopBroker(legs=legs))["order"]["stop_price"] == 579.37


@pytest.mark.parametrize("broker", [None, StopBroker(), StopBroker(explode=True)])
def test_adoption_without_a_leg_is_unchanged(env, broker):
    """No leg, no broker, or a failed lookup: the record is exactly what it
    was before — no invented stop, and adoption still happens."""
    ledger, cfg = env
    rec = _adopt(ledger, cfg, broker)
    assert rec["order"] == {"id": None, "symbol": "META", "adopted": True}


# ---- link 4: repair of a position adopted before this fix ------------------

def _stopless_meta(ledger):
    return ledger.log_decision(
        "META", "buy", "adopted: broker position with no ledger record", {},
        None, executed=True, order={"id": None, "symbol": "META", "adopted": True},
        entry_price=616.31, qty=6, strategy="ma_crossover")


def _aapl(ledger):
    return ledger.log_decision(
        "AAPL", "buy", "momentum", {}, None, executed=True,
        order={"id": "a-1", "order_class": "bracket", "stop_price": 286.73},
        entry_price=306.535, qty=12, strategy="tsmom")


def test_the_live_book_before_and_after(env):
    """The numbers from 2026-09-28: the cap read $8,209 of risk; the real
    figure is ~$460. A $142-risk entry was refused and now fits."""
    ledger, cfg = env
    _stopless_meta(ledger)
    _aapl(ledger)
    equity = 99_642.18
    cap = equity * cfg["risk"]["max_portfolio_heat_pct"] / 100

    before = risk.portfolio_heat(ledger.open_buys(), cfg, equity)
    assert before + 142 > cap, "the trap no longer reproduces"

    open_trades = ledger.open_buys()
    still = main.attach_broker_stops(StopBroker(legs=[META_LEG]), ledger,
                                     open_trades)
    after = risk.portfolio_heat(open_trades, cfg, equity)
    assert still == []
    assert after == pytest.approx(6 * (616.31 - 579.37) + 12 * (306.535 - 286.73))
    assert after + 142 <= cap


def test_repair_is_in_memory_only(env):
    ledger, cfg = env
    _stopless_meta(ledger)
    decisions = [r for r in ledger.all_records() if r["type"] == "decision"]
    main.attach_broker_stops(StopBroker(legs=[META_LEG]), ledger,
                             ledger.open_buys())
    assert [r for r in ledger.all_records() if r["type"] == "decision"] == decisions
    assert (next(iter(ledger.open_buys().values()))["order"]
            .get("stop_price")) is None


def test_repair_sets_order_class_so_an_exit_cancels_the_leg(env):
    """main.py's exit path cancels protective legs only when order_class is
    bracket/oto. Without it, selling META would race its own stop leg."""
    ledger, cfg = env
    _stopless_meta(ledger)
    open_trades = ledger.open_buys()
    main.attach_broker_stops(StopBroker(legs=[META_LEG]), ledger, open_trades)
    order = next(iter(open_trades.values()))["order"]
    assert order["order_class"] in ("bracket", "oto")
    assert order["stop_source"] == "broker"


def test_a_recorded_stop_is_never_overwritten(env):
    ledger, cfg = env
    _aapl(ledger)
    open_trades = ledger.open_buys()
    main.attach_broker_stops(
        StopBroker(legs=[{"id": "x", "symbol": "AAPL", "stop_price": 325.06}]),
        ledger, open_trades)
    assert next(iter(open_trades.values()))["order"]["stop_price"] == 286.73


def test_recovery_is_logged_once_per_trade_per_day(env):
    ledger, cfg = env
    _stopless_meta(ledger)
    for _ in range(3):
        main.attach_broker_stops(StopBroker(legs=[META_LEG]), ledger,
                                 ledger.open_buys())
    assert len(_events(ledger, "stop_recovered")) == 1


@pytest.mark.parametrize("broker", [StopBroker(), StopBroker(explode=True), None])
def test_no_leg_anywhere_is_reported_as_the_trap(env, broker):
    ledger, cfg = env
    _stopless_meta(ledger)
    assert main.attach_broker_stops(broker, ledger, ledger.open_buys()) == ["META"]


def test_the_cycle_repairs_before_anything_reads_a_stop():
    """Structural, because the repair is worthless unwired: it must run on the
    cycle's open_trades AFTER they are loaded and BEFORE the heat rail and the
    exit path read them. Deleting the call leaves every behavioural test above
    green, which is exactly how the trap stayed unseen."""
    import inspect
    src = inspect.getsource(main._run_cycle)
    load = src.index("open_trades = ledger.open_buys()")
    repair = src.index("attach_broker_stops(broker, ledger, open_trades)")
    first_read = src.index("pre_trade_checks(")
    assert load < repair < first_read
    assert "heat_trap_armed" in src[repair:first_read]


# ---- health says so --------------------------------------------------------

def _health(cfg, tmp_path):
    cfg.setdefault("ops", {})["require_offhost_mirror"] = False
    cfg["llm"]["enabled"] = False
    return health.status(cfg)


def test_health_fails_while_the_last_cycle_armed_it(env, tmp_path):
    ledger, cfg = env
    ledger.log_event("heat_trap_armed", "META: open with no recorded stop")
    ledger.log_event("cycle_complete", "{}")
    s = _health(cfg, tmp_path)
    assert s["heat_trap_armed"]
    assert any("heat trap armed" in p for p in s["problems"])


def test_health_clears_once_a_later_cycle_does_not(env, tmp_path):
    ledger, cfg = env
    ledger.log_event("heat_trap_armed", "META: open with no recorded stop")
    ledger.log_event("cycle_complete", "{}")
    ledger.log_event("cycle_complete", "{}")
    s = _health(cfg, tmp_path)
    assert s["heat_trap_armed"] is None
    assert not any("heat trap" in p for p in s["problems"])
