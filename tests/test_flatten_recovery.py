"""Automatic liquidation, and the interlocks that keep it from firing wrongly.

Why this file exists (2026-08-02)
---------------------------------
`_kill_switch_fired` engaged HALT, called `flatten_all()`, and if that raised it
logged `kill_switch_flatten_failed` and returned. The halt is `freeze`, so no
later cycle ran, so **nothing ever retried**: the book sat open after a 5% daily
loss with only its broker-side bracket legs managing it. PR #73 documented that
as the worst state in the system and left it manual. This automates it.

Automating a liquidation earns a different standard of proof than automating a
report. The dangerous failure is not "it did not run" — it is **"it ran when it
should not have"**, and every test below is aimed at that side:

  * it cannot run without a marker the kill switch itself wrote
  * clearing the HALT cancels it, because the marker lives INSIDE the HALT file
  * an unreadable file authorises nothing
  * it stops at a budget instead of hammering a broker forever
  * it holds no code path that could open a position

The one test pointed the other way — `a_partial_flatten_is_a_failure` — exists
because the retry is worthless on top of a success signal that can lie, and
until now it could: `flatten_all()` not raising was taken as a flat book.
"""
import ast
import os

import pytest

import flatten_recovery
import risk
from ledger import Ledger


# --------------------------------------------------------------- fake brokers

class StubBroker:
    """Positions that survive `flatten_all` unless `clears` says otherwise."""

    def __init__(self, positions=None, raises=False, clears=False):
        self._positions = dict(positions or {})
        self._raises = raises
        self._clears = clears
        self.flatten_calls = 0

    def flatten_all(self):
        self.flatten_calls += 1
        if self._raises:
            raise RuntimeError("broker timeout closing positions")
        if self._clears:
            self._positions = {}

    def positions(self):
        return self._positions


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated cwd — HALT_FILE and the ledger path are both relative."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    # Always inside the retry window, so window behaviour is tested explicitly
    # below rather than deciding the result of every other test by wall-clock.
    monkeypatch.setattr(flatten_recovery, "within_retry_window", lambda *a: True)
    return Ledger("memory/ledger.jsonl")


CFG = {"risk": {"kill_switch": {"auto_retry_flatten": True, "max_attempts": 3}}}


def _kill_switch_halt(detail="still open: SPY"):
    """The state the kill switch leaves behind when its flatten fails."""
    risk.engage_halt("daily loss limit breached", mode=risk.HALT_MODE_FREEZE)
    risk.mark_flatten_pending(detail)


def _events(ledger):
    return [r.get("event") for r in ledger.all_records()
            if r.get("type") == "event"]


# ------------------------------------------------- it must not fire by itself

def test_no_halt_means_no_liquidation(env):
    broker = StubBroker({"SPY": {"qty": 1}})
    assert flatten_recovery.run(broker, env, CFG) == "not_pending"
    assert broker.flatten_calls == 0


def test_a_MANUAL_freeze_never_triggers_a_liquidation(env):
    """THE interlock that matters most.

    `./scripts/halt.sh --freeze "broker sending bad fills"` is a halt pulled
    BECAUSE the broker is suspect. If that alone armed the recovery, halting
    over bad fills would hand the same broker a liquidation order — the exact
    inversion the freeze mode exists to prevent. Only the kill switch's own
    marker authorises this.
    """
    risk.engage_halt("MANUAL — broker sending bad fills",
                     mode=risk.HALT_MODE_FREEZE)
    broker = StubBroker({"SPY": {"qty": 1}})
    assert flatten_recovery.run(broker, env, CFG) == "not_pending"
    assert broker.flatten_calls == 0


def test_an_exits_mode_halt_never_triggers_a_liquidation(env):
    risk.engage_halt("MANUAL — market has gone mad", mode=risk.HALT_MODE_EXITS)
    broker = StubBroker({"SPY": {"qty": 1}})
    assert flatten_recovery.run(broker, env, CFG) == "not_pending"
    assert broker.flatten_calls == 0


