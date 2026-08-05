"""Phase 2 Group A: the sizing and bracket arithmetic measures magnitude,
not "long minus short". Phase 1 made the risk RAILS short-aware; this pins
the PATH a short order actually travels — size_order, portfolio_heat, and
bracket_prices — before anything in this repo emits a "short" or "cover"
signal.

Nothing shorts yet (ENTRY_ACTIONS includes "short" in strategies/base.py, but
no strategy emits it), so every test here is a rehearsal: it proves the
arithmetic is correct for a direction nothing has exercised in production,
and — the load-bearing half — that the LONG path computes byte-identically
to before. A short-side assertion with no long-side twin proves nothing:
without the twin you cannot tell "fixed the short" from "broke the long and
got lucky on this one number".

Three failures, each a variant of the same root cause (arithmetic written as
`entry - stop`, which is only ever a magnitude when the stop is below entry):

  1. size_order: a short's stop sits ABOVE entry, so the un-fixed
     `(price - stop_price) / price` is negative, `dollars` goes negative,
     and `qty` floors to 0 or less — a risk-sized short silently becomes an
     unsized one, no error, no log.
  2. portfolio_heat: `max(entry - stop, 0.0)` clamps a short's positive
     risk to exactly 0.0, because for a short `entry - stop` is negative
     before the clamp — indistinguishable from the nonsensical-long case
     the clamp exists to catch. The heat cap goes blind to every short in
     the book.
  3. bracket_prices: the stop is unconditionally placed below entry and the
     take-profit above it, so a short bracket has both legs on the wrong
     side — and Phase 1's `broker.bracket_market_order` now REFUSES a short
     whose stop is not above entry, so today this function cannot produce a
     placeable short bracket at all.

Boundary pairs throughout, in the style of tests/test_short_rails.py: each
short-side assertion sits beside its long-side twin, differing only in the
direction being sized or bracketed.
"""
import risk


ACCOUNT = {"equity": 100_000.0, "cash": 100_000.0,
           "last_equity": 100_000.0, "buying_power": 100_000.0}


def _cfg(**risk_overrides):
    base = {
        "risk_per_trade_pct": 1.0,
        "max_position_pct": 100.0,
        "max_order_value_usd": 0,  # 0 disables the per-order cap
        "brackets": {"enabled": True, "stop_atr_mult": 2.0,
                     "take_profit_atr_mult": 3.0},
    }
    base.update(risk_overrides)
    return {"risk": base}


# ---------------------------------------------------------------------------
# size_order: risk-based sizing must measure the entry-to-stop DISTANCE, not
# assume the stop is below price.
# ---------------------------------------------------------------------------

def test_risk_sized_long_is_unchanged_by_the_direction_parameter():
    """The long path with no direction argument (every existing caller) must
    compute exactly what it computed before: 1% of equity risked over a $10
    entry-to-stop distance on a $100 stock -> $1000/$10 = 100 shares."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom", stop_price=90.0)
    assert qty == 100


def test_risk_sized_short_uses_the_same_distance_magnitude():
    """The short twin: same $100 entry, same $10 distance, but the stop sits
    ABOVE entry at $110. Risk sizing means 'risk this equity fraction over
    the entry-to-stop distance' — the distance is the same $10 magnitude, so
    the answer must be the same 100 shares, not 0."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom",
                          stop_price=110.0, direction="short")
    assert qty == 100


def test_risk_sized_short_without_the_fix_would_silently_return_zero():
    """Pins the FAILURE MODE by name, not just the correct answer: a
    negative stop_frac floors to qty <= 0 with no exception and no log —
    this is the assertion that would catch a regression back to
    `(price - stop_price) / price` un-guarded by direction."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom",
                          stop_price=110.0, direction="short")
    assert qty > 0


def test_long_stop_on_the_wrong_side_falls_back_to_notional_not_risk_sizing():
    """A 'buy' whose stop is ABOVE price is nonsensical for a long — this is
    unchanged behaviour: risk sizing does not activate, so sizing falls back
    to fixed-fractional notional (1% of 100k = $1000 budget / $100 price =
    10 shares, clamped only by the caps below)."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0},
               max_position_pct=100.0)
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom", stop_price=110.0)
    # notional: equity * risk_per_trade_pct / 100 / price = 100_000*0.01/100/100 = 10
    assert qty == 10


