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

Groups B and C were added to this same file as the short path grew, each
under its own banner: Group B covers broker.market_order's side mapping, and
Group C covers src/main.py's action vocabulary and — the one defect in this
phase that no pure-function test could have caught — the in-cycle position
view that Phase 1's exposure rails read.
"""
import pytest

import risk
from test_short_rails import _FakeTrading, _broker_with


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


# ---------------------------------------------------------------------------
# pre_trade_checks: the heat cap's own `new_risk` term — the CANDIDATE
# order's contribution, on top of portfolio_heat's tally of what is already
# open — must price a short's stop distance too. Group A fixed
# portfolio_heat; it did not touch this second site, which computed the same
# `max(price - candidate_stop, 0.0)` magnitude for the order under review
# right now. A short's candidate stop sits ABOVE price, so the un-fixed
# formula clamped the very order the cap is deciding on to $0.00 — the cap
# was blind to its own decision.
# ---------------------------------------------------------------------------

def _entries_cfg(**risk_overrides):
    """pre_trade_checks reaches keys _cfg() above never had to set: entries
    read `max_open_positions` by direct key access (not `.get`), and the
    heat cap only binds when `max_portfolio_heat_pct` is set."""
    overrides = {"max_open_positions": 5, "max_trades_per_day": 0,
                 "max_portfolio_heat_pct": 0.3}
    overrides.update(risk_overrides)
    return _cfg(**overrides)


def test_a_short_candidates_own_stop_risk_is_counted_not_zeroed(
        tmp_path, monkeypatch):
    """An empty book means portfolio_heat == 0, so the cap decision rests
    entirely on THIS candidate's own risk: 50 shares x |27.33 - 20.00| =
    $366.50 of a $100k book breaches a 0.3% ($300) cap. The un-fixed clamp
    read this order's own risk as $0.00 and let it straight through."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg()
    with pytest.raises(risk.RiskRejection) as exc_info:
        risk.pre_trade_checks("short", "AAPL", 50, 20.00, ACCOUNT, {}, cfg,
                              open_trades={}, candidate_stop=27.33)
    assert exc_info.value.rail == "heat"


def test_a_long_candidates_own_stop_risk_is_still_counted_the_same_way(
        tmp_path, monkeypatch):
    """The long twin: this half of the formula was already correct, and
    fixing the short side must not disturb it — same magnitude, same cap,
    same refusal."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg()
    with pytest.raises(risk.RiskRejection) as exc_info:
        risk.pre_trade_checks("buy", "AAPL", 50, 27.33, ACCOUNT, {}, cfg,
                              open_trades={}, candidate_stop=20.00)
    assert exc_info.value.rail == "heat"


def test_a_short_candidate_under_the_cap_is_not_refused(tmp_path, monkeypatch):
    """The other boundary: raise the cap clear of the same $366.50 and the
    short must be admitted — proves the rail fires on the NUMBER, not on
    'a short candidate exists'."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg(max_portfolio_heat_pct=5.0)
    risk.pre_trade_checks("short", "AAPL", 50, 20.00, ACCOUNT, {}, cfg,
                          open_trades={}, candidate_stop=27.33)  # no raise


def test_a_long_candidate_under_the_cap_is_still_not_refused(
        tmp_path, monkeypatch):
    """The twin."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg(max_portfolio_heat_pct=5.0)
    risk.pre_trade_checks("buy", "AAPL", 50, 27.33, ACCOUNT, {}, cfg,
                          open_trades={}, candidate_stop=20.00)  # no raise


def test_a_shorts_nonsensical_stop_below_price_still_clamps_new_risk_to_zero(
        tmp_path, monkeypatch):
    """The `max(..., 0.0)` clamp must survive on the short side exactly as it
    always has on the long side: a short candidate whose stop is BELOW price
    is nonsensical (that stop could never trigger) and must contribute $0 —
    not a negative number that would UNDERSTATE the cap check."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg(max_portfolio_heat_pct=0.0001)  # any real risk would breach
    risk.pre_trade_checks("short", "AAPL", 50, 27.33, ACCOUNT, {}, cfg,
                          open_trades={}, candidate_stop=20.00)  # no raise: clamped


def test_a_longs_nonsensical_stop_above_price_still_clamps_new_risk_to_zero(
        tmp_path, monkeypatch):
    """The twin: unchanged behaviour for a long whose recorded stop sits
    above price — this is the case the `max(..., 0.0)` clamp originally
    existed to catch, before a short ever needed the same protection."""
    monkeypatch.chdir(tmp_path)
    cfg = _entries_cfg(max_portfolio_heat_pct=0.0001)
    risk.pre_trade_checks("buy", "AAPL", 50, 20.00, ACCOUNT, {}, cfg,
                          open_trades={}, candidate_stop=27.33)  # no raise: clamped


# ---------------------------------------------------------------------------
# broker.market_order: the last untreated case of "anything that isn't buy is
# a sell". Phase 1 removed that fallthrough from bracket_market_order; this
# section pins the same fix here. main.py routes every non-bracket action
# (buy-without-a-bracket, sell, short, cover) through this one function as
# `broker.market_order(symbol, qty, sig.action, ...)`, so an un-fixed
# fallthrough sends "cover" to SELL — doubling a losing short instead of
# closing it — with nothing raising and nothing logging.
#
# Boundary pairs throughout, reusing tests/test_short_rails.py's `_FakeTrading`
# (captures the submitted request instead of hitting the network) rather than
# inventing a second fake trading client for the same job.
# ---------------------------------------------------------------------------

def test_a_buy_still_submits_a_BUY():
    """The unchanged case — the paired half of the cover test below."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).market_order("AAPL", 10, "buy")
    assert fake.requests[0].side == OrderSide.BUY


def test_a_cover_submits_a_BUY_not_a_SELL():
    """The defect this group exists to fix: covering a short must submit a
    BUY. Under the old `... if side == "buy" else SELL` fallthrough this sent
    a SELL, which doubles the short instead of closing it — silently, with no
    exception and no log line to catch it."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).market_order("AAPL", 10, "cover")
    assert fake.requests[0].side == OrderSide.BUY


def test_a_sell_still_submits_a_SELL():
    """The unchanged case — the paired half of the short test below."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).market_order("TSLA", 10, "sell")
    assert fake.requests[0].side == OrderSide.SELL


def test_a_short_still_submits_a_SELL():
    """Opening a short was already correct under the old fallthrough — by
    accident, since it fell into the same "not buy" bucket as every other
    unrecognised value. Pinned here so the explicit mapping this group adds
    cannot regress the one case the old code happened to get right."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).market_order("TSLA", 10, "short")
    assert fake.requests[0].side == OrderSide.SELL


