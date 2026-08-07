"""Alpaca broker wrapper. All exchange interaction goes through here.

Paper trading is the default and is enforced by a double interlock:
config.yaml `mode: live` AND .env `LIVE_TRADING_CONFIRMED=YES` are both
required before a single live order can be placed.

RESILIENCE (2026-08-06). Two things were measured before any of it was written,
and both changed the shape of the fix:

1. **The SDK already retries — the wrong errors.** `alpaca-py` 0.43.5's
   `RESTClient.__init__` sets `_retry=3`, `_retry_wait=3`,
   `_retry_codes=[429, 504]`, and neither `TradingClient` nor
   `StockHistoricalDataClient` exposes those parameters, so the module defaults
   always apply. Rate limits and gateway timeouts have been retried since day
   one. The audit line "zero retry/backoff in broker.py" was wrong.

2. **Nothing has ever had a timeout, and THAT is the defect.**
   `RESTClient._request` builds `opts = {"headers": ..., "allow_redirects":
   False}` with NO `timeout` key, and `requests.Session.request` defaults to
   `None` — wait forever. Measured on the live bot 2026-08-05: 52.5s on
   account+positions, 89.7s on one symbol's bars, 103.7s on an open-order
   lookup. All three ended in `RemoteDisconnected` — a connection-level error,
   not a 429/504 — so the SDK's retry never engaged. 246 seconds, four minutes
   of a fifteen-minute window, spent on dead sockets.

Hence the ordering: **timeout first, retry second.** A retry without a timeout
is strictly worse than no retry at all — three attempts at 90s is 4.5 minutes
on a single symbol, and the window closes at 16:00 (divergence #18).
"""
import functools
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, TakeProfitRequest,
                                     StopLossRequest, GetOrdersRequest,
                                     GetOrderByIdRequest)
from alpaca.trading.enums import (OrderSide, TimeInForce, OrderClass,
                                  QueryOrderStatus)
from alpaca.data.enums import Adjustment
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

log = logging.getLogger("broker")

_TIMEFRAMES = {
    "1Min": TimeFrame(1, TimeFrameUnit.Minute),
    "5Min": TimeFrame(5, TimeFrameUnit.Minute),
    "15Min": TimeFrame(15, TimeFrameUnit.Minute),
    "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
    "1Day": TimeFrame(1, TimeFrameUnit.Day),
}


# ---------- resilience knobs (all overridable from config.yaml `ops`) --------

DEFAULT_TIMEOUT_SEC = 10.0        # read timeout; 0 disables the whole patch
DEFAULT_CONNECT_TIMEOUT_SEC = 5.0
DEFAULT_RETRY_ATTEMPTS = 2        # EXTRA attempts, so 3 calls worst case
DEFAULT_RETRY_BUDGET_SEC = 60.0   # per Broker instance == per cycle
_BACKOFF_SEC = (0.5, 1.5)         # attempt 0 -> 0.5s, attempt 1 -> 1.5s

# Connection-class failures ONLY. An APIError means the request arrived and was
# refused — retrying it is asking the same question and being told no again,
# and on an order path it is how a duplicate gets born. `ConnectionError`
# covers urllib3's ProtocolError/RemoteDisconnected, which is the shape both
# 2026-08-05 hangs actually took.
_RETRYABLE = (requests.exceptions.ConnectionError,
              requests.exceptions.Timeout)


class RetryBudget:
    """Seconds this Broker may spend retrying, across every call it makes.

    Per-call retry limits are not enough here. The cycle fetches bars for ~40
    symbols one at a time; 40 x 2 retries x a 10s timeout is twenty minutes
    against a fifteen-minute window. A resilience fix that can overrun the
    close would trade one failure for divergence #18, which is worse.

    Charged with the elapsed time of each FAILED attempt plus the sleep that
    follows it — not with wall clock, and not with successful calls, because
    neither is what the budget is protecting against.
    """

    def __init__(self, total_sec: float):
        self.total = max(0.0, float(total_sec))
        self.spent = 0.0
        self.exhausted_reported = False

    def charge(self, seconds: float) -> None:
        self.spent += max(0.0, seconds)

    def remaining(self) -> float:
        return max(0.0, self.total - self.spent)