def test_short_stop_on_the_wrong_side_falls_back_to_notional_not_risk_sizing():
    """The short twin: a stop BELOW price is nonsensical for a short (it can
    never trigger, per broker.bracket_market_order's own geometry check) —
    risk sizing must not activate here either, so it falls back to the same
    notional sizing as the long case above."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0},
               max_position_pct=100.0)
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom",
                          stop_price=90.0, direction="short")
    assert qty == 10


def test_long_stop_equal_to_price_is_a_degenerate_zero_distance():
    """Zero entry-to-stop distance is undefined risk, not infinite size —
    must not activate risk sizing (which would divide by zero) and must
    fall back to notional, same as the missing-stop case."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom", stop_price=100.0)
    assert qty == 10


def test_short_stop_equal_to_price_is_a_degenerate_zero_distance():
    """The short twin of the zero-distance case."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom",
                          stop_price=100.0, direction="short")
    assert qty == 10


def test_long_with_no_stop_price_uses_notional_sizing_unaffected_by_direction():
    """No stop known at all (brackets off, or caller has none) -> notional,
    exactly as before the direction parameter existed."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom")
    assert qty == 10


def test_short_with_no_stop_price_uses_notional_sizing_unaffected_by_direction():
    """The short twin: direction alone, with no stop, must not do anything
    surprising — still notional."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 100.0, cfg, strategy="tsmom", direction="short")
    assert qty == 10


def test_zero_price_returns_zero_for_a_long_regardless_of_risk_sizing():
    """Existing fail-safe (a 0/negative price is bad data, not an
    opportunity) must survive the direction parameter unchanged."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    assert risk.size_order(ACCOUNT, 0.0, cfg, strategy="tsmom", stop_price=-10.0) == 0


def test_zero_price_returns_zero_for_a_short_too():
    """The short twin: direction must not create a bypass of the price<=0
    guard, which runs before any direction-specific arithmetic."""
    cfg = _cfg(risk_sizing={"enabled": True, "risk_pct": 1.0})
    qty = risk.size_order(ACCOUNT, 0.0, cfg, strategy="tsmom", stop_price=10.0,
                          direction="short")
    assert qty == 0


# ---------------------------------------------------------------------------
# portfolio_heat: must count a short's dollar risk instead of clamping it to
# zero, while continuing to clamp a NONSENSICAL long (stop above entry) to
# zero exactly as before.
# ---------------------------------------------------------------------------

def test_a_normal_long_contributes_its_entry_to_stop_distance_to_heat():
    """Unchanged long behaviour: qty x (entry - stop). No 'action' key is the
    common case in today's records (main.py has only ever written 'buy'), so
    the default path must match that silently."""
    open_trades = {"t1": {"qty": 100, "entry_price": 100.0,
                          "order": {"stop_price": 90.0}}}
    heat = risk.portfolio_heat(open_trades, _cfg(), 100_000.0)
    assert heat == 100 * (100.0 - 90.0)


def test_a_normal_short_contributes_its_stop_to_entry_distance_to_heat():
    """The short twin: a short's stop sits ABOVE entry, so its magnitude is
    (stop - entry), not (entry - stop). This is the case the un-fixed
    `max(entry - stop, 0.0)` clamped to exactly zero, making every short
    invisible to the heat cap."""
    open_trades = {"t1": {"qty": 100, "entry_price": 100.0, "action": "short",
                          "order": {"stop_price": 110.0}}}
    heat = risk.portfolio_heat(open_trades, _cfg(), 100_000.0)
    assert heat == 100 * (110.0 - 100.0)