def test_an_unrecognized_side_is_refused_before_reaching_the_broker():
    """The other half of the old fallthrough's blast radius: a typo or a
    wrong constant (anything that is not one of the four known actions) must
    raise, not silently submit a SELL. `fake.requests` staying empty proves
    the rejection happened BEFORE `submit_order`, not after."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="oops"):
        _broker_with(fake).market_order("AAPL", 10, "oops")
    assert fake.requests == []


def test_all_four_recognized_sides_are_accepted():
    """The paired half of the rejection test above: every one of the four
    values main.py's action vocabulary can actually send (ENTRY_ACTIONS =
    buy/short, EXIT_ACTIONS = sell/cover) must be accepted, not just the two
    that happen to map to BUY. A fix that narrowed the accepted set instead
    of widening the rejected one would pass the unknown-side test above and
    still be broken."""
    fake = _FakeTrading()
    b = _broker_with(fake)
    for side in ("buy", "sell", "short", "cover"):
        b.market_order("AAPL", 10, side)
    assert len(fake.requests) == 4


# ===========================================================================
# Group C: src/main.py's action vocabulary, and the in-cycle position view.
#
# main.py used `action == "buy"` as a synonym for "this is an entry" and
# `action == "sell"` for "this is an exit" at fifteen sites. Because "buy" is
# in ENTRY_ACTIONS and "sell" is in EXIT_ACTIONS, a CORRECT substitution is a
# no-op by construction — the whole risk in that change is misclassifying a
# site, and no test could have caught it, because nothing emits shorts.
#
# So these tests do the opposite of the sections above: instead of calling a
# pure function, they drive the REAL `main.run_cycle` with a scripted signal
# and observe what it did. Two sites needed more than a substitution and get
# the most attention here:
#
#   * the in-cycle position view (main.py ~1315). A "short" fell into the
#     `else` and POPPED the symbol, so within the same cycle Phase 1's
#     net-exposure band and the down-regime gross cap were blind to it —
#     rails merged days earlier, silently defeated. `_observed_view` below is
#     how these tests see that: the fake's `positions()` hands back the very
#     dict main.py mutates, so the assertion is against the real object the
#     rails would have read, not a reconstruction of it.
#
#   * ledger.open_buys (src/ledger.py). The only path by which the live bot
#     reloads its open book from persisted state; on `== "buy"` a restart
#     dropped every open short.
#
# Boundary pairs throughout: every short/cover assertion has the long/sell
# twin it differs from in exactly one way. Without the twin you cannot tell
# "taught main.py about shorts" from "broke the long path and got lucky".
# ===========================================================================

import copy
import json
from datetime import datetime, timedelta, timezone

import yaml

import main
import strategies
from ledger import Ledger
from strategies.base import Signal

from conftest import make_bars
from test_main_cycle import FakeCycleBroker

# SMA3 crosses above SMA5 on the last bar; last close is $20. Reused from
# tests/test_main_cycle.py so the long path here is the same long path the
# end-to-end cycle tests already pin.
CYCLE_CLOSES = [10] * 6 + [9, 9, 9, 20]
CYCLE_PRICE = 20.0
# ATR(3) over CYCLE_CLOSES: the last three true ranges are 0, 0 and 11.
CYCLE_ATR = 11 / 3


class _ScriptedCycleBroker:
    """Cycle-driving fake, scripted per test.

    Two things it does that tests/test_main_cycle.py's fake does not:

      * `positions()` returns the SAME dict every time, and main.py mutates
        that object in place (src/main.py: `positions = broker.positions()`,
        never a copy). That is how these tests observe the in-cycle position
        view directly rather than re-deriving what it "should" contain — a
        re-derivation would pass whenever it is wrong in the same direction
        as the code.
      * `bracket_market_order` captures its keyword arguments VERBATIM, with
        `**extra` rather than named `side`/`entry_price` parameters. That is
        deliberate: it lets a test assert that a LONG entry sent no `side`
        and no `entry_price` at all, which is a claim a fake with defaulted
        named parameters could not distinguish from "sent the defaults".
    """

    def __init__(self, bars, positions=None):
        self._bars = bars
        self.view = {} if positions is None else positions
        self.submitted = []
        self.bracket_calls = []
        self.canceled = []

    def account(self):
        return {"equity": 100_000.0, "cash": 100_000.0,
                "last_equity": 100_000.0, "buying_power": 100_000.0}

    def positions(self):
        return self.view

    def bars(self, symbol, timeframe, limit):
        return self._bars

    def latest_price(self, symbol):
        return self._bars[-1]["close"]        # no drift; the guard passes

    def last_price(self, symbol):
        return self._bars[-1]["close"]

    def market_order(self, symbol, qty, side, client_order_id=None):
        order = {"id": f"plain-{len(self.submitted)}", "symbol": symbol,
                 "qty": qty, "side": side, "status": "accepted",
                 "client_order_id": client_order_id}
        self.submitted.append(order)
        return order

    def bracket_market_order(self, symbol, qty, stop_price,
                             take_profit_price=None, client_order_id=None,
                             **extra):
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
        return {"id": order_id, "status": "accepted",
                "filled_avg_price": None, "filled_qty": 0, "legs": []}

    def closed_orders(self, symbol, limit=20):
        return []

    def cancel_open_orders(self, symbol):
        self.canceled.append(symbol)
        return 1

    def flatten_all(self):
        raise AssertionError("the kill switch must not fire in these tests")


@pytest.fixture
def scripted_cycle(tmp_path, monkeypatch, cfg):
    """Run one real `main.run_cycle` with a scripted signal.

    Returns `run(action, positions=..., risk_overrides=...)`, which yields the
    broker afterwards so a test can inspect what was submitted AND the
    in-cycle position view the cycle left behind.

    `symbols=[...]` widens the scan list. A one-symbol cycle can never reach a
    SECOND entry, and the second entry is the only moment the in-cycle
    open-trades dict is ever READ (Group C-bis, defect 7).
    """
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3      # the fixture bars are short

    def run(action, positions=None, holding_action=None, symbols=None,
            **risk_overrides):
        local = copy.deepcopy(cfg)
        local["risk"].update(risk_overrides)
        if symbols is not None:
            local["symbols"] = symbols
        with open("config.yaml", "w") as f:
            yaml.safe_dump(local, f)

        # `position_side` is accepted and unused: this fixture scripts the
        # ACTION directly, so it never has to derive one from the side. It must
        # still appear in the signature — main.py now passes it on the exit
        # path, and a fake that refuses an argument the real dispatch accepts
        # fails the cycle for a reason unrelated to what the test is asserting.
        def _scripted(name, symbol, bars, cfg_, holding,
                      cross_section=None, entry_ts=None, position_side=None):
            want = holding_action if holding else action
            if want is None:
                return Signal(symbol, "hold", "scripted hold", {}, name)
            return Signal(symbol, want, f"scripted {want}", {}, name)

        monkeypatch.setattr(strategies, "generate", _scripted)
        broker = _ScriptedCycleBroker(make_bars(CYCLE_CLOSES), positions)
        monkeypatch.setattr(main, "Broker", lambda c: broker)
        main.run_cycle()
        broker.cfg = local
        return broker

    return run


def _observed_view(broker) -> dict:
    """The in-cycle position view main.py actually left behind — the same
    object every risk rail in the cycle read."""
    return broker.view


# ---------------------------------------------------------------------------
# THE LOAD-BEARING PAIR. A short must be RECORDED in the in-cycle position
# view with a NEGATIVE market value, matching what broker.positions() returns
# for a real short. Before this fix it was popped, and every rail that reads
# `positions` mid-cycle went blind to it.
# ---------------------------------------------------------------------------

def test_a_same_cycle_short_is_recorded_in_the_position_view_as_a_negative(
        scripted_cycle):
    """THE test that would have caught the defect. A "short" used to fall into
    the `else` branch and pop the symbol; the view must instead carry it with
    negative qty AND negative market_value — the shape src/broker.py builds
    from Alpaca's own signed fields, so the in-cycle view and the broker's
    view agree on sign and not merely on membership."""
    broker = scripted_cycle("short")
    view = _observed_view(broker)
    assert "SPY" in view, ("a same-cycle short must be RECORDED in the "
                           "position view, not popped out of it")
    assert view["SPY"]["qty"] == -50
    assert view["SPY"]["market_value"] == -50 * CYCLE_PRICE
    assert view["SPY"]["avg_entry"] == CYCLE_PRICE


def test_a_same_cycle_buy_is_still_recorded_in_the_position_view_as_a_positive(
        scripted_cycle):
    """The twin, differing only in the direction traded. The long path must be
    untouched: positive qty, positive market value, the same four keys and the
    same numbers as before this commit."""
    broker = scripted_cycle("buy")
    assert _observed_view(broker)["SPY"] == {
        "qty": 50, "market_value": 50 * CYCLE_PRICE,
        "avg_entry": CYCLE_PRICE, "unrealized_pl": 0.0}


# ---------------------------------------------------------------------------
# ...and the reason that shape matters: Phase 1's net-exposure band and the
# down-regime GROSS cap both read the in-cycle `positions` dict. These four
# tests feed each rail the view a real cycle produced, and pin the
# discriminator explicitly — the same rail, given the EMPTY view the old
# pop-the-symbol behaviour left behind, does not fire at all.
# ---------------------------------------------------------------------------

def _band_cfg(lo=None, hi=None, gross_pct=None):
    band = {}
    if lo is not None:
        band["min"] = lo
    if hi is not None:
        band["max"] = hi
    r = {"risk_per_trade_pct": 1.0, "max_position_pct": 100.0,
         "max_order_value_usd": 0, "max_open_positions": 0,
         "net_exposure_pct": band}
    if gross_pct is not None:
        r["regime_exposure"] = {"enabled": True, "down_max_gross_pct": gross_pct}
    return {"risk": r}


def test_the_net_exposure_band_sees_a_short_opened_earlier_in_the_same_cycle(
        scripted_cycle):
    """The rail Phase 1 merged, against the view Group C produces. The cycle
    shorts $1,000 of a $100k book (net -1.0%); a second $1,000 short would
    take net to -2.0%, through a -1.5% floor. It must be REFUSED.

    The second assertion is the whole point: handed the empty view the old
    `else`-branch pop left behind, the identical rail reads net as 0.0%,
    projects -1.0%, and permits the order. Nothing would have failed — the
    band would simply have been blind to every short opened this cycle."""
    view = _observed_view(scripted_cycle("short"))
    cfg = _band_cfg(lo=-1.5)
    assert risk.net_exposure_pct(view, 100_000.0) == pytest.approx(-1.0)
    with pytest.raises(risk.RiskRejection, match="net exposure band"):
        risk.pure_checks("short", "AAPL", 10, 100.0, ACCOUNT, view, cfg)
    # The discriminator: same rail, same order, pre-fix view.
    risk.pure_checks("short", "AAPL", 10, 100.0, ACCOUNT, {}, cfg)


def test_the_net_exposure_band_still_sees_a_buy_opened_in_the_same_cycle(
        scripted_cycle):
    """The twin, differing only in direction and in which side of the band it
    tests. This half never regressed — a buy was always recorded — and it is
    here so a change that recorded shorts by breaking longs cannot pass."""
    view = _observed_view(scripted_cycle("buy"))
    cfg = _band_cfg(hi=1.5)
    assert risk.net_exposure_pct(view, 100_000.0) == pytest.approx(1.0)
    with pytest.raises(risk.RiskRejection, match="net exposure band"):
        risk.pure_checks("buy", "AAPL", 10, 100.0, ACCOUNT, view, cfg)
    risk.pure_checks("buy", "AAPL", 10, 100.0, ACCOUNT, {}, cfg)


def test_the_down_regime_gross_cap_sees_a_same_cycle_short(scripted_cycle):
    """GROSS sums MAGNITUDES, so a short counts toward it exactly as a long
    does — but only if the short is in the dict at all. $1,000 already
    deployed plus a $1,000 order is $2,000 gross against a $1,500 cap."""
    view = _observed_view(scripted_cycle("short"))
    cfg = _band_cfg(gross_pct=1.5)
    with pytest.raises(risk.RiskRejection, match="down-regime exposure cap"):
        risk.pure_checks("short", "AAPL", 10, 100.0, ACCOUNT, view, cfg,
                         regime_label="down_lowvol")
    risk.pure_checks("short", "AAPL", 10, 100.0, ACCOUNT, {}, cfg,
                     regime_label="down_lowvol")


def test_the_down_regime_gross_cap_still_sees_a_same_cycle_buy(scripted_cycle):
    """The twin. Identical numbers to the short above — which is the point of
    a gross cap: $1,000 of exposure is $1,000 whichever way it points."""
    view = _observed_view(scripted_cycle("buy"))
    cfg = _band_cfg(gross_pct=1.5)
    with pytest.raises(risk.RiskRejection, match="down-regime exposure cap"):
        risk.pure_checks("buy", "AAPL", 10, 100.0, ACCOUNT, view, cfg,
                         regime_label="down_lowvol")
    risk.pure_checks("buy", "AAPL", 10, 100.0, ACCOUNT, {}, cfg,
                     regime_label="down_lowvol")


# ---------------------------------------------------------------------------
# The bracket submission: a short entry must carry `side` and a POSITIVE
# `entry_price` (Phase 1 made both required — a short's loss is unbounded, so
# its stop may not go unchecked), and its bracket geometry must invert.
#
# A long must send exactly what it sent before this commit: no `side`, no
# `entry_price`. That is not cosmetic — `bracket_market_order` applies a
# geometry check whenever `entry_price` is present, and main.py's live call
# site has never triggered it.
# ---------------------------------------------------------------------------

def test_a_short_entry_submits_its_bracket_with_side_short_and_a_real_entry(
        scripted_cycle):
    broker = scripted_cycle("short")
    call = broker.bracket_calls[0]
    assert call["side"] == "short"
    assert call["entry_price"] == CYCLE_PRICE > 0


def test_a_long_entry_still_submits_its_bracket_with_no_side_and_no_entry_price(
        scripted_cycle):
    """The twin, and the byte-identical promise stated as an assertion. A long
    that started passing `entry_price` would newly activate the long geometry
    check on the live path — a behaviour change dressed as a no-op."""
    broker = scripted_cycle("buy")
    call = broker.bracket_calls[0]
    assert "side" not in call and "entry_price" not in call


def test_a_short_entrys_bracket_stop_is_above_entry_and_its_target_below(
        scripted_cycle):
    """The geometry that makes the order placeable at all: Phase 1's broker
    REFUSES a short whose stop is not above entry, so a short reaching the
    broker with a long's stop would have been rejected at submission."""
    call = scripted_cycle("short").bracket_calls[0]
    assert call["stop_price"] == pytest.approx(round(CYCLE_PRICE + 2 * CYCLE_ATR, 2))
    assert call["take_profit_price"] == pytest.approx(
        round(CYCLE_PRICE - 3 * CYCLE_ATR, 2))
    assert call["stop_price"] > CYCLE_PRICE > call["take_profit_price"]