def install_timeout(client, timeout: float,
                    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SEC):
    """Give one alpaca-py client a default socket timeout. Idempotent.

    The SDK takes no timeout parameter anywhere, so this wraps the bound
    `request` of the `requests.Session` it holds. WRAPS rather than REPLACES:
    a fresh Session would silently discard any adapter, header or cookie the
    SDK configured on its own, now or in a future version.

    `_session` is a PRIVATE attribute of a third-party package and this is a
    bet on it. The bet is made falsifiable rather than hopeful: an SDK that
    renames or retypes it raises HERE, at construction, which surfaces as a
    refusal to start rather than as every call silently losing its timeout
    again. `tests/test_broker_resilience.py` mutates exactly that.
    """
    session = getattr(client, "_session", None)
    if not isinstance(session, requests.Session):
        raise RuntimeError(
            f"{type(client).__name__} has no requests.Session at `_session` "
            f"(got {type(session).__name__}) — alpaca-py changed shape and "
            f"broker calls would run with NO TIMEOUT, which is the 2026-08-05 "
            f"failure (246s on three dead sockets). Re-point "
            f"broker.install_timeout() at the new attribute before upgrading.")
    if getattr(session, "_repete_timeout", None) is not None:
        return session
    original = session.request
    default = (min(connect_timeout, timeout), timeout)

    @functools.wraps(original)
    def request(*args, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = default
        return original(*args, **kwargs)

    session.request = request
    session._repete_timeout = default
    return session


def _backoff_sec(attempt: int) -> float:
    """Jittered backoff. Jitter is +0-50%, so two symbols failing in the same
    millisecond do not retry in lockstep."""
    base = _BACKOFF_SEC[min(attempt, len(_BACKOFF_SEC) - 1)]
    return base * (1.0 + random.random() * 0.5)


def retryable_read(method):
    """Retry a READ on connection-class failures, inside the shared budget.

    Reads only, and the exclusion is structural rather than documented:
    `tests/test_broker_resilience.py` walks this module's AST, asserts every
    write method is undecorated, and asserts the read/write split NAMES every
    public method on Broker — so a method added later fails the suite until
    somebody classifies it.

    Why writes are excluded even though both live submission sites pass a
    `client_order_id`: on retry Alpaca rejects the duplicate, and the caller
    would then have to tell "already placed, this is success" apart from "real
    failure". Getting that classifier wrong doubles a position. The timeout is
    the large win on writes; automatic re-submission is a new way to be wrong
    for a small one. Writes keep the SDK's own 3x 429/504 retry.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        attempts = getattr(self, "_retry_attempts", 0)
        budget = getattr(self, "_retry_budget", None)
        for attempt in range(attempts + 1):
            started = time.monotonic()
            try:
                return method(self, *args, **kwargs)
            except _RETRYABLE as exc:
                elapsed = time.monotonic() - started
                if attempt >= attempts or budget is None:
                    raise
                budget.charge(elapsed)
                if budget.remaining() <= 0:
                    if not budget.exhausted_reported:
                        budget.exhausted_reported = True
                        log.error(
                            "broker retry budget of %.0fs is spent — further "
                            "failures this cycle fail immediately rather than "
                            "eating into the close", budget.total)
                    raise
                delay = min(_backoff_sec(attempt), budget.remaining())
                budget.charge(delay)
                log.warning("%s failed (%s) — retry %d/%d in %.1fs",
                            method.__name__, exc, attempt + 1, attempts, delay)
                time.sleep(delay)
        raise AssertionError("unreachable")   # pragma: no cover

    wrapper._repete_retryable = True
    return wrapper


def _safe_multiplier(raw) -> float:
    """Parse an account's `multiplier` defensively; 1.0 (no leverage, the
    safe direction) is the fallback for anything that isn't a real positive
    number.

    THE TRAP: `float(raw or 1)` looks like a safe fallback but checks the
    TRUTHINESS of the raw attribute, not the parsed number. Alpaca's SDK
    returns numeric account fields as STRINGS throughout this module, and
    the string "0" is truthy in Python — only an EMPTY string is falsy — so
    `"0" or 1` evaluates to `"0"` and `float("0")` sails through as 0.0.
    `multiplier` is exactly the kind of value a later phase divides by or
    multiplies buying power against, so a silent 0.0 here would zero out
    sizing or raise ZeroDivisionError far from its cause. The fix is to
    parse FIRST and gate on the parsed number, not on the raw value.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


