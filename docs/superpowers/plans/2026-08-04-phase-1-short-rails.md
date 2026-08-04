# Phase 1 — Make Shorts Survivable Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every risk rail, bracket and preflight check short-aware, while shipping nothing that shorts — live behaviour stays byte-identical.

**Architecture:** Direction comes from `Signal.action` (`buy`/`short` are entries, `sell`/`cover` are exits); quantity stays **unsigned**. That one decision keeps invariant #2 intact for free and avoids a signed-quantity refactor across `risk`, `backtest` and the ledger. Rails that summed `market_value` learn to use `abs()`; a new directional net-exposure band constrains how far the short leg can pull net exposure down.

**Tech Stack:** Python 3.11, pytest, `alpaca-py`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-130-30-long-short-design.md`

**Out of scope (Phase 2/3):** `xsmom`'s short leg, ETF exclusion, leg attribution, the DIAGNOSTIC gate, `backtest.py` signed positions. Nothing here emits a short order.

---

## One correction to the spec, found while planning

The spec listed "invariant #2 inverts on the short side" as a hazard needing a
code fix. **It does not.** `src/llm.py:143` already clamps
`verdict["scale"] = min(max(float(...), 0.0), 1.0)`, and because quantity stays
unsigned, `int(full_qty * scale)` shrinks a short toward zero exactly as it
shrinks a long. Task 7 pins this as a **regression test plus a structural guard**
rather than shipping a fix for a non-bug.

## File structure

| file | responsibility after this phase |
|---|---|
| `src/strategies/base.py` | owns the action vocabulary — `ENTRY_ACTIONS`, `EXIT_ACTIONS` |
| `src/risk.py` | rails read exposure with `abs()`; entries gated on `ENTRY_ACTIONS`; new net band |
| `src/broker.py` | brackets work on both sides; account reports shorting capability |
| `src/preflight.py` | refuses to start when shorts are configured but the broker forbids them |
| `tests/test_short_rails.py` | **new** — every short-side rail property, in boundary pairs |

---

### Task 1: Gross exposure must count a short as exposure

**Files:**
- Modify: `src/risk.py:1116` (the `regime_exposure` block) and `src/risk.py:1123` (the position cap)
- Test: `tests/test_short_rails.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_short_rails.py`:

```python
"""Short-side rail properties. Phase 1 ships no shorts — these pin the rails
BEFORE anything can emit one.

The failure mode being prevented is silent. Alpaca reports a short position's
`market_value` as NEGATIVE, so a rail that sums raw values nets a long against a
short: a book carrying 100% gross reads as 0% and the cap simply stops binding.
Nothing logs, nothing raises, and the rail appears to be working.

Boundary pairs throughout, in the style of tests/test_credit_gate.py: each
blocked case sits beside a permitted one differing in exactly the thing the rail
measures.
"""
import pytest

import risk

ACCOUNT = {"equity": 100_000.0}


def _cfg(**overrides):
    base = {"max_position_pct": 100.0, "max_open_positions": 0}
    base.update(overrides)
    return {"risk": base}


# ---- gross exposure counts magnitude, not sign ----