def test_a_long_entrys_bracket_stop_is_still_below_entry_and_its_target_above(
        scripted_cycle):
    """The twin — the exact numbers tests/test_main_cycle.py already pins,
    restated here so the direction wiring cannot move them."""
    call = scripted_cycle("buy").bracket_calls[0]
    assert call["stop_price"] == pytest.approx(round(CYCLE_PRICE - 2 * CYCLE_ATR, 2))
    assert call["take_profit_price"] == pytest.approx(
        round(CYCLE_PRICE + 3 * CYCLE_ATR, 2))
    assert call["stop_price"] < CYCLE_PRICE < call["take_profit_price"]


# ---------------------------------------------------------------------------
# Sizing: risk.size_order also grew a `direction` in Group A, and main.py must
# pass it. The discriminator here is a NUMBER, not a spy on the call: with
# stop-distance sizing on, a correctly-directed short sizes to 13 shares; an
# un-wired one fails `_risk_sizing_active` (its stop is on the "wrong" side of
# price for a long), falls back silently to notional, and sizes 50.
# ---------------------------------------------------------------------------

_RISK_SIZING = {"risk_sizing": {"enabled": True, "risk_pct": 0.1,
                                "strategies": ["ma_crossover"]}}


def test_a_short_entry_is_stop_distance_sized_not_silently_dropped_to_notional(
        scripted_cycle):
    broker = scripted_cycle("short", **_RISK_SIZING)
    assert broker.bracket_calls[0]["qty"] == 13, (
        "50 here would mean the short fell back to notional sizing — the "
        "silent failure Group A's `direction` parameter exists to prevent")


def test_a_long_entry_is_stop_distance_sized_to_the_same_share_count(
        scripted_cycle):
    """The twin. The distance is the same magnitude on both sides, so the
    share count must be identical — and the long's must not have moved."""
    broker = scripted_cycle("buy", **_RISK_SIZING)
    assert broker.bracket_calls[0]["qty"] == 13


# ---------------------------------------------------------------------------
# The two sites found in review.
#
# 1. main.py hardcoded `"action": "buy"` into the IN-CYCLE open_trades record,
#    so Group A's `portfolio_heat` short branch — which reads exactly that
#    key — was inert on that path: nothing ever wrote "short", so a short
#    opened earlier in the same cycle contributed 0.0 to the heat cap.
#
#    An earlier version of this comment also claimed the LEDGER decision was
#    hardcoded. It never was — main.py has always called
#    `ledger.log_decision(symbol, sig.action, ...)`. The ledger path's defect
#    was #2 below, the `open_buys()` FILTER, not the value written.
#
#    Note which writer the tests immediately below exercise: all of them go
#    through `_reloaded_book()`, i.e. the LEDGER. The in-cycle dict is a
#    different writer and needs its own test — see Group C-bis, defect 7.
# 2. ledger.open_buys() filtered `action == "buy"`. It is the only path by
#    which the live bot reloads its open book, so a restart dropped every open
#    short from the reloaded book.
# ---------------------------------------------------------------------------

def _reloaded_book(broker) -> dict:
    """The open book as a RESTARTED process would reload it — straight off
    persisted state, through the same `open_buys()` the live cycle calls."""
    return Ledger(broker.cfg["memory"]["ledger_path"]).open_buys()


