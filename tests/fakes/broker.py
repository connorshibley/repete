"""One broker fake, complete against `broker.Broker`'s public surface.

WHY THIS FILE EXISTS (Phase 5a, 2026-08-06)
-------------------------------------------
There were **29 hand-rolled broker fakes** in this suite (21 module-level, 8
nested) and **only 2 of them defined `latest_price`**. `src/main.py` calls it
in the entry drift guard. Every `run_cycle()` driven by `FakeCycleBroker`
therefore took the guard's fail-OPEN branch, which is loud by design:

    WARNING main:main.py:1539 SPY: drift check skipped, quote unavailable
            ('FakeCycleBroker' object has no attribute 'latest_price')

Two consequences, and the second is the nastier one:

  1. the drift guard's INTERACTION with the rest of the cycle was never
     exercised in the flagship cycle file (the guard's own arithmetic was —
     `tests/test_drift_guard.py` subclasses the fake and adds the method);
  2. every one of those tests emitted a **spurious `degradation` ledger
     event**, caused by the fake rather than by the code under test — and
     `degradation` is precisely the event counted against
     `ops.max_degradations_per_day`.

A test double that silently omits a method does not fail. It changes which
branch runs and then agrees with whatever happens. Phase 6 is about to move a
692-line function; a net woven from fakes like that would freeze the
AttributeError branch as correct and certify the refactor against it.

WHAT KEEPS IT HONEST
--------------------
`tests/test_broker_conformance.py` walks `inspect.signature` over every public
method of the real `Broker` and requires this class to accept the same calls,
and separately requires **every broker-shaped class under tests/** to be named
as conformant or explicitly exempt-with-a-reason. A fake added later fails the
suite until somebody classifies it — the same no-silent-bucket rule the Phase 4
write-retry exclusion uses.

DESIGN NOTES INHERITED FROM THE FAKES THIS REPLACES — do not "simplify" these:

  * `positions()` returns THE SAME dict every time. `src/main.py` does
    `positions = broker.positions()` and mutates it in place, never a copy, so
    tests observe the in-cycle position view directly instead of re-deriving
    what it "should" contain. A re-derivation passes whenever it is wrong in
    the same direction as the code. (from `_ScriptedCycleBroker`)
  * `bracket_market_order` captures keywords VERBATIM via `**extra` rather than
    named `side`/`entry_price` parameters, so a test can assert a long entry
    sent NO `side` and NO `entry_price` at all — a claim that defaulted named
    parameters cannot distinguish from "sent the defaults". (same source)
  * `flatten_all` raises. The kill switch firing in a test that did not ask for
    it is a failure, not a no-op.
"""


class ConformantBroker:
    """Complete stand-in for `broker.Broker`, scripted per test.

    `latest_price` defaults to the last bar's close — i.e. zero drift, so the
    entry drift guard RUNS and PASSES. That is the deliberate default: the
    guard executing is the point, and a fake that made it trip everywhere would
    just be the old bug with the polarity flipped.
    """

    def __init__(self, bars, positions=None, orders=None, closed=None,
                 live_price=None, quote_error=False):
        self._bars = bars
        self.view = {} if positions is None else positions
        self._orders = orders or {}
        self._closed = closed or []
        self._live = live_price
        self._quote_error = quote_error
        self.is_open = True          # market_open(); tests flip for the
        self.clock_error = False     # closed-market / clock-down paths
        self.submitted = []
        self.bracket_calls = []
        self.canceled = []
        self.replaced = []
        self.calls = []              # every method name, in order

    # ---------- account / state ----------

    def account(self) -> dict:
        self.calls.append("account")
        return {"equity": 100_000.0, "cash": 100_000.0,
                "last_equity": 100_000.0, "buying_power": 100_000.0,
                "shorting_enabled": True, "multiplier": 1.0}

    def positions(self) -> dict:
        self.calls.append("positions")
        return self.view

    # ---------- market data ----------

    def market_open(self) -> bool:
        """Mirrors Broker.market_open (2026-08-11, swing_scan's closed-market
        guard). Always open by default — a test that wants the closed or
        clock-down path sets `fake.is_open = False` or `clock_error = True`.
        The guard FAILS CLOSED on an exception, and the conformance registry
        is what forces this fake to model that surface at all."""
        self.calls.append("market_open")
        if self.clock_error:
            raise RuntimeError("clock endpoint down")
        return self.is_open

    def bars(self, symbol, timeframe, limit):
        self.calls.append("bars")
        return self._bars

    def latest_price(self, symbol):
        self.calls.append("latest_price")
        if self._quote_error:
            raise RuntimeError("quote feed down")
        if self._live is not None:
            return self._live
        return self._bars[-1]["close"]

    def last_price(self, symbol):
        self.calls.append("last_price")
        return self._bars[-1]["close"] if self._bars else None

    # ---------- orders ----------

    def market_order(self, symbol, qty, side, client_order_id=None):
        self.calls.append("market_order")
        order = {"id": f"plain-{len(self.submitted)}", "symbol": symbol,
                 "qty": qty, "side": side, "status": "accepted",
                 "client_order_id": client_order_id}
        self.submitted.append(order)
        return order

    def bracket_market_order(self, symbol, qty, stop_price,
                             take_profit_price=None, client_order_id=None,
                             **extra):
        self.calls.append("bracket_market_order")
        self.bracket_calls.append({"symbol": symbol, "qty": qty,
                                   "stop_price": stop_price,
                                   "take_profit_price": take_profit_price,
                                   **extra})
        order = {"id": "entry-1", "symbol": symbol, "qty": qty,
                 "side": extra.get("side", "buy"), "status": "accepted",
                 "order_class": "bracket", "stop_price": stop_price,
                 "take_profit_price": take_profit_price,
                 "leg_ids": ["leg-stop", "leg-tp"],
                 "client_order_id": client_order_id}
        self.submitted.append(order)
        return order

    def get_order(self, order_id):
        self.calls.append("get_order")
        if order_id in self._orders:
            return self._orders[order_id]
        return {"id": order_id, "status": "accepted", "type": "market",
                "filled_at": None, "filled_avg_price": None,
                "filled_qty": 0.0, "legs": []}

    def closed_orders(self, symbol, limit=20):
        self.calls.append("closed_orders")
        return list(self._closed)

    def cancel_open_orders(self, symbol):
        self.calls.append("cancel_open_orders")
        self.canceled.append(symbol)
        return 0

    def open_stop_orders(self):
        self.calls.append("open_stop_orders")
        return []

    def replace_stop(self, order_id, stop_price):
        self.calls.append("replace_stop")
        self.replaced.append((order_id, stop_price))
        return {"id": f"{order_id}-r", "stop_price": stop_price}

    def flatten_all(self):
        raise AssertionError(
            "the kill switch must not fire in a test that did not ask for it")
