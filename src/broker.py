"""Alpaca broker wrapper. All exchange interaction goes through here.

Paper trading is the default and is enforced by a double interlock:
config.yaml `mode: live` AND .env `LIVE_TRADING_CONFIRMED=YES` are both
required before a single live order can be placed.
"""
import os
import logging
from datetime import datetime, timedelta, timezone

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (MarketOrderRequest, TakeProfitRequest,
                                     StopLossRequest, GetOrdersRequest,
                                     GetOrderByIdRequest)
from alpaca.trading.enums import (OrderSide, TimeInForce, OrderClass,
                                  QueryOrderStatus)
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

    # ---------- account / state (source of truth — see state.py) ----------

    def account(self) -> dict:
        a = self.trading.get_account()
        return {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "last_equity": float(a.last_equity),  # equity at previous close
            "buying_power": float(a.buying_power),
        }

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

    def bars(self, symbol: str, timeframe: str, limit: int):
        """Return a list of dicts (ts, open, high, low, close, volume), oldest first."""
        # Without an explicit start, Alpaca windows the query to the current
        # day and returns a single bar. 2 calendar days per requested daily
        # bar comfortably covers weekends and holidays.
        # NOTE: the API-side `limit` truncates from the OLDEST end of the
        # window, so a large lookback would return stale months-old bars.
        # Fetch the whole window and slice the most recent `limit` here.
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=_TIMEFRAMES[timeframe],
            start=datetime.now(timezone.utc) - timedelta(days=limit * 2),
        )
        resp = self.data.get_stock_bars(req)
        rows = resp.data.get(symbol, [])
        return [
            {"ts": b.timestamp.isoformat(), "open": b.open, "high": b.high,
             "low": b.low, "close": b.close, "volume": b.volume}
            for b in rows
        ][-limit:]

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
        order = self.trading.submit_order(MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        ))
        log.info("Order submitted: %s %s x%s (id=%s)", side, symbol, qty, order.id)
        return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": side,
                "status": str(order.status)}

    def bracket_market_order(self, symbol: str, qty: float, stop_price: float,
                             take_profit_price: float | None = None,
                             client_order_id: str | None = None) -> dict:
        """BUY market order with protective legs, GTC so they survive across days.

        Alpaca requires BOTH legs for OrderClass.BRACKET; with no take-profit
        we submit OTO (stop-loss leg only) instead.
        """
        order_class = OrderClass.BRACKET if take_profit_price else OrderClass.OTO
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=order_class,
            stop_loss=StopLossRequest(stop_price=stop_price),
            take_profit=(TakeProfitRequest(limit_price=take_profit_price)
                         if take_profit_price else None),
            client_order_id=client_order_id,
        )
        order = self.trading.submit_order(req)
        legs = [str(leg.id) for leg in (order.legs or [])]
        log.info("Bracket order submitted: buy %s x%s stop=%.2f tp=%s (id=%s, legs=%s)",
                 symbol, qty, stop_price, take_profit_price, order.id, legs)
        return {"id": str(order.id), "symbol": symbol, "qty": qty, "side": "buy",
                "status": str(order.status), "order_class": order_class.value,
                "stop_price": stop_price, "take_profit_price": take_profit_price,
                "leg_ids": legs}

    def get_order(self, order_id: str) -> dict:
        """Order status incl. legs (nested lookup)."""
        o = self.trading.get_order_by_id(order_id, GetOrderByIdRequest(nested=True))
        return {
            "id": str(o.id),
            "status": str(o.status),
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else None,
            "filled_qty": float(o.filled_qty) if o.filled_qty else 0.0,
            "legs": [{"id": str(l.id), "type": str(l.type), "side": str(l.side),
                      "status": str(l.status),
                      "stop_price": float(l.stop_price) if l.stop_price else None,
                      "filled_avg_price": float(l.filled_avg_price) if l.filled_avg_price else None}
                     for l in (o.legs or [])],
        }

    def closed_orders(self, symbol: str, limit: int = 20) -> list[dict]:
        """Recently closed orders for one symbol, newest first (reconciliation fallback)."""
        orders = self.trading.get_orders(GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, symbols=[symbol], limit=limit))
        return [{"id": str(o.id), "side": str(o.side), "status": str(o.status),
                 "type": str(o.type),
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
        """Latest daily close — final fallback for reconciliation exit prices."""
        bars = self.bars(symbol, "1Day", 1)
        return bars[-1]["close"] if bars else None

    def flatten_all(self):
        """Kill switch: cancel everything and close all positions."""
        log.warning("FLATTEN ALL: cancelling orders and closing positions")
        self.trading.cancel_orders()
        self.trading.close_all_positions(cancel_orders=True)