def test_an_open_short_survives_the_reload_path_with_its_action_intact(
        scripted_cycle):
    """The restart defect. On the old `action == "buy"` filter this dict came
    back EMPTY: the position still existed at the broker, but the bot held no
    record of it — no owner to route its exit, no stop-risk in the heat cap,
    and adopt_untracked_positions would have re-adopted it as a fresh trade
    with a fabricated entry price."""
    book = _reloaded_book(scripted_cycle("short"))
    assert len(book) == 1, "a restart must not drop an open short"
    rec = next(iter(book.values()))
    assert rec["action"] == "short"
    assert rec["symbol"] == "SPY" and rec["qty"] == 50


def test_an_open_buy_still_survives_the_reload_path_with_its_action_intact(
        scripted_cycle):
    """The twin — the case that always worked, pinned so widening the filter
    cannot have narrowed it."""
    book = _reloaded_book(scripted_cycle("buy"))
    assert len(book) == 1
    rec = next(iter(book.values()))
    assert rec["action"] == "buy"
    assert rec["symbol"] == "SPY" and rec["qty"] == 50


def test_the_heat_cap_sees_a_reloaded_shorts_real_stop_risk_not_zero(
        scripted_cycle):
    """The consequence of writing the real action, measured through the rail
    it unblocks. A short entered at $20 with its stop at $27.33 risks
    50 x $7.33. With "buy" hardcoded, portfolio_heat took (entry - stop),
    got a negative, and the max(..., 0.0) clamp zeroed it out invisibly."""
    book = _reloaded_book(scripted_cycle("short"))
    heat = risk.portfolio_heat(book, _cfg(), 100_000.0)
    assert heat == pytest.approx(50 * (round(CYCLE_PRICE + 2 * CYCLE_ATR, 2)
                                       - CYCLE_PRICE))
    assert heat > 0.0, "a short's stop-risk must not read as zero"


def test_the_heat_cap_still_sees_a_reloaded_buys_stop_risk_unchanged(
        scripted_cycle):
    """The twin, and the symmetry that makes the fix legible: the same
    distance, so the same dollars of heat, whichever way the position points."""
    book = _reloaded_book(scripted_cycle("buy"))
    heat = risk.portfolio_heat(book, _cfg(), 100_000.0)
    assert heat == pytest.approx(50 * (CYCLE_PRICE
                                       - round(CYCLE_PRICE - 2 * CYCLE_ATR, 2)))


# ---------------------------------------------------------------------------
# The EXIT half of the vocabulary. Three sites converge on one flow:
#
#   * main.py ~1451 dispatched the held-position exit on `action == "sell"`,
#     so a "cover" — the only way a short can ever be closed — fell into the
#     `else` and was logged as a HOLD. The position's own exit signal,
#     discarded every cycle, forever.
#   * main.py ~1259 cancelled the surviving bracket legs on `action ==
#     "sell"`. Those legs reserve the shares; a cover submitted without the
#     cancel races the stop leg for the same position.
#   * main.py ~1188 read the exit quantity straight off the position view.
#     A broker reports a short's qty NEGATIVE, so a cover computed a negative
#     qty and `pure_checks` refused it as `zero_qty` — the rail trapping the
#     position it exists to protect.
# ---------------------------------------------------------------------------

def _age_the_open_entry(path, days=30):
    """Backdate the open decision record so the swing guard lets the exit run.

    min_holding_days cannot simply be set to 0 here: preflight refuses a
    non-positive value and ABORTS the cycle, so a 0 would leave these tests
    silently asserting against a cycle that never ran. Aging the position is
    also the truthful setup — a real cover happens to a short that has been
    open a while."""
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    for r in rows:
        if r.get("type") == "decision":
            r["ts"] = old
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _open_then_close(scripted_cycle, entry, exit_action, sign):
    """Cycle 1 opens the position; cycle 2 hands the broker the position the
    way a real broker would report it (SIGNED qty) and scripts the exit."""
    opened = scripted_cycle(entry)
    _age_the_open_entry(opened.cfg["memory"]["ledger_path"])
    held = {"SPY": {"qty": sign * 50.0,
                    "market_value": sign * 50 * CYCLE_PRICE,
                    "avg_entry": CYCLE_PRICE, "unrealized_pl": 0.0}}
    return scripted_cycle("hold", positions=held, holding_action=exit_action)


def test_a_cover_on_a_held_short_is_routed_to_the_exit_path_at_full_size(
        scripted_cycle):
    """All three exit-side sites in one assertion set. `qty == 50`, not -50
    and not 0, is the `abs()` half: without it the cover never reaches the
    broker at all, and the short is trapped."""
    broker = _open_then_close(scripted_cycle, "short", "cover", -1)
    exits = [o for o in broker.submitted if o["side"] == "cover"]
    assert len(exits) == 1, "a cover must reach the broker, not be logged as a hold"
    assert exits[0]["qty"] == 50
    assert broker.canceled == ["SPY"], (
        "the surviving bracket legs must be cancelled before the cover — they "
        "reserve the very shares it is trying to buy back")
    assert "SPY" not in _observed_view(broker), "a cover must pop the symbol"


def test_a_sell_on_a_held_long_still_routes_to_the_exit_path_at_full_size(
        scripted_cycle):
    """The twin, differing only in direction. Same three sites, the case that
    always worked."""
    broker = _open_then_close(scripted_cycle, "buy", "sell", 1)
    exits = [o for o in broker.submitted if o["side"] == "sell"]
    assert len(exits) == 1
    assert exits[0]["qty"] == 50
    assert broker.canceled == ["SPY"]
    assert "SPY" not in _observed_view(broker)


def test_closing_a_short_writes_its_outcome_so_the_reloaded_book_empties(
        scripted_cycle):
    """The round trip. `open_buys()` must both KEEP the short across a restart
    while it is open and RELEASE it once closed — a filter that leaked either
    way would desync the book against the broker."""
    broker = _open_then_close(scripted_cycle, "short", "cover", -1)
    assert _reloaded_book(broker) == {}


def test_closing_a_long_still_writes_its_outcome_the_same_way(scripted_cycle):
    """The twin."""
    broker = _open_then_close(scripted_cycle, "buy", "sell", 1)
    assert _reloaded_book(broker) == {}


# ---------------------------------------------------------------------------
# main.py ~1498: the flat-symbol entry filter. `action != "buy"` filed every
# non-buy as a hold REASON, so a "short" on a flat symbol was recorded as a
# reason not to trade rather than evaluated as the entry it is.
# ---------------------------------------------------------------------------

def test_a_short_on_a_flat_symbol_is_evaluated_as_an_entry_not_filed_as_a_hold(
        scripted_cycle):
    broker = scripted_cycle("short")
    assert len(broker.submitted) == 1, (
        "a short on a flat symbol must be evaluated as an entry; on the old "
        "`action != \"buy\"` filter it never reached the pipeline at all")


def test_a_buy_on_a_flat_symbol_is_still_evaluated_as_an_entry(scripted_cycle):
    """The twin."""
    assert len(scripted_cycle("buy").submitted) == 1


def test_a_hold_on_a_flat_symbol_still_trades_nothing(scripted_cycle):
    """The other boundary of the same filter, and the one that matters most:
    widening "what counts as an entry" must not have widened it to everything.
    A "hold" is neither an entry nor an exit and must still submit nothing."""
    assert scripted_cycle("hold").submitted == []


# ---------------------------------------------------------------------------
# The remaining hardcoded "buy" literals: the judgment ledger (judge
# calibration is measured off it) and the public journal/recap. Both described
# a short as a buy — a lie in the record rather than a mis-executed trade,
# which is the harder kind to notice later.
# ---------------------------------------------------------------------------

def _judgment_actions(broker) -> list:
    import judgments
    store = judgments.JudgmentStore(broker.cfg["learning"]["judgments_path"])
    return [r.get("action") for r in store.replay().values()]


def test_an_executed_shorts_judgment_records_the_action_as_short(scripted_cycle):
    """Judge calibration is scored off this store. Recording every short as a
    "buy" would not misroute a single order — it would quietly corrupt every
    calibration number computed from the judgment ledger afterwards."""
    assert _judgment_actions(scripted_cycle("short")) == ["short"]


def test_an_executed_buys_judgment_still_records_the_action_as_buy(
        scripted_cycle):
    """The twin."""
    assert _judgment_actions(scripted_cycle("buy")) == ["buy"]


def _journal_kinds(broker) -> list:
    """journal.add_entry derives its `kind` field straight from the trade
    dict's `action`, so `kind` is where the hardcoded "buy" surfaced."""
    with open(broker.cfg["x_posting"]["journal_path"]) as f:
        return [json.loads(line)["kind"] for line in f if line.strip()]