def test_a_nonsensical_long_with_stop_above_entry_still_clamps_to_zero():
    """The clamp `max(..., 0.0)` was doing double duty before this fix:
    catching both a short (now handled above) and a long whose recorded
    stop is above entry, which should never happen but must not go NEGATIVE
    and understate the book's real risk if it somehow does."""
    open_trades = {"t1": {"qty": 100, "entry_price": 100.0,
                          "order": {"stop_price": 110.0}}}
    heat = risk.portfolio_heat(open_trades, _cfg(), 100_000.0)
    assert heat == 0.0


def test_a_nonsensical_short_with_stop_below_entry_also_clamps_to_zero():
    """The short twin of the clamp above: a short whose recorded stop is
    BELOW entry is just as nonsensical (that stop could never trigger) and
    must clamp to zero rather than go negative."""
    open_trades = {"t1": {"qty": 100, "entry_price": 100.0, "action": "short",
                          "order": {"stop_price": 90.0}}}
    heat = risk.portfolio_heat(open_trades, _cfg(), 100_000.0)
    assert heat == 0.0


def test_a_position_missing_action_is_treated_as_a_long_not_a_short():
    """Every record main.py has ever written carries 'action': 'buy' (or, in
    older records, no key at all) — the default must resolve to the LONG
    formula, not silently start reading legacy records as shorts."""
    open_trades = {"t1": {"qty": 50, "entry_price": 50.0,
                          "order": {"stop_price": 45.0}}}
    heat = risk.portfolio_heat(open_trades, _cfg(), 100_000.0)
    assert heat == 50 * (50.0 - 45.0)


def test_a_position_with_no_recorded_stop_falls_back_to_per_trade_heat_long():
    """Unchanged fallback: a position with qty but no stop contributes the
    standard per-trade risk fraction of equity, regardless of direction."""
    cfg = _cfg(risk_per_trade_pct=2.0)
    open_trades = {"t1": {"qty": 10, "entry_price": 100.0, "order": {}}}
    heat = risk.portfolio_heat(open_trades, cfg, 100_000.0)
    assert heat == 100_000.0 * 2.0 / 100


def test_a_short_with_no_recorded_stop_falls_back_to_per_trade_heat_too():
    """The short twin: the per-trade fallback does not look at direction at
    all today, and this fix must not change that — a short missing its stop
    is exactly as unmeasurable as a long missing its stop."""
    cfg = _cfg(risk_per_trade_pct=2.0)
    open_trades = {"t1": {"qty": 10, "entry_price": 100.0, "action": "short",
                          "order": {}}}
    heat = risk.portfolio_heat(open_trades, cfg, 100_000.0)
    assert heat == 100_000.0 * 2.0 / 100


# ---------------------------------------------------------------------------
# bracket_prices: a short bracket needs the stop ABOVE entry and the
# take-profit BELOW it — the mirror image of a long bracket, not the same
# formula run on both sides.
# ---------------------------------------------------------------------------

def test_long_bracket_prices_are_unchanged_by_the_direction_parameter():
    """No direction argument (every existing caller) must compute exactly
    what it computed before: stop = entry - mult*ATR, tp = entry + mult*ATR."""
    cfg = _cfg()
    stop, tp = risk.bracket_prices(100.0, 5.0, cfg)
    assert (stop, tp) == (90.0, 115.0)


def test_short_bracket_stop_is_above_entry_and_take_profit_is_below():
    """The short twin: same entry, same ATR, but the geometry inverts — the
    stop sits ABOVE entry (a short loses money as price rises) and the
    take-profit sits BELOW it. This is exactly what
    broker.bracket_market_order's geometry check now requires, and what the
    un-fixed function could never produce for a short."""
    cfg = _cfg()
    stop, tp = risk.bracket_prices(100.0, 5.0, cfg, direction="short")
    assert (stop, tp) == (110.0, 85.0)