def test_an_unreadable_halt_file_authorises_nothing(env, monkeypatch):
    """Polarity check, and it is the OPPOSITE of `halt_mode()` on purpose.

    There, an unknown file means "stop" — inaction is safe. Here the marker
    authorises a sale, so an unparseable file must mean "do not act". Selling
    the book because a file failed to read would be the worst possible reading.
    """
    _kill_switch_halt()

    def _boom(*a, **k):
        raise OSError("disk fell over")
    monkeypatch.setattr("builtins.open", _boom)
    assert risk.flatten_pending() is False


def test_the_marker_cannot_conjure_its_own_halt(env):
    """`mark_flatten_pending` must never CREATE the HALT file. A marker able to
    do that would be a way to arm an automatic liquidation from nothing."""
    assert risk.mark_flatten_pending("no halt engaged") is False
    assert not risk.check_halt()
    assert not risk.flatten_pending()


def test_clearing_the_halt_cancels_a_pending_flatten(env):
    """The surprise-liquidation guard, and the reason the marker lives in the
    HALT file rather than in memory/.

    The documented recovery is `rm HALT`. If pending state survived that, the
    next scheduled run would liquidate a book the operator had just decided to
    keep. Storing it here makes "clear the halt" and "cancel the flatten" the
    same physical act rather than two things a human must remember to pair.
    """
    _kill_switch_halt()
    assert risk.flatten_pending()

    os.remove(risk.HALT_FILE)               # what the runbook tells you to do

    assert not risk.flatten_pending()
    broker = StubBroker({"SPY": {"qty": 1}})
    assert flatten_recovery.run(broker, env, CFG) == "not_pending"
    assert broker.flatten_calls == 0


def test_the_config_switch_turns_it_off(env):
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}})
    off = {"risk": {"kill_switch": {"auto_retry_flatten": False}}}
    assert flatten_recovery.run(broker, env, off) == "disabled"
    assert broker.flatten_calls == 0


# ----------------------------------------------------- when it SHOULD fire

def test_a_successful_retry_clears_the_marker_and_LEAVES_the_halt(env):
    """Recovery finishes the liquidation; it does not decide the incident is
    over. The daily-loss breach that caused it is still a human's to clear."""
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}}, clears=True)

    assert flatten_recovery.run(broker, env, CFG) == "recovered"

    assert broker.flatten_calls == 1
    assert not risk.flatten_pending()
    assert risk.check_halt(), "recovery must NOT clear the halt"
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE
    assert "kill_switch_flatten_recovered" in _events(env)


def test_a_partial_flatten_is_a_FAILURE_even_though_nothing_raised(env):
    """The defect this was built on top of.

    `flatten_all()` is cancel_orders() + close_all_positions(), and Alpaca's
    close-all reports per-position results rather than raising when only some
    close. So a flatten that closed 3 of 5 was recorded as complete and the two
    still open were invisible. Success means the BROKER says the book is empty.
    """
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}})     # never clears, never raises

    assert flatten_recovery.run(broker, env, CFG) == "retrying"

    assert broker.flatten_calls == 1
    assert risk.flatten_pending(), "a partial flatten must stay pending"
    assert "kill_switch_flatten_retry" in _events(env)


def test_it_retries_until_the_book_is_flat(env):
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}})
    assert flatten_recovery.run(broker, env, CFG) == "retrying"
    broker._clears = True                        # the outage passes
    assert flatten_recovery.run(broker, env, CFG) == "recovered"
    assert not risk.flatten_pending()


# --------------------------------------------------------- the budget stops it

def test_it_abandons_at_the_budget_and_stops_counting(env):
    """Bounded, and quiet once bounded.

    An alert repeating every 15 minutes forever is one an operator learns to
    swipe away, which is worse than none. After the budget it escalates ONCE
    and then returns 'abandoned' without spending further attempts.
    """
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}})

    assert flatten_recovery.run(broker, env, CFG) == "retrying"     # 1/3
    assert flatten_recovery.run(broker, env, CFG) == "retrying"     # 2/3
    assert flatten_recovery.run(broker, env, CFG) == "abandoned"    # 3/3
    calls_at_abandon = broker.flatten_calls
    assert risk.flatten_attempts() == 3

    # Further runs must not keep trying, keep counting, or keep alerting.
    assert flatten_recovery.run(broker, env, CFG) == "abandoned"
    assert flatten_recovery.run(broker, env, CFG) == "abandoned"
    assert broker.flatten_calls == calls_at_abandon
    assert risk.flatten_attempts() == 3
    assert _events(env).count("kill_switch_flatten_abandoned") == 1
    assert risk.flatten_pending(), (
        "abandoning must NOT clear the marker — the exposure is still there "
        "and clearing it would hide an unresolved incident")