def test_a_shorts_public_journal_entry_is_filed_as_a_short(scripted_cycle):
    """The journal is this bot's public track record and is read back by the
    lesson loop. Filing a short under "buy" misstates the direction of a real
    trade to both."""
    assert _journal_kinds(scripted_cycle("short")) == ["short"]


def test_a_buys_public_journal_entry_is_still_filed_as_a_buy(scripted_cycle):
    """The twin."""
    assert _journal_kinds(scripted_cycle("buy")) == ["buy"]


# ---------------------------------------------------------------------------
# The substitution argument itself, made executable.
#
# Every one of Group C's fifteen main.py sites is one of four rewrites. Each
# is a no-op for the only two actions anything in this repo currently emits —
# that is the whole reason a change this large could be made to the live
# trading cycle at all. Stated here as an assertion rather than left in a
# commit message, so a future edit to ENTRY_ACTIONS/EXIT_ACTIONS that broke
# the property would be caught by the suite instead of by production.
# ---------------------------------------------------------------------------

def test_every_group_c_rewrite_is_a_no_op_for_buy_and_sell():
    from strategies.base import ENTRY_ACTIONS as E, EXIT_ACTIONS as X
    for act in ("buy", "sell"):
        assert (act in E) == (act == "buy")          # `== "buy"`  -> `in E`
        assert (act not in E) == (act != "buy")      # `!= "buy"`  -> `not in E`
        assert (act in X) == (act == "sell")         # `== "sell"` -> `in X`
    # And the fourth rewrite, `"buy"` -> `sig.action`, is identity when the
    # action IS "buy" — trivially, but it is the rewrite applied to five
    # hardcoded literals, so pin the premise rather than assume it.
    assert "buy" == "buy"


def test_the_two_vocabularies_stay_disjoint():
    """The property every one of those rewrites rests on. An action in both
    sets would make an entry rail and an exit rail fire on the same signal —
    the guard would still LOOK gated at every one of the fifteen sites."""
    from strategies.base import ENTRY_ACTIONS as E, EXIT_ACTIONS as X
    assert set(E).isdisjoint(X)


# ===========================================================================
# GROUP C-bis: the SIGN-AWARE half of Group C.
#
# Group C fixed main.py's action VOCABULARY — which branch a "short" or a
# "cover" takes. These six sites need direction-aware ARITHMETIC or
# direction-aware order MATCHING instead, which is why they were deliberately
# left out of that commit: each changes a NUMBER or a LOOKUP, not a branch,
# and every one of them has to be argued separately as a no-op for a long.
#
# Same boundary-pair discipline as the rest of this file. Nothing shorts yet,
# so the long twin is the ONLY evidence that a sign-aware rewrite left live
# behaviour byte-identical; a short-side assertion standing alone cannot tell
# "fixed the short" from "broke the long and got lucky on this number".
# ===========================================================================

from memory import Memory


@pytest.fixture
def closing_env(tmp_path, cfg):
    """Real Ledger + Memory on tmp_path, LLM off, X dry-run — the same offline
    harness tests/test_reconcile.py uses, so these drive the very handle_close
    and resolve_exit_price the live reconcile path calls."""
    cfg["memory"]["ledger_path"] = str(tmp_path / "memory" / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "memory" / "learnings.md")
    ledger = Ledger(cfg["memory"]["ledger_path"])
    return ledger, Memory(cfg, ledger), cfg


def _open_entry(ledger, action, entry=100.0, qty=10):
    """One executed ENTRY record, in the shape main.py writes it: qty POSITIVE
    on both sides, direction carried by `action`, bracket legs on the side the
    direction requires."""
    long_side = action == "buy"
    order = {"id": "entry-1", "symbol": "SPY", "qty": qty, "side": action,
             "status": "filled", "order_class": "bracket",
             "stop_price": 90.0 if long_side else 110.0,
             "take_profit_price": 115.0 if long_side else 85.0,
             "leg_ids": ["leg-stop", "leg-tp"]}
    tid = ledger.log_decision("SPY", action, "scripted", {}, None,
                              executed=True, order=order, entry_price=entry,
                              qty=qty, strategy="ma_crossover")
    return tid, ledger.open_buys()[tid]


# ---------------------------------------------------------------------------
# Defect 1. main.py's handle_close computed `(exit - entry) * qty` with qty
# stored POSITIVE, so a short that fell $10 recorded as a $100 LOSS — and
# `result` inverted with it. That outcome record is what the lesson
# generator, the scorecard and the decay monitor all read, so the learning
# loop would have been fed a mirror image of the bot's own short book. This
# is the highest-value fix in the group.
# ---------------------------------------------------------------------------

def test_a_profitable_shorts_pnl_is_positive_and_records_as_a_win(closing_env):
    """Shorted at $100, covered at $90: the short made $10 a share."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "short")
    main.handle_close(tid, rec, 90.0, ledger, memory, cfg)
    t = ledger.closed_trades()[0]
    assert t["pnl"] == pytest.approx(100.0), (
        "-100.0 here is the defect: a profitable short recorded as a loss")
    assert t["pnl_pct"] == pytest.approx(10.0)
    assert t["result"] == "win"


def test_a_profitable_longs_pnl_is_still_positive_and_records_as_a_win(
        closing_env):
    """The twin, differing only in direction: bought at $100, sold at $110 —
    the same $10 a share, the same numbers this path has always written."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "buy")
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)
    t = ledger.closed_trades()[0]
    assert t["pnl"] == pytest.approx(100.0)
    assert t["pnl_pct"] == pytest.approx(10.0)
    assert t["result"] == "win"


def test_a_losing_shorts_pnl_is_negative_and_records_as_a_loss(closing_env):
    """The other side of the same inversion, and the more dangerous one: a
    short stopped out UP $10 booked as a $100 win would teach the judge that
    its worst shorts were its best.  Note the argument this pins is not
    "shorts are profitable" — it is that the sign tracks the direction."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "short")
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)
    t = ledger.closed_trades()[0]
    assert t["pnl"] == pytest.approx(-100.0)
    assert t["pnl_pct"] == pytest.approx(-10.0)
    assert t["result"] == "loss"


def test_a_losing_longs_pnl_is_still_negative_and_records_as_a_loss(
        closing_env):
    """The twin."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "buy")
    main.handle_close(tid, rec, 90.0, ledger, memory, cfg)
    t = ledger.closed_trades()[0]
    assert t["pnl"] == pytest.approx(-100.0)
    assert t["pnl_pct"] == pytest.approx(-10.0)
    assert t["result"] == "loss"


def test_a_legacy_record_with_no_action_key_is_still_read_as_a_long(
        closing_env):
    """Every outcome written before Group C carries no `action` at all. The
    direction sign must default to the long formula, or this fix would
    silently reinterpret the existing record set — the same promise
    risk.portfolio_heat makes about legacy books."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "buy")
    rec.pop("action")
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)
    assert ledger.closed_trades()[0]["pnl"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Defect 6. The closing recap was posted with a hardcoded `"action": "sell"`,
# so a covered short would have been announced to the public feed — and
# archived in the bot's own post record — as a sell.
# ---------------------------------------------------------------------------

def _posted_texts(cfg) -> list:
    with open(cfg["x_posting"]["posts_log_path"]) as f:
        return [json.loads(line)["text"] for line in f if line.strip()]


def test_the_closing_recap_for_a_short_is_captioned_cover_not_sell(closing_env):
    """A short is closed by a COVER. Captioning it "sell" misstates the
    direction of a real trade in the public track record — and in the archive
    the blog and the lesson loop read back."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "short")
    main.handle_close(tid, rec, 90.0, ledger, memory, cfg)
    assert any("COVER" in t for t in _posted_texts(cfg))
    assert not any("SELL" in t for t in _posted_texts(cfg))


def test_the_closing_recap_for_a_long_is_still_captioned_sell(closing_env):
    """The twin — the caption this path has always written."""
    ledger, memory, cfg = closing_env
    tid, rec = _open_entry(ledger, "buy")
    main.handle_close(tid, rec, 110.0, ledger, memory, cfg)
    assert any("SELL" in t for t in _posted_texts(cfg))
    assert not any("COVER" in t for t in _posted_texts(cfg))


# ---------------------------------------------------------------------------
# Defect 3. resolve_exit_price's closed-order fallback matched
# `side.endswith("sell")`. A short is closed by a BUY, so for a short that
# fallback either found nothing or — the realistic case, because Alpaca
# returns closed orders NEWEST FIRST and a short's own opening SELL is in
# that list — matched the OPENING order and returned the ENTRY price as the
# exit price. handle_close would then write a fabricated ~$0 outcome into the
# permanent, append-only record: not a missed trade, a false one.
# ---------------------------------------------------------------------------