class Broker:
    def __init__(self, cfg: dict):
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise SystemExit(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY missing — paste your paper "
                "keys into .env (get them at https://app.alpaca.markets, PAPER "
                "dashboard). See .env.example.")

        live_requested = cfg.get("mode", "paper") == "live"
        live_confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "NO") == "YES"
        self.paper = not (live_requested and live_confirmed)
        if live_requested and not live_confirmed:
            log.warning("config requests live mode but LIVE_TRADING_CONFIRMED != YES "
                        "— staying in PAPER mode.")
        log.info("Broker mode: %s", "PAPER" if self.paper else "LIVE")

        self.trading = TradingClient(key, secret, paper=self.paper)
        self.data = StockHistoricalDataClient(key, secret)

        ops = cfg.get("ops") or {}
        timeout = float(ops.get("broker_timeout_sec", DEFAULT_TIMEOUT_SEC))
        self._retry_attempts = int(
            ops.get("broker_retry_attempts", DEFAULT_RETRY_ATTEMPTS))
        self._retry_budget = RetryBudget(
            ops.get("broker_retry_budget_sec", DEFAULT_RETRY_BUDGET_SEC))
        # A fresh budget per Broker, and main.py builds one Broker per cycle —
        # so the budget is per cycle without anything having to reset it.
        if timeout > 0:
            install_timeout(self.trading, timeout)
            install_timeout(self.data, timeout)
        else:
            log.warning("ops.broker_timeout_sec is 0 — broker calls can hang "
                        "indefinitely (the 2026-08-05 failure mode)")

    # ---------- account / state (source of truth — see state.py) ----------

    @retryable_read
    def account(self) -> dict:
        a = self.trading.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "last_equity": float(a.last_equity),  # equity at previous close
            "buying_power": float(a.buying_power),
            # FAIL CLOSED. `getattr` default False, not True: an account whose
            # capability we cannot read must not be assumed to have it. A
            # cash account silently rejects every short at submission, one
            # order at a time, and the book reverts to long-only with nothing
            # anywhere saying why.
            "shorting_enabled": bool(getattr(a, "shorting_enabled", False)),
            "multiplier": _safe_multiplier(getattr(a, "multiplier", None)),
        }

    @retryable_read
    def positions(self) -> dict:
        out = {}
        for p in self.trading.get_all_positions():
            out[p.symbol] = {
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
            }
        return out

    # ---------- market data ----------

    @retryable_read
    def bars(self, symbol: str, timeframe: str, limit: int):
        """Return a list of dicts (ts, open, high, low, close, volume), oldest first."""
        # Without an explicit start, Alpaca windows the query to the current
        # day and returns a single bar. 2 calendar days per requested daily
        # bar comfortably covers weekends and holidays.
        # NOTE: the API-side `limit` truncates from the OLDEST end of the
        # window, so a large lookback would return stale months-old bars.
        # Fetch the whole window and slice the most recent `limit` here.
        # DIVERGENCE #12 (W2-3, 2026-07-29): adjustment was omitted, and
        # Alpaca's default is RAW. Every frozen snapshot is split- AND
        # dividend-adjusted (yfinance auto_adjust=True; the intraday file uses
        # adjustment=ALL), so the live bot was reading a different price series
        # from the one every gate scored.
        #
        # This is not theoretical here. data/snapshots/MANIFEST.json records the
        # first intraday build going out RAW, carrying 11 unadjusted splits
        # "that the simulator read as overnight collapses — it voided a gate
        # run." A 2-for-1 split is a 50% overnight gap in a raw series: it
        # collapses SMAs, spikes ATR, and can fire an entry signal or trip a
        # stop on an event that never moved the value of the position.
        #
        # ALL rather than SPLIT: the snapshots are dividend-adjusted too, and
        # matching them is the whole point.
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_TIMEFRAMES[timeframe],
            start=datetime.now(timezone.utc) - timedelta(days=limit * 2),
            adjustment=Adjustment.ALL,
        )
        resp = self.data.get_stock_bars(req)
        rows = resp.data.get(symbol, [])
        return [
            {"ts": b.timestamp.isoformat(), "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in rows
        ][-limit:]

    @retryable_read
    def latest_price(self, symbol: str) -> float:
        """Most recent trade price — the entry drift guard compares this
        against the bar-close the signal priced from. Raises on failure;
        the caller fails OPEN (bars freshness covers the outage class)."""
        from alpaca.data.requests import StockLatestTradeRequest
        resp = self.data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=symbol))
        return float(resp[symbol].price)

    # ---------- orders ----------

    def market_order(self, symbol: str, qty: float, side: str,
                     client_order_id: str | None = None) -> dict:
        # client_order_id = idempotency key: Alpaca rejects a duplicate, so a
        # crash-and-rerun cycle cannot double-submit the same intended order.
        #
        # Explicit map, no fallthrough — the same fix Phase 1 already made to
        # bracket_market_order. "cover" is an EXIT (closes a short) and must
        # be a BUY; the old `... if side == "buy" else SELL` sent it as a
        # SELL, which doubles the short instead of closing it, silently —
        # nothing raised, nothing logged. "short" is an ENTRY and must be a
        # SELL (opens the short) — it happened to fall into the old
        # fallthrough's SELL bucket too, but by accident, alongside every
        # other unrecognised value. Anything else — a typo, a wrong constant
        # — is refused rather than defaulting to SELL, which is exactly what
        # let "cover" slip through unnoticed before.
        if side in ("buy", "cover"):
            order_side = OrderSide.BUY
        elif side in ("sell", "short"):
            order_side = OrderSide.SELL
        else:
            raise ValueError(
                f"side must be 'buy', 'sell', 'short', or 'cover', got {side!r}")
        order = self.trading.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=order_side,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        ))
        log.info("Order submitted: %s %s x%s (id=%s)", side, symbol, qty, order.id)
        # `side` echoes the INPUT verbatim, not the broker-level BUY/SELL —
        # same choice bracket_market_order made (it returns "short", not
        # "sell"). Nothing in this codebase reads this key: main.py stores
        # the whole dict under the ledger record's "order" field for audit,
        # and every place that branches on direction (fill-quality sign,
        # trade_plan, evidence.lineage) reads the ledger record's "action"
        # field instead, which already carries "cover"/"short" losslessly.
        # Collapsing to "buy"/"sell" here would throw away exactly the
        # buy-vs-cover / sell-vs-short distinction this group exists to get
        # right, for no consumer that asked for it.
        return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": side,
                "status": str(order.status)}

    def bracket_market_order(self, symbol: str, qty: float, stop_price: float,
                             take_profit_price: float | None = None,
                             client_order_id: str | None = None,
                             side: str = "buy",
                             entry_price: float | None = None) -> dict:
        """Market order with protective legs, GTC so they survive across days.

        Alpaca requires BOTH legs for OrderClass.BRACKET; with no take-profit
        we submit OTO (stop-loss leg only) instead.

        `side` must be "buy" or "short" — anything else raises. "sell" is
        deliberately NOT an alias for "short": this codebase's own vocabulary
        (ENTRY_ACTIONS in strategies/base.py, and risk.py's desync_sell guard)
        already uses "sell" to mean CLOSE a long, so accepting it here would
        let a caller passing sig.action straight through silently open a
        short while meaning to exit one.

        A short's stop sits ABOVE its entry, a long's BELOW — the geometry
        inverts with direction, and a stop on the wrong side can never
        trigger. Because a short's loss is unbounded, `entry_price` is
        REQUIRED for "short" — but required only means present and positive;
        it is the CALLER's job to pass the real entry, since a placeholder
        like 0.0 (e.g. a failed quote lookup defaulting to zero) would pass
        "is it there" while making the geometry check pass trivially for
        every stop, which is worse than no check at all. `entry_price` stays
        OPTIONAL for "buy" — src/main.py's only live call site passes none,
        and Phase 1 promises byte-identical live behaviour — so a long
        submitted without it gets NO geometry check at all. That is
        deliberate, not an oversight.
        """
        if side not in ("buy", "short"):
            raise ValueError(f"side must be 'buy' or 'short', got {side!r}")
        is_short = side == "short"

        # Order matters: entry_price=0.0 (or negative) would otherwise pass
        # "is it None" and then make stop_price<=entry_price / >=entry_price
        # trivially false/true for any real stop — a short with no meaningful
        # check while LOOKING validated, or a long refused for no reason.
        if entry_price is not None and entry_price <= 0:
            raise ValueError(
                f"entry_price {entry_price} is not a real price — a price is "
                f"never non-positive")
        if is_short and entry_price is None:
            raise ValueError(
                "short bracket requires entry_price — a short's stop cannot "
                "go unchecked, its loss is unbounded")

        if entry_price is not None:
            if is_short and stop_price <= entry_price:
                raise ValueError(
                    f"short stop {stop_price} must be above entry "
                    f"{entry_price} — a stop below it can never trigger")
            if not is_short and stop_price >= entry_price:
                raise ValueError(
                    f"long stop {stop_price} must be below entry "
                    f"{entry_price} — a stop above it can never trigger")

        order_class = OrderClass.BRACKET if take_profit_price else OrderClass.OTO
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL if is_short else OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=order_class,
            stop_loss=StopLossRequest(stop_price=stop_price),
            take_profit=(TakeProfitRequest(limit_price=take_profit_price)
                         if take_profit_price else None),
            client_order_id=client_order_id,
        )
        order = self.trading.submit_order(req)
        legs = [str(leg.id) for leg in (order.legs or [])]
        log.info("Bracket order submitted: %s %s x%s stop=%.2f tp=%s "
                 "(id=%s, legs=%s)",
                 "short" if is_short else "buy", symbol, qty, stop_price,
                 take_profit_price, order.id, legs)
        return {"id": str(order.id), "symbol": symbol, "qty": qty,
                "side": "short" if is_short else "buy",
                "status": str(order.status), "order_class": order_class.value,
                "stop_price": stop_price,
                "take_profit_price": take_profit_price, "leg_ids": legs}

    @retryable_read
    def get_order(self, order_id: str) -> dict:
        """Order status incl. legs (nested lookup)."""
        o = self.trading.get_order_by_id(order_id, GetOrderByIdRequest(nested=True))
        return {
            "id": str(o.id),
            "status": str(o.status),
            # `type` drives exit-reason classification in main.resolve_exit_price;
            # omitting it silently labelled every filled leg "take_profit".
            "type": str(o.type),
            "filled_at": o.filled_at.isoformat() if o.filled_at else None,
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "filled_qty": float(o.filled_qty) if o.filled_qty else 0.0,
            "legs": [{"id": str(l.id), "type": str(l.type), "side": str(l.side),
                      "status": str(l.status),
                      "stop_price": float(l.stop_price) if l.stop_price else None,
                      "filled_avg_price": float(l.filled_avg_price) if l.filled_avg_price else None}
                     for l in (o.legs or [])],
        }

    @retryable_read
    def closed_orders(self, symbol: str, limit: int = 20) -> list[dict]:
        """Recently closed orders for one symbol, newest first (reconciliation fallback)."""
        orders = self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=limit))
        return [{"id": str(o.id), "side": str(o.side), "status": str(o.status),
                 "type": str(o.type),
                 # filled_at recovers a position's REAL entry time when adopting
                 # an untracked broker position (Alpaca's Position model carries
                 # no timestamp), so the swing guard measures true age.
                 "filled_at": o.filled_at.isoformat() if o.filled_at else None,
                 "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
                 "filled_qty": float(o.filled_qty) if o.filled_qty else 0.0}
                for o in orders]

    def cancel_open_orders(self, symbol: str) -> int:
        """Cancel all open orders for one symbol (surviving bracket legs reserve
        the shares, so they must go before a strategy-signal market sell)."""
        open_orders = self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN, symbols=[symbol]))
        for o in open_orders:
            self.trading.cancel_order_by_id(o.id)
        if open_orders:
            log.info("Cancelled %d open order(s) on %s", len(open_orders), symbol)
        return len(open_orders)

    @retryable_read
    def open_stop_orders(self) -> list[dict]:
        """All open stop legs (protective exits), for the heat report.
        One row per stop: symbol, qty, stop_price."""
        orders = self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.OPEN))
        out = []
        for o in orders:
            if "stop" in str(o.type).lower() and o.stop_price:
                out.append({"id": str(o.id), "symbol": o.symbol,
                            "qty": float(o.qty or 0),
                            "stop_price": float(o.stop_price)})
        return out

    def replace_stop(self, order_id: str, stop_price: float) -> dict:
        """Raise a resting stop leg (chandelier trail ratchet). NOTE: Alpaca
        replacement mints a NEW order id — reconciliation keeps working
        because resolve_exit_price falls back to closed_orders(symbol)."""
        from alpaca.trading.requests import ReplaceOrderRequest
        o = self.trading.replace_order_by_id(
            order_id, ReplaceOrderRequest(stop_price=stop_price))
        log.info("Stop leg %s replaced -> %s @ %.2f", order_id, o.id, stop_price)
        return {"id": str(o.id), "stop_price": stop_price}

    def last_price(self, symbol: str) -> float | None:
        """Latest daily close — final fallback for reconciliation exit prices.

        Deliberately NOT decorated with `@retryable_read`: its only work is a
        call to `self.bars`, which is. Decorating both would nest one retry
        loop inside another and multiply the attempts (3 x 3 = 9) while each
        layer believed it was allowing three.
        """
        bars = self.bars(symbol, "1Day", 1)
        return bars[-1]["close"] if bars else None

    def flatten_all(self):
        """Kill switch: cancel everything and close all positions."""
        log.warning("FLATTEN ALL: cancelling orders and closing positions")
        self.trading.cancel_orders()
        self.trading.close_all_positions(cancel_orders=True)