def test_an_unreadable_book_after_a_flatten_is_not_success(env):
    """If positions() itself fails we cannot claim the book is flat."""
    _kill_switch_halt()

    class BlindBroker(StubBroker):
        def positions(self):
            raise RuntimeError("positions endpoint down")

    assert flatten_recovery.run(BlindBroker({"SPY": {"qty": 1}}),
                                env, CFG) == "retrying"
    assert risk.flatten_pending()


# --------------------------------------------------------------- the window

@pytest.mark.parametrize("stamp,expected", [
    ("2026-08-03T09:30", True),    # Monday, the open
    ("2026-08-03T15:59", True),    # Monday, one minute before the close
    ("2026-08-03T09:29", False),   # pre-open: a market order would be rejected
    ("2026-08-03T16:00", False),   # the close
    ("2026-08-03T03:00", False),   # overnight
    ("2026-08-01T11:00", False),   # Saturday
    ("2026-08-02T11:00", False),   # Sunday
])
def test_the_retry_window_is_the_regular_session(stamp, expected):
    """Attempts outside the session would be rejected for reasons that have
    nothing to do with the outage, and would still spend the budget."""
    from datetime import datetime
    when = datetime.fromisoformat(stamp).replace(tzinfo=flatten_recovery.ET)
    assert flatten_recovery.within_retry_window(when) is expected


def test_outside_the_window_it_does_not_spend_an_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    monkeypatch.setattr(flatten_recovery, "within_retry_window", lambda *a: False)
    _kill_switch_halt()
    broker = StubBroker({"SPY": {"qty": 1}})
    ledger = Ledger("memory/ledger.jsonl")

    assert flatten_recovery.run(broker, ledger, CFG) == "outside_window"

    assert broker.flatten_calls == 0
    assert risk.flatten_attempts() == 0


# ------------------------------------------- it can only close, never open

def test_recovery_holds_no_path_that_could_OPEN_a_position():
    """Proven against the source, so it holds for whatever a refactor does.

    Same shape as the AST walk in test_halt_switch.py. This module exists to
    finish a liquidation the daily-loss rail already started — it is not a new
    reason to trade, and nothing in it may submit a buy.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "src", "flatten_recovery.py")).read()
    tree = ast.parse(src)
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    forbidden = {"market_order", "bracket_market_order", "submit_order",
                 "buy", "pre_trade_checks", "size_order"}
    assert not called & forbidden, f"recovery can open a position: {called & forbidden}"
    assert "engage_halt" not in called, (
        "recovery must not engage or re-engage a halt — it only ever finishes "
        "one the kill switch already started")


# ------------------------------------------------- the scheduler can express it

def test_due_fires_on_every_minute_in_a_collection_and_no_others():
    """`due()` accepted only an exact int minute until flatten-retry needed to
    run four times an hour. It already took a collection for HOURS, so this is
    the symmetric case — pinned because a silent regression to int-only would
    make the job fire once an hour and look like it was working."""
    import importlib.util
    from datetime import datetime
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "sched_minutes", os.path.join(root, "scripts", "scheduler.py"))
    sched = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sched)

    job = next(j for j in sched.JOBS if j[0] == "flatten-retry")
    monday = datetime(2026, 8, 3, 11, 0)
    for minute in (0, 15, 30, 45):
        assert sched.due(job, monday.replace(minute=minute)), minute
    for minute in (1, 14, 29, 44, 59):
        assert not sched.due(job, monday.replace(minute=minute)), minute
    assert not sched.due(job, datetime(2026, 8, 1, 11, 0))    # Saturday