class _ClosedOrderBroker:
    """Only the two methods resolve_exit_price's fallback path touches. No
    `leg_ids` lookup succeeds, so the closed-order branch is what is under
    test — the same shape tests/test_reconcile.py's FakeBroker uses."""

    def __init__(self, closed):
        self._closed = closed

    def get_order(self, order_id):
        raise RuntimeError("leg lookup unavailable — fall through")

    def closed_orders(self, symbol, limit=20):
        return self._closed

    def last_price(self, symbol):
        raise AssertionError("the closed-order branch must resolve this exit")


def _order(side, price):
    return {"id": f"o-{side}", "side": side, "status": "OrderStatus.FILLED",
            "type": "OrderType.MARKET", "filled_at": None,
            "filled_avg_price": price, "filled_qty": 10.0}


def test_a_shorts_exit_is_resolved_from_the_buy_that_closed_it(closing_env):
    """Newest-first, exactly as broker.closed_orders returns it: the covering
    BUY at $92, then the short's own opening SELL at $100. On the un-fixed
    `endswith("sell")` this returned 100.0 — the entry price, dressed up as
    an exit, producing a $0 P&L outcome for a trade that made $80."""
    ledger, _memory, _cfg = closing_env
    _tid, rec = _open_entry(ledger, "short")
    broker = _ClosedOrderBroker([_order("OrderSide.BUY", 92.0),
                                 _order("OrderSide.SELL", 100.0)])
    price, reason = main.resolve_exit_price(broker, rec)
    assert price == 92.0, ("100.0 here is the short's OWN entry fill matched "
                           "as its exit — a fabricated outcome record")
    assert reason == "closed_order"


def test_a_longs_exit_is_still_resolved_from_the_sell_that_closed_it(
        closing_env):
    """The twin, mirrored: the closing SELL at $108 newest, the opening BUY at
    $100 behind it. This is the case that always worked, and the assertion
    that proves matching on direction did not start matching the long's own
    entry instead."""
    ledger, _memory, _cfg = closing_env
    _tid, rec = _open_entry(ledger, "buy")
    broker = _ClosedOrderBroker([_order("OrderSide.SELL", 108.0),
                                 _order("OrderSide.BUY", 100.0)])
    price, reason = main.resolve_exit_price(broker, rec)
    assert price == 108.0
    assert reason == "closed_order"


def test_a_legacy_record_with_no_action_still_resolves_off_a_sell(closing_env):
    """Pre-Group-C records carry no `action`. They are all longs, and they must
    keep resolving exactly as they did — the same legacy promise the P&L sign
    makes above."""
    ledger, _memory, _cfg = closing_env
    _tid, rec = _open_entry(ledger, "buy")
    rec.pop("action")
    broker = _ClosedOrderBroker([_order("OrderSide.SELL", 108.0),
                                 _order("OrderSide.BUY", 100.0)])
    assert main.resolve_exit_price(broker, rec)[0] == 108.0


# ---------------------------------------------------------------------------
# Defects 4 and 5. adopt_untracked_positions rescues a 'ghost' — a broker
# position whose ledger write never landed. It could not rescue a short:
#
#   * `qty = int(pos.get("qty") or 0); if qty <= 0: continue` skipped every
#     short outright, because src/broker.py copies Alpaca's own float(p.qty),
#     which is NEGATIVE for a short. A ghost short stayed a ghost forever —
#     the exact silent omission from the P&L, the learning loop and the
#     public track record this function exists to prevent, on one side only.
#   * it then wrote a hardcoded "buy" — the last hardcoded literal that
#     survived Group C.
#   * _recover_fill_ts matched the side that OPENS a long. A short is opened
#     by a SELL, so an adopted short's real fill time was never recovered and
#     its holding clock restarted at the adoption write, blocking its exit
#     for another min_holding_days.
# ---------------------------------------------------------------------------

class _RoundTripBroker:
    """closed_orders carrying BOTH sides, so each test's assertion names the
    side that OPENED the position under test rather than passing by default.
    A real history has both: every prior round trip left one of each."""

    def __init__(self, buy_ts, sell_ts):
        self._orders = [
            {"id": "o-buy", "side": "OrderSide.BUY", "status": "filled",
             "type": "OrderType.MARKET", "filled_at": buy_ts,
             "filled_avg_price": 450.0, "filled_qty": 10.0},
            {"id": "o-sell", "side": "OrderSide.SELL", "status": "filled",
             "type": "OrderType.MARKET", "filled_at": sell_ts,
             "filled_avg_price": 450.0, "filled_qty": 10.0},
        ]

    def closed_orders(self, symbol, limit=20):
        return self._orders


def _adopt(ledger, cfg, qty, broker=None, avg_entry=450.0, market_value=None):
    positions = {"SPY": {"qty": qty, "avg_entry": avg_entry,
                         "market_value": (qty * 450.0 if market_value is None
                                          else market_value),
                         "unrealized_pl": 0.0}}
    main.adopt_untracked_positions(broker=broker, ledger=ledger, cfg=cfg,
                                   positions=positions)
    return ledger.open_buys()


def test_a_ghost_short_is_adopted_as_a_short_with_a_positive_quantity(
        closing_env):
    """The broker reports -10; the ledger stores magnitude and carries the
    direction in `action`, which is the invariant every other site in this
    phase depends on. On the un-fixed `qty <= 0: continue` this book came
    back EMPTY."""
    ledger, _memory, cfg = closing_env
    book = _adopt(ledger, cfg, qty=-10)
    assert len(book) == 1, "a ghost short must be adopted, not skipped"
    rec = next(iter(book.values()))
    assert rec["action"] == "short"
    assert rec["qty"] == 10, "quantity is stored POSITIVE on both sides"
    assert rec["entry_price"] == 450.0


def test_a_ghost_long_is_still_adopted_as_a_buy_unchanged(closing_env):
    """The twin, differing only in the sign the broker reported. Same three
    fields, same numbers tests/test_adopt.py already pins."""
    ledger, _memory, cfg = closing_env
    book = _adopt(ledger, cfg, qty=10)
    assert len(book) == 1
    rec = next(iter(book.values()))
    assert rec["action"] == "buy"
    assert rec["qty"] == 10
    assert rec["entry_price"] == 450.0


def test_a_flat_symbol_is_still_not_adopted(closing_env):
    """The boundary the `qty <= 0` guard was really there for. Widening it to
    admit negatives must not have widened it to admit zero — a symbol the
    broker reports flat is not a position to rescue."""
    ledger, _memory, cfg = closing_env
    assert _adopt(ledger, cfg, qty=0) == {}


def test_an_adopted_shorts_entry_price_is_positive_when_derived_from_value(
        closing_env):
    """The defensive branch: with no avg_entry, entry is derived from market
    value — which the broker reports NEGATIVE for a short. Dividing that by a
    now-positive qty would record a -$450 entry price, and every P&L, stop
    distance and heat number computed from it afterwards would be nonsense."""
    ledger, _memory, cfg = closing_env
    book = _adopt(ledger, cfg, qty=-10, avg_entry=None, market_value=-4500.0)
    assert next(iter(book.values()))["entry_price"] == pytest.approx(450.0)


def test_an_adopted_longs_entry_price_from_value_is_unchanged(closing_env):
    """The twin: abs() is an identity on a long's positive market value."""
    ledger, _memory, cfg = closing_env
    book = _adopt(ledger, cfg, qty=10, avg_entry=None, market_value=4500.0)
    assert next(iter(book.values()))["entry_price"] == pytest.approx(450.0)


def test_an_adopted_short_recovers_the_fill_time_of_the_sell_that_opened_it(
        closing_env):
    """A short is OPENED by a sell. Matching "buy" unconditionally recovered
    either nothing or — with a prior round trip in the history, as here — the
    wrong order's timestamp, and the swing guard would then measure the
    position's age from the wrong moment."""
    ledger, _memory, cfg = closing_env
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(days=9)).isoformat()
    unrelated = (now - timedelta(days=40)).isoformat()
    book = _adopt(ledger, cfg, qty=-10,
                  broker=_RoundTripBroker(buy_ts=unrelated, sell_ts=opened))
    rec = next(iter(book.values()))
    assert rec["entry_ts"] == opened
    assert main.position_entry_ts(rec) == opened