def test_long_bracket_returns_none_when_the_stop_would_be_non_positive():
    """Existing guard: a stop that would land at or below zero degrades to a
    plain market order rather than placing a nonsensical protective stop."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["stop_atr_mult"] = 30.0  # 100 - 30*5 = -50
    assert risk.bracket_prices(100.0, 5.0, cfg) is None


def test_short_bracket_returns_none_when_the_take_profit_would_be_non_positive():
    """The short twin of the guard above: for a short, the STOP is always
    positive (entry + a positive multiple of ATR), but the TAKE-PROFIT can
    go non-positive (entry - a large multiple of ATR) — so the guard must
    protect the take-profit on this side, not the stop."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["take_profit_atr_mult"] = 30.0  # 100 - 30*5 = -50
    assert risk.bracket_prices(100.0, 5.0, cfg, direction="short") is None


def test_short_bracket_stop_stays_positive_even_with_a_large_multiplier():
    """The other half of the asymmetry: a short's stop = entry + mult*ATR
    only grows with a larger multiplier, so the same 30x multiplier that
    breaks a long's stop must NOT break a short's — proves the guard was
    moved to the take-profit, not merely duplicated onto the stop too."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["stop_atr_mult"] = 30.0
    cfg["risk"]["brackets"]["take_profit_atr_mult"] = 0  # disabled, no TP to check
    stop, tp = risk.bracket_prices(100.0, 5.0, cfg, direction="short")
    assert stop == 100.0 + 30.0 * 5.0
    assert tp is None


def test_long_bracket_omits_take_profit_when_its_multiplier_is_zero():
    """Unchanged: take-profit is optional and stays None when disabled."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["take_profit_atr_mult"] = 0
    stop, tp = risk.bracket_prices(100.0, 5.0, cfg)
    assert stop == 90.0
    assert tp is None


def test_short_bracket_omits_take_profit_when_its_multiplier_is_zero_too():
    """The short twin: same optionality, same None, direction unrelated."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["take_profit_atr_mult"] = 0
    stop, tp = risk.bracket_prices(100.0, 5.0, cfg, direction="short")
    assert stop == 110.0
    assert tp is None


def test_long_bracket_is_none_when_brackets_are_disabled():
    """Unchanged: brackets off means no bracket, regardless of direction."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["enabled"] = False
    assert risk.bracket_prices(100.0, 5.0, cfg) is None


def test_short_bracket_is_none_when_brackets_are_disabled_too():
    """The short twin of the disabled-brackets case."""
    cfg = _cfg()
    cfg["risk"]["brackets"]["enabled"] = False
    assert risk.bracket_prices(100.0, 5.0, cfg, direction="short") is None


def test_long_bracket_is_none_when_atr_is_missing():
    """Unchanged: no ATR, no bracket, regardless of direction."""
    cfg = _cfg()
    assert risk.bracket_prices(100.0, None, cfg) is None


def test_short_bracket_is_none_when_atr_is_missing_too():
    """The short twin of the missing-ATR case."""
    cfg = _cfg()
    assert risk.bracket_prices(100.0, None, cfg, direction="short") is None


def test_long_bracket_is_none_when_atr_is_zero_or_negative():
    """Unchanged: non-positive ATR is treated as unavailable, regardless of
    direction — a zero or negative ATR cannot describe a real distance."""
    cfg = _cfg()
    assert risk.bracket_prices(100.0, 0.0, cfg) is None
    assert risk.bracket_prices(100.0, -1.0, cfg) is None


def test_short_bracket_is_none_when_atr_is_zero_or_negative_too():
    """The short twin of the non-positive-ATR case."""
    cfg = _cfg()
    assert risk.bracket_prices(100.0, 0.0, cfg, direction="short") is None
    assert risk.bracket_prices(100.0, -1.0, cfg, direction="short") is None