def test_a_short_adds_to_gross_exposure_rather_than_cancelling_a_long():
    """$50k long + $50k short is a 100% gross book, not a 0% one."""
    cfg = _cfg(regime_exposure={"enabled": True, "down_max_gross_pct": 50})
    positions = {"AAPL": {"market_value": 50_000.0},
                 "TSLA": {"market_value": -50_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "MSFT", 1, 1.0, ACCOUNT, positions, cfg,
                         regime_label="down_trend")
    assert e.value.rail == "regime_exposure"


def test_the_same_book_under_the_cap_is_permitted():
    """The paired half. A rail that blocked everything would also pass the
    test above, and would be just as broken."""
    cfg = _cfg(regime_exposure={"enabled": True, "down_max_gross_pct": 50})
    positions = {"AAPL": {"market_value": 40_000.0}}
    risk.pure_checks("buy", "MSFT", 1, 1.0, ACCOUNT, positions, cfg,
                     regime_label="down_trend")


def test_the_concentration_cap_measures_a_short_by_magnitude():
    """Adding to an existing SHORT in the same name must count against the
    per-name cap. Raw addition would read -$9,000 + $2,000 as tiny."""
    cfg = _cfg(max_position_pct=10.0)
    positions = {"TSLA": {"market_value": -9_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "TSLA", 20, 100.0, ACCOUNT, positions, cfg)
    assert e.value.rail == "position_cap"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v`
Expected: FAIL — `DID NOT RAISE risk.RiskRejection` on tests 1 and 3.

- [ ] **Step 3: Fix both call sites**

In `src/risk.py`, inside `pure_checks`, change the position cap from:

```python
        existing = positions.get(symbol, {}).get("market_value", 0.0)
        if existing + order_value > account["equity"] * r["max_position_pct"] / 100:
```

to:

```python
        # abs(): Alpaca reports a SHORT's market_value as negative, so raw
        # addition reads -$9,000 + $2,000 as a $7,000 position and the per-name
        # cap stops binding on exactly the side where it matters. Magnitude is
        # what concentration means.
        existing = abs(positions.get(symbol, {}).get("market_value", 0.0))
        if existing + order_value > account["equity"] * r["max_position_pct"] / 100:
```

And the gross sum from:

```python
            gross = sum(p.get("market_value", 0.0) for p in positions.values())
```

to:

```python
            # abs() for the same reason: a long and a short would otherwise net
            # toward zero and a 100%-gross book would report as 0%. GROSS is a
            # sum of magnitudes; net is a different number with its own rail.
            gross = sum(abs(p.get("market_value", 0.0))
                        for p in positions.values())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full suite — this touches a rail every gate depends on**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. Existing books are long-only, so `abs()` is a no-op on them.

- [ ] **Step 6: Commit**

```bash
git add src/risk.py tests/test_short_rails.py
git commit -m "Gross and concentration caps measure magnitude, not sign"
```

---

### Task 2: An action vocabulary, so rails can tell entries from exits

**Files:**
- Modify: `src/strategies/base.py`
- Modify: `src/risk.py` (`pure_checks`: the `action == "buy"` gate and the desync guard)
- Test: `tests/test_short_rails.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_rails.py`:

```python
# ---- shorts are ENTRIES; covers are EXITS ----

def test_a_short_passes_through_the_entry_rails():
    """A short is an entry and must meet every entry rail. Gating the entry
    block on `action == "buy"` would let shorts bypass the concentration cap,
    the drawdown breaker and the slot limits entirely."""
    cfg = _cfg(max_position_pct=10.0)
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 200, 100.0, ACCOUNT, {}, cfg)
    assert e.value.rail == "position_cap"


def test_a_cover_with_no_position_is_a_desync():
    """The mirror of the sell guard. Covering something you are not short is a
    state desync, and sending it would OPEN a long."""
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, {}, _cfg())
    assert e.value.rail == "desync_cover"


def test_a_cover_against_a_real_short_is_allowed():
    """The paired half."""
    positions = {"TSLA": {"market_value": -5_000.0}}
    risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "entry_rails or desync or cover_against"`
Expected: FAIL — `DID NOT RAISE` on the first two.

- [ ] **Step 3: Add the vocabulary**

In `src/strategies/base.py`, directly below the `Signal` dataclass:

```python
#: Actions that OPEN or ADD TO risk. Every entry rail keys off this set, so a
#: new direction added here is automatically subject to all of them — which is
#: the opposite of the failure where `action == "buy"` let shorts bypass the
#: concentration cap because nobody remembered to update the condition.
ENTRY_ACTIONS = ("buy", "short")

#: Actions that REDUCE risk. Exits always run — a rail that trapped you in a
#: losing book while it fell would be the opposite of a risk control.
EXIT_ACTIONS = ("sell", "cover")
```

- [ ] **Step 4: Use it in the rails**

In `src/risk.py`, add near the top with the other imports:

```python
from strategies.base import ENTRY_ACTIONS
```

Change the entry gate from:

```python
    if action == "buy":
```

to:

```python
    if action in ENTRY_ACTIONS:
```

And replace the desync guard:

```python
    if action == "sell" and symbol not in positions:
        raise RiskRejection(f"no position in {symbol} to sell (state desync guard)", rail="desync_sell")
```

with:

```python
    if action == "sell" and symbol not in positions:
        raise RiskRejection(f"no position in {symbol} to sell (state desync guard)", rail="desync_sell")

    # The mirror. Covering a symbol we are not short would not be a no-op — it
    # would submit a BUY and open a long, which is the same class of bug as
    # selling something we do not hold, with the sign flipped.
    if action == "cover" and symbol not in positions:
        raise RiskRejection(f"no short position in {symbol} to cover "
                            f"(state desync guard)", rail="desync_cover")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v`
Expected: 6 passed.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass. `ENTRY_ACTIONS` contains `"buy"`, so long-only behaviour is unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/strategies/base.py src/risk.py tests/test_short_rails.py
git commit -m "Shorts are entries, covers are exits: one action vocabulary for the rails"
```

---

### Task 3: A directional net-exposure band

**Files:**
- Modify: `src/risk.py` (new helper + check inside the entry block)
- Modify: `config.yaml`
- Test: `tests/test_short_rails.py`

**Why directional:** a naive two-sided band would brick the bot. §48 measured
deployment as low as **4.72%** with the drawdown rail engaged; a `min: 80`
applied to buys would reject every entry and the book could never climb back
into the band. A buy raises net and a short lowers it, so the rule is: **`max`
constrains buys, `min` constrains shorts.** An order that moves net *toward* the
band is never blocked.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_rails.py`:

```python
# ---- the net-exposure band ----

BAND = {"net_exposure_pct": {"min": 80, "max": 120}}


def test_a_short_that_would_push_net_below_the_floor_is_refused():
    positions = {"AAPL": {"market_value": 85_000.0}}      # net 85%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 100, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # -10% -> net 75%
    assert e.value.rail == "net_exposure"


def test_a_smaller_short_inside_the_band_is_allowed():
    """The paired half — same book, same direction, smaller size."""
    positions = {"AAPL": {"market_value": 85_000.0}}
    risk.pure_checks("short", "TSLA", 10, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))                         # -1% -> net 84%


def test_a_buy_that_would_push_net_above_the_ceiling_is_refused():
    positions = {"AAPL": {"market_value": 118_000.0}}      # net 118%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "MSFT", 50, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # +5% -> net 123%
    assert e.value.rail == "net_exposure"


def test_a_buy_is_never_blocked_by_the_FLOOR():
    """THE LOAD-BEARING CASE. §48 measured deployment at 4.72%. If the floor
    applied to buys, a bot below the band could never trade its way back in —
    the rail would be an outage, and it would look like a working rail."""
    risk.pure_checks("buy", "MSFT", 1, 100.0, ACCOUNT, {}, _cfg(**BAND))


def test_a_cover_is_never_blocked_by_the_band():
    """Exits always run."""
    positions = {"TSLA": {"market_value": -50_000.0}}
    risk.pure_checks("cover", "TSLA", 100, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))


def test_the_band_is_off_when_unconfigured():
    """Absent config must behave exactly as before, or this rail silently
    changes every existing gate result."""
    positions = {"AAPL": {"market_value": 500_000.0}}
    risk.pure_checks("buy", "MSFT", 1, 100.0, ACCOUNT, positions, _cfg())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "net or band or FLOOR"`
Expected: FAIL — `DID NOT RAISE` on the two rejection cases.

- [ ] **Step 3: Add the helper**

In `src/risk.py`, above `pure_checks`:

```python
def net_exposure_pct(positions: dict, equity: float) -> float:
    """Signed exposure as a percentage of equity: longs positive, shorts
    negative. Distinct from GROSS, which sums magnitudes — a 130/30 book is
    160% gross and 100% net, and the two rails answer different questions."""
    if not equity:
        return 0.0
    return sum(p.get("market_value", 0.0)
               for p in positions.values()) / equity * 100
```

- [ ] **Step 4: Add the check**

In `src/risk.py`, inside `pure_checks`, at the end of the `if action in ENTRY_ACTIONS:` block:

```python
        # NET exposure band (130/30). DIRECTIONAL ON PURPOSE: `max` constrains
        # buys, `min` constrains shorts. A two-sided band would be an outage —
        # §48 measured deployment at 4.72% with the drawdown rail engaged, and
        # a floor applied to buys would reject every entry, leaving a book
        # below the band permanently unable to climb back into it.
        #
        # An order that moves net TOWARD the band is never blocked.
        band = r.get("net_exposure_pct") or {}
        if band:
            net = net_exposure_pct(positions, account["equity"])
            delta_pct = order_value / account["equity"] * 100
            projected = net + (delta_pct if action == "buy" else -delta_pct)
            hi, lo = band.get("max"), band.get("min")
            if action == "buy" and hi is not None and projected > hi:
                raise RiskRejection(
                    f"net exposure band: buying would take net to "
                    f"{projected:.1f}% (ceiling {hi}%)", rail="net_exposure")
            if action == "short" and lo is not None and projected < lo:
                raise RiskRejection(
                    f"net exposure band: shorting would take net to "
                    f"{projected:.1f}% (floor {lo}%)", rail="net_exposure")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v`
Expected: 12 passed.

- [ ] **Step 6: Document it in config, DISABLED**

In `config.yaml`, inside the `risk:` block, add:

```yaml
  # NET exposure band for the 130/30 book (Phase 1, 2026-08-04). COMMENTED OUT
  # until a strategy actually shorts — an unconfigured band is off, and turning
  # it on before there are shorts would only ever constrain buys.
  #
  # DIRECTIONAL: `max` constrains buys, `min` constrains shorts. Never both on
  # one order. §48 measured deployment at 4.72% with the drawdown rail engaged,
  # so a floor applied to buys would reject every entry and the book could not
  # climb back into its own band.
  #
  # net_exposure_pct:
  #   min: 80      # floor on net — stops the short leg pulling the book neutral
  #   max: 120     # ceiling on net — stops the long leg drifting past 130/30
```

- [ ] **Step 7: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass — the band is unconfigured, so it is inert.

```bash
git add src/risk.py config.yaml tests/test_short_rails.py
git commit -m "Net-exposure band: directional, so it can never become an outage"
```

---

### Task 4: Brackets work on both sides

**Files:**
- Modify: `src/broker.py:141-169` (`bracket_market_order`)
- Test: `tests/test_short_rails.py`

**Why this is the most important task in the phase:** a short's loss is
unbounded. `bracket_market_order` is hardcoded `side=OrderSide.BUY`, so a short
routed through it today would open a LONG; routed around it, it would carry no
stop at all.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_rails.py`:

```python
# ---- brackets on the short side ----

class _FakeTrading:
    """Captures the request instead of sending it. No network, ever."""

    def __init__(self):
        self.requests = []

    def submit_order(self, req):
        self.requests.append(req)
        return type("O", (), {"id": "order-1", "status": "accepted",
                              "legs": []})()


def _broker_with(fake):
    import broker as broker_mod
    b = object.__new__(broker_mod.Broker)      # no __init__, no credentials
    b.trading = fake
    return b


def test_a_short_bracket_submits_a_SELL_not_a_BUY():
    """The hardcoded side. A short routed through this today opens a LONG."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order(
        "TSLA", 10, stop_price=110.0, side="short")
    assert fake.requests[0].side == OrderSide.SELL


def test_a_long_bracket_still_submits_a_BUY():
    """The paired half — the default must not move."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order("AAPL", 10, stop_price=90.0)
    assert fake.requests[0].side == OrderSide.BUY


def test_a_short_bracket_refuses_a_stop_BELOW_the_entry():
    """Geometry, and it is not cosmetic. A short's stop sits ABOVE entry; a
    stop below it can never trigger, so the position would be unprotected while
    appearing bracketed — the worst of both."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="above"):
        _broker_with(fake).bracket_market_order(
            "TSLA", 10, stop_price=90.0, side="short", entry_price=100.0)
    assert fake.requests == []


def test_a_long_bracket_refuses_a_stop_ABOVE_the_entry():
    """The mirror."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="below"):
        _broker_with(fake).bracket_market_order(
            "AAPL", 10, stop_price=110.0, side="buy", entry_price=100.0)
    assert fake.requests == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "bracket"`
Expected: FAIL — `TypeError: unexpected keyword argument 'side'`.

- [ ] **Step 3: Generalise the method**

In `src/broker.py`, replace `bracket_market_order` with:

```python
    def bracket_market_order(self, symbol: str, qty: float, stop_price: float,
                             take_profit_price: float | None = None,
                             client_order_id: str | None = None,
                             side: str = "buy",
                             entry_price: float | None = None) -> dict:
        """Market order with protective legs, GTC so they survive across days.

        Alpaca requires BOTH legs for OrderClass.BRACKET; with no take-profit
        we submit OTO (stop-loss leg only) instead.

        `side` defaults to "buy" so every existing call site is unchanged. A
        short's stop sits ABOVE its entry — the geometry inverts with the
        direction, and a stop on the wrong side can never trigger, leaving the
        position unprotected while looking bracketed. `entry_price` is optional
        only because callers that cannot supply it lose the check, not the
        order; when it IS supplied the geometry is enforced.
        """
        is_short = side in ("short", "sell")
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "bracket"`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass — `side` defaults to `"buy"`.

```bash
git add src/broker.py tests/test_short_rails.py
git commit -m "Brackets work short: correct side, and the stop must be on the right side of entry"
```

---

### Task 5: The account reports whether it can short at all

**Files:**
- Modify: `src/broker.py:56-63` (`account`)
- Test: `tests/test_short_rails.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_short_rails.py`:

```python
# ---- can this account even short? ----

def test_the_account_reports_shorting_capability():
    """Without this, a broker that forbids shorting fails every short order
    one at a time, at submission, forever — and the book quietly reverts to
    long-only with nothing saying so."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "200000"; shorting_enabled = True; multiplier = "2"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["shorting_enabled"] is True
    assert a["multiplier"] == 2.0


def test_a_broker_that_omits_the_fields_reads_as_NOT_shortable():
    """Fail closed. An unknown capability must not read as permission."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["shorting_enabled"] is False
    assert a["multiplier"] == 1.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "shorting_capability or NOT_shortable"`
Expected: FAIL — `KeyError: 'shorting_enabled'`.

- [ ] **Step 3: Extend `account()`**

In `src/broker.py`, replace the `account` return with:

```python
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
            "multiplier": float(getattr(a, "multiplier", 1) or 1),
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "shorting_capability or NOT_shortable"`
Expected: 2 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`

```bash
git add src/broker.py tests/test_short_rails.py
git commit -m "Account reports shorting_enabled and multiplier, failing closed"
```

---

### Task 6: Preflight refuses a shorting bot on a non-shorting account

**Files:**
- Modify: `src/preflight.py` (`run`)
- Test: `tests/test_short_rails.py`

**Note the polarity:** preflight FAILS SAFE, per its own module docstring — a
misconfigured system must not trade at all. This is exactly that case.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_short_rails.py`:

```python
# ---- preflight ----

def _short_cfg(enabled):
    return {"risk": {"risk_per_trade_pct": 1.0, "max_position_pct": 10.0,
                     "daily_loss_limit_pct": 5.0, "min_holding_days": 1,
                     "max_order_value_usd": 0, "max_trades_per_day": 0,
                     "max_open_positions": 0},
            "strategies": {"xsmom": {"enabled": enabled,
                                     "short_bottom_fraction": 0.25}}}


def test_preflight_refuses_shorts_when_the_broker_forbids_them():
    import preflight
    fails = preflight.run(_short_cfg(True),
                          account={"shorting_enabled": False})
    assert any("shorting" in f for f in fails)


def test_preflight_is_silent_when_the_broker_allows_shorts():
    import preflight
    fails = preflight.run(_short_cfg(True),
                          account={"shorting_enabled": True})
    assert not any("shorting" in f for f in fails)


def test_preflight_is_silent_when_no_strategy_shorts():
    """The paired half that matters most right now: Phase 1 ships with shorts
    OFF, so this check must be completely inert until Phase 2."""
    import preflight
    fails = preflight.run(_short_cfg(False),
                          account={"shorting_enabled": False})
    assert not any("shorting" in f for f in fails)


def test_preflight_still_works_with_no_account_argument():
    """Every existing caller passes cfg only. The signature must stay
    backward-compatible or the cycle aborts at main.py's preflight call."""
    import preflight
    fails = preflight.run(_short_cfg(False))
    assert not any("shorting" in f for f in fails)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "preflight"`
Expected: FAIL — `run() got an unexpected keyword argument 'account'`.

- [ ] **Step 3: Extend `run`**

In `src/preflight.py`, change the signature and add the check at the end of `run`, immediately before `return fails`:

```python
def run(cfg: dict, account: dict | None = None) -> list[str]:
    """All failures found (empty list = clear to trade).

    `account` is optional so every existing caller keeps working. When it IS
    supplied, one more question can be asked that config alone cannot answer:
    is this broker account actually permitted to do what the config tells the
    bot to do?
    """
```

```python
    # A bot configured to short on an account that cannot short does not fail
    # loudly — it fails one order at a time, at submission, and quietly runs
    # long-only. Preflight's polarity is FAIL SAFE, so this is a refusal.
    if account is not None and not account.get("shorting_enabled", False):
        shorting = [name for name, s in (cfg.get("strategies") or {}).items()
                    if s.get("enabled") and s.get("short_bottom_fraction")]
        if shorting:
            fails.append(
                f"shorting is disabled on this brokerage account, but "
                f"{', '.join(sorted(shorting))} is configured to short "
                f"(short_bottom_fraction). Enable margin/shorting at the "
                f"broker, or unset short_bottom_fraction.")

    return fails
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "preflight"`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`

```bash
git add src/preflight.py tests/test_short_rails.py
git commit -m "Preflight refuses a shorting config on a non-shorting account"
```

---

### Task 7: Pin invariant #2 on the short side

**Files:**
- Test only: `tests/test_short_rails.py`

**No code change.** `src/llm.py:143` already clamps
`verdict["scale"] = min(max(float(verdict.get("scale", 1.0)), 0.0), 1.0)`, and
because quantity stays unsigned, `int(full_qty * scale)` shrinks a short toward
zero exactly as it shrinks a long. This task pins that so a future signed-qty
refactor cannot silently invert it.

- [ ] **Step 1: Write the tests**

Append to `tests/test_short_rails.py`:

```python
# ---- invariant #2 holds on the short side ----

@pytest.mark.parametrize("raw,expected", [
    (1.5, 1.0),      # the judge cannot enlarge
    (-0.5, 0.0),     # nor flip the sign
    (0.4, 0.4),      # a genuine downsize survives
])
def test_the_judge_scale_is_clamped_into_zero_to_one(raw, expected):
    """Invariant #2: the LLM may only VETO or DOWNSIZE, never enlarge or
    invent. A scale above 1.0 would enlarge; a negative one would REVERSE the
    trade, turning a downsize into an opposite-direction entry."""
    import llm
    v = llm._clamp_scale({"verdict": "downsize", "scale": raw})
    assert v["scale"] == expected


def test_downsizing_a_short_moves_it_toward_zero_not_further_negative():
    """The structural guarantee behind the whole phase: quantity is UNSIGNED
    and direction lives in `action`, so scaling shrinks magnitude regardless of
    side. If anyone ever makes qty signed, this test is what fails."""
    full_qty = 100
    for scale in (0.0, 0.25, 1.0):
        assert 0 <= int(full_qty * scale) <= full_qty
```

- [ ] **Step 2: Extract the clamp so it is testable**

In `src/llm.py`, the clamp currently sits inline at line 143. Extract it, and
call it where the inline version was:

```python
def _clamp_scale(verdict: dict) -> dict:
    """Invariant #2, in one place. The LLM may only VETO or DOWNSIZE — never
    enlarge, and never reverse. Extracted from an inline expression so it can
    be tested directly; a rule this important should not be reachable only
    through a live model call."""
    verdict["scale"] = min(max(float(verdict.get("scale", 1.0)), 0.0), 1.0)
    return verdict
```

- [ ] **Step 3: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_short_rails.py -v -k "clamp or downsizing"`
Expected: 4 passed.

- [ ] **Step 4: Run the full suite and commit**

Run: `.venv/bin/python -m pytest tests/ -q`

```bash
git add src/llm.py tests/test_short_rails.py
git commit -m "Pin invariant #2 on the short side; extract the scale clamp"
```

---

### Task 8: Prove the rails, then open the branch

**Files:** none modified — verification only.

- [ ] **Step 1: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, count higher than the pre-Phase-1 baseline by ~21.

- [ ] **Step 2: Mutation-test every rail this phase added**

Each must be CAUGHT. Uses the driver the `bot-prove-it` skill ships.

```bash
M=.claude/skills/bot-prove-it/scripts/mutate.py

.venv/bin/python $M --file src/risk.py \
  --find 'gross = sum(abs(p.get("market_value", 0.0))' \
  --replace 'gross = sum((p.get("market_value", 0.0))' \
  --expect tests/test_short_rails.py --suite tests/test_short_rails.py

.venv/bin/python $M --file src/risk.py \
  --find 'if action == "buy" and hi is not None and projected > hi:' \
  --replace 'if action == "buy" and hi is not None and projected > hi + 1e9:' \
  --expect tests/test_short_rails.py --suite tests/test_short_rails.py

.venv/bin/python $M --file src/broker.py \
  --find 'side=OrderSide.SELL if is_short else OrderSide.BUY,' \
  --replace 'side=OrderSide.BUY,' \
  --expect tests/test_short_rails.py --suite tests/test_short_rails.py
```

Expected: `CAUGHT` and `restore verified byte-identical` for all three.

- [ ] **Step 3: Confirm live behaviour is unchanged**

Run: `git diff main --stat -- config.yaml`
Expected: comment-only change. The net band ships commented out, no strategy
emits a short, and `ENTRY_ACTIONS` contains `"buy"` — so the running bot is
byte-identical.

- [ ] **Step 4: Open the PR**

```bash
git push -u origin phase-1-short-rails
gh pr create --title "Phase 1: make shorts survivable (nothing shorts yet)" \
  --body "Implements Phase 1 of docs/superpowers/specs/2026-08-04-130-30-long-short-design.md. No strategy emits a short; live behaviour is byte-identical. Three mutations run and caught."
```

---

## Self-review

**Spec coverage.** Hazard 1 (bracket side) → Task 4. Hazard 2 (gross `abs`) →
Task 1. Hazard 3 (invariant #2) → Task 7, **downgraded to a test after finding
`llm.py:143` already clamps**. Net band → Task 3. Preflight → Tasks 5–6. Signal
contract → Task 2.

**Deliberately deferred to Phase 2**, and the spec's Phase 1 wording said
"signed position model through risk and backtest": `backtest.py` is **not**
touched here. It calls `pure_checks` and never passes `"short"`, so there is
nothing for it to do until a strategy emits one. Changing it now would be
speculative work with no test that could fail — YAGNI.

**Type consistency.** `ENTRY_ACTIONS` / `EXIT_ACTIONS` defined in Task 2 and
used in Tasks 2–3. `net_exposure_pct()` defined and used in Task 3.
`_clamp_scale()` defined and used in Task 7. Rail keys `regime_exposure`,
`position_cap`, `desync_cover`, `net_exposure` all asserted against the strings
raised.

**Placeholder scan:** none. Every step carries the code or command it needs.