def test_an_adopted_long_still_recovers_the_fill_time_of_its_opening_buy(
        closing_env):
    """The twin, mirrored — the case tests/test_adopt.py pins, restated here
    with the opposite-side order also present so the match is proven to be on
    the side that opens a LONG rather than on whatever came back first."""
    ledger, _memory, cfg = closing_env
    now = datetime.now(timezone.utc)
    opened = (now - timedelta(days=9)).isoformat()
    unrelated = (now - timedelta(days=40)).isoformat()
    book = _adopt(ledger, cfg, qty=10,
                  broker=_RoundTripBroker(buy_ts=opened, sell_ts=unrelated))
    rec = next(iter(book.values()))
    assert rec["entry_ts"] == opened
    assert main.position_entry_ts(rec) == opened


# ---------------------------------------------------------------------------
# Defect 2. record_fill_quality's sign. The convention is pinned by
# tests/test_fill_quality.py: POSITIVE bps means a WORSE fill than the price
# the signal saw. The sign therefore keys off whether the order BUYS or
# SELLS, not off whether it opens or closes:
#
#   buy   (buy to open)    pays    -> higher fill is worse -> +1
#   cover (buy to close)   pays    -> higher fill is worse -> +1
#   sell  (sell to close)  receives-> lower  fill is worse -> -1
#   short (sell to open)   receives-> lower  fill is worse -> -1
#
# `1 if action == "buy" else -1` gets three of those four right by accident,
# including short. The broken one is COVER — the only way a short is ever
# closed — which reported every cover's slippage cost as a saving, biasing
# the measured trading cost in GUIDE §9's go-live comparison in the
# flattering direction. All four are pinned here, because the argument for
# the fix is the whole table and not the one row that moves.
# ---------------------------------------------------------------------------

class _FillBroker:
    def __init__(self, fill):
        self._fill = fill

    def get_order(self, order_id):
        return {"id": order_id, "filled_avg_price": self._fill}


def _slippage_bps(ledger, action, signal, fill):
    ledger.log_decision("SPY", action, "scripted", {}, None, executed=True,
                        order={"id": "o-1"}, entry_price=signal, qty=10)
    main.record_fill_quality(_FillBroker(fill), ledger)
    return [r for r in ledger.all_records()
            if r["type"] == "fill_quality"][0]["slippage_bps"]


def test_a_cover_that_pays_more_than_the_signal_price_scores_worse(
        closing_env):
    """The broken row. A cover BUYS the shares back, so paying $100.05 for a
    signal of $100.00 is 5 bps of COST. The un-fixed sign returned -5.0 —
    a real cost reported as a saving."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "cover", 100.0, 100.05) == pytest.approx(5.0)


def test_a_buy_that_pays_more_than_the_signal_price_still_scores_worse(
        closing_env):
    """The twin for the other BUYING action, and the row
    tests/test_fill_quality.py already pins — restated here so a change to
    the sign rule cannot move the reference case it is derived from."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "buy", 100.0, 100.05) == pytest.approx(5.0)


def test_a_short_entry_that_fills_below_the_signal_price_scores_worse(
        closing_env):
    """A short SELLS to open, so receiving $99.95 against a $100.00 signal is
    5 bps of cost. This row was already correct — it is pinned because the
    earlier diagnosis of this defect named short entries as the broken branch
    and was wrong, and an unpinned "already correct" is what let that stand."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "short", 100.0, 99.95) == pytest.approx(5.0)


def test_a_sell_that_fills_below_the_signal_price_still_scores_worse(
        closing_env):
    """The twin for the other SELLING action."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "sell", 100.0, 99.95) == pytest.approx(5.0)


def test_a_cover_that_pays_less_than_the_signal_price_scores_better(
        closing_env):
    """The other side of the corrected row: a genuinely GOOD cover fill must
    still read negative. A sign fix that made every cover positive would pass
    the test above and be just as wrong."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "cover", 100.0, 99.95) == pytest.approx(-5.0)


def test_a_sell_that_fills_above_the_signal_price_still_scores_better(
        closing_env):
    """The twin."""
    ledger, _memory, _cfg = closing_env
    assert _slippage_bps(ledger, "sell", 100.0, 100.05) == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# Defect 7. Group C's `open_trades["action"] = sig.action` was UNPINNED:
# reverting it to the hardcoded "buy" left the whole suite green.
#
# The reason nothing caught it is that the existing heat tests measure the
# RELOADED ledger book (`open_buys()`), which is a different writer. The
# in-cycle dict is read exactly once — by `pre_trade_checks(open_trades=...)`
# for the SECOND entry of a cycle — and a second same-cycle short is the only
# thing that can tell the two writers apart. With "buy" hardcoded,
# portfolio_heat takes (entry - stop) on a short, gets a negative, and the
# max(..., 0.0) clamp zeroes the first short's real stop-risk out of the cap
# invisibly, so the second short is permitted through a cap it has already
# breached.
# ---------------------------------------------------------------------------

# The first entry risks 50 x |27.33 - 20.00| = $366.50 of a $100k book, on
# either side: the bracket distance is the same magnitude whichever way the
# position points.
#
# Before the short-path Fix 1 (pre_trade_checks' own `new_risk` term, in
# risk.py), the two caps below had to be DIFFERENT numbers: a short
# candidate's own risk read as $0.00 (its stop sits above price, and the
# un-fixed `max(price - candidate_stop, 0.0)` clamped that to zero), so only
# the long side's cap could put the boundary between its own first and
# second entry. Fix 1 makes both sides measure the same $366.50 for their
# own candidate order, so the SAME cap now puts the boundary in the same
# place on both sides:
#
#   cap $500 (0.5%) -> 1st: $0 + $366.50 ok | 2nd: $366.50 + $366.50 REFUSED
#
# What the pair holds constant is the thing under test: in both cases the
# refusal is driven by the FIRST entry's $366.50 read out of the in-cycle
# dict (portfolio_heat, Group A's fix) plus the SECOND entry's own $366.50
# candidate risk (pre_trade_checks, Fix 1).
_TWO = ["SPY", "AAPL"]


def _rejections(broker) -> list:
    led = Ledger(broker.cfg["memory"]["ledger_path"])
    return [r for r in led.all_records()
            if r["type"] == "decision" and not r["executed"]]


def test_the_heat_cap_refuses_a_second_same_cycle_short_on_the_first_ones_risk(
        scripted_cycle):
    """Two symbols, both shorted in one cycle: the first executes and the
    second is refused by the heat rail. This pins TWO fixes at once: defect 7
    (main.py must record the in-cycle position as `action: "short"`, not a
    hardcoded "buy", or the first short's stop-risk reads as $0 out of
    portfolio_heat) and short-path Fix 1 (pre_trade_checks' own `new_risk`
    term must price the second short's candidate risk instead of clamping it
    to $0 too) — either regression alone would let the second short
    through."""
    broker = scripted_cycle("short", symbols=_TWO,
                            max_portfolio_heat_pct=0.5)
    assert len(broker.bracket_calls) == 1, (
        "the second short must not reach the broker: the first short's "
        "$366.50 of stop-risk (portfolio_heat, Group A's fix) plus the "
        "second short's own $366.50 candidate risk (pre_trade_checks, "
        "Fix 1) together exceed the $500 cap")
    rejected = _rejections(broker)
    assert len(rejected) == 1
    assert rejected[0]["rail"] == "heat"
    assert rejected[0]["action"] == "short"


def test_the_heat_cap_still_refuses_a_second_same_cycle_buy_the_same_way(
        scripted_cycle):
    """The twin, now at the SAME cap value as the short above — Fix 1 made
    the two sides symmetric, so there is no longer a reason for them to
    differ. This half never had the action-vocabulary defect ("buy" is what
    the code always wrote) and pre_trade_checks' own new_risk term was
    already correct for a long, so this is the assertion proving neither fix
    broke behaviour that was already right."""
    broker = scripted_cycle("buy", symbols=_TWO, max_portfolio_heat_pct=0.5)
    assert len(broker.bracket_calls) == 1
    rejected = _rejections(broker)
    assert len(rejected) == 1
    assert rejected[0]["rail"] == "heat"
    assert rejected[0]["action"] == "buy"


def test_a_second_same_cycle_short_is_permitted_when_the_cap_has_room(
        scripted_cycle):
    """The other boundary. The rail above must fire on the NUMBER and not
    merely on "a second entry exists" — raise the cap clear of both positions
    and the second short must go through."""
    broker = scripted_cycle("short", symbols=_TWO, max_portfolio_heat_pct=5.0)
    assert len(broker.bracket_calls) == 2
    assert _rejections(broker) == []


def test_a_second_same_cycle_buy_is_still_permitted_when_the_cap_has_room(
        scripted_cycle):
    """The twin."""
    broker = scripted_cycle("buy", symbols=_TWO, max_portfolio_heat_pct=5.0)
    assert len(broker.bracket_calls) == 2
    assert _rejections(broker) == []


# ---------------------------------------------------------------------------
# Fix 2b: the account-aware preflight pass has exactly one production call
# site now — main.py's _bootstrap_cycle, AFTER Broker(cfg) exists and the
# account has been fetched. Before this fix, `preflight.run(cfg,
# account=...)` was never called with an account anywhere in production:
# the guard in preflight.py could reject a config, but nothing in main.py
# ever gave it the chance to. These tests exercise main.py's wiring, not
# preflight.py's own logic (test_short_rails.py covers that directly).
# ---------------------------------------------------------------------------

def _shorting_cfg(cfg, shorting_configured):
    """The `cfg` fixture plus a strategies block — configured to short or
    not, matching test_short_rails.py's `_short_cfg` shape. Carries enough of
    xsmom's own params (rank_lookback_bars, skip_bars) that
    strategies.max_lookback_bars can compute a required_lookback for it
    without KeyError — main.run_cycle() reaches that call for EVERY
    strategy `_config_map` names, enabled or not, so a config that is
    "just enough to be refused by preflight" still has to be a config the
    rest of the cycle could survive touching, in case this test's own
    abort assertion is what is broken."""
    local = copy.deepcopy(cfg)
    local["strategies"] = {"xsmom": {"enabled": shorting_configured,
                                     "priority": 1,
                                     "rank_lookback_bars": 2, "skip_bars": 0,
                                     "buy_top_fraction": 0.25,
                                     "exit_below_fraction": 0.50,
                                     "short_bottom_fraction": 0.25}}
    return local


class _ShortingAwareBroker(FakeCycleBroker):
    """FakeCycleBroker's account() is a fixed dict with no shorting flag at
    all — every test that needs to say whether THIS broker can short needs
    its own account(), the same way a real Alpaca account differs by
    account, not by code path."""

    def __init__(self, bars, shorting_enabled):
        super().__init__(bars)
        self._shorting_enabled = shorting_enabled

    def account(self):
        a = super().account()
        a["shorting_enabled"] = self._shorting_enabled
        return a


def test_the_cycle_aborts_when_the_broker_forbids_a_configured_short(
        tmp_path, monkeypatch, cfg):
    """End-to-end proof (mirrors test_preflight.py's
    test_cycle_aborts_on_preflight_failure): a strategy configured to short,
    on a broker that cannot, must trade NOTHING and must log the same
    preflight_failure event a cfg-only failure would — this abort happens
    one step later in the cycle (after Broker(cfg)), but it must look
    identical from the outside."""
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    local = _shorting_cfg(cfg, shorting_configured=True)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(local, f)
    broker = _ShortingAwareBroker(make_bars(CYCLE_CLOSES), shorting_enabled=False)
    monkeypatch.setattr(main, "Broker", lambda c: broker)
    main.run_cycle()
    assert broker.submitted == []
    recs = Ledger(cfg["memory"]["ledger_path"]).all_records()
    assert any(r.get("event") == "preflight_failure" for r in recs)


def test_the_cycle_still_runs_when_the_broker_allows_a_configured_short(
        tmp_path, monkeypatch, cfg):
    """The twin: the same configured strategy, but a broker that CAN short,
    must not be aborted by this check — proves the rail fires on the
    account's own capability, not merely on 'a strategy is configured to
    short'."""
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    local = _shorting_cfg(cfg, shorting_configured=True)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(local, f)
    broker = _ShortingAwareBroker(make_bars(CYCLE_CLOSES), shorting_enabled=True)
    monkeypatch.setattr(main, "Broker", lambda c: broker)
    started = main._bootstrap_cycle()
    assert started is not None


def test_the_cycle_still_runs_when_no_strategy_is_configured_to_short(
        tmp_path, monkeypatch, cfg):
    """The other twin: a broker that cannot short is only a problem for a
    strategy that asks it to — nothing shorts yet in this repo
    (ENTRY_ACTIONS includes "short", but no strategy emits it), so this must
    stay completely inert, the same way the guard itself does in
    test_short_rails.py."""
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3
    local = _shorting_cfg(cfg, shorting_configured=False)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(local, f)
    broker = _ShortingAwareBroker(make_bars(CYCLE_CLOSES), shorting_enabled=False)
    monkeypatch.setattr(main, "Broker", lambda c: broker)
    started = main._bootstrap_cycle()
    assert started is not None


# ---------------------------------------------------------------------------
# Fix 3: judge_model.apply must enforce invariant #2 (the LLM may only VETO
# or DOWNSIZE, never enlarge or reverse) through the SAME clamp llm.py uses
# for a real verdict (`llm._clamp_scale`), not a bare `int(qty * scale)` that
# trusts whatever the calibration histogram holds. Latent today — the
# histogram is built from already-clamped LIVE judgments, so every scale it
# has ever held was in [0, 1] — which is the reason this is cheap to fix,
# not a reason it can be skipped: nothing about the histogram FORMAT
# guarantees that stays true.
# ---------------------------------------------------------------------------

def _rigged_cal_file(tmp_path, monkeypatch, histogram, veto_rate=0.0):
    """A calibration file whose histogram holds whatever a hand-edited file
    or a future writer might produce — these tests care what apply() does
    with an out-of-range scale, not how one could arise from the real
    calibration pipeline (scripts/calibrate_judge.py)."""
    import judge_model
    path = tmp_path / "judge_calibration.json"
    cal = {"n_judged_buys": 146, "min_sample": 50, "veto_rate": veto_rate,
          "scale_histogram": histogram}
    path.write_text(json.dumps(cal))
    monkeypatch.setattr(judge_model, "_CAL_PATH", str(path))
    judge_model._cache = None
    return path


def _judge_cfg(salt="fix3"):
    return {"backtest": {"judge_model": {"enabled": True, "salt": salt}}}


def test_a_calibration_scale_above_one_is_clamped_to_full_size(
        tmp_path, monkeypatch):
    """The failure this fix closes: every draw lands in the single 1.5
    bucket (corrupt file, hand edit, future format) — a bare
    int(qty * scale) would ENLARGE a 10-share intent to 15. The clamp must
    cap it at scale 1.0, same as the live judge would be forced to."""
    import judge_model
    _rigged_cal_file(tmp_path, monkeypatch, {"1.5": 100})
    out = judge_model.apply(10, "AAPL", "2025-01-01", _judge_cfg())
    assert out == 10


def test_a_normal_downsize_scale_is_unaffected_by_the_clamp(
        tmp_path, monkeypatch):
    """The twin: a genuine in-range downsize must pass through the clamp
    untouched — proves the fix caps a bad value without also capping a
    legitimate one."""
    import judge_model
    _rigged_cal_file(tmp_path, monkeypatch, {"0.5": 100})
    out = judge_model.apply(10, "AAPL", "2025-01-01", _judge_cfg())
    assert out == 5


def test_a_negative_calibration_scale_cannot_reverse_the_trade(
        tmp_path, monkeypatch):
    """The other half of invariant #2: an un-clamped negative scale would
    make qty * scale negative — a 'downsize' that flips a buy into a
    negative-quantity sell instead of merely skipping it. Must clamp to 0.0,
    the same floor a real veto produces."""
    import judge_model
    _rigged_cal_file(tmp_path, monkeypatch, {"-0.5": 100})
    out = judge_model.apply(10, "AAPL", "2025-01-01", _judge_cfg())
    assert out == 0


def test_a_genuine_veto_still_zeroes_the_trade_exactly_as_before(
        tmp_path, monkeypatch):
    """The twin: an in-range veto (scale 0.0, drawn via veto_rate) must still
    be honoured exactly as it always was — the clamp is a ceiling/floor on a
    bad value, not a reinterpretation of a legitimate zero."""
    import judge_model
    _rigged_cal_file(tmp_path, monkeypatch, {"0.5": 100}, veto_rate=1.0)
    out = judge_model.apply(10, "AAPL", "2025-01-01", _judge_cfg())
    assert out == 0
