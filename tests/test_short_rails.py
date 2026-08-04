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
    """The paired half, varying only the dimension the rail measures: same
    long + short book as above, sized so gross ($40k) stays under the $50k
    cap. A rail that blocked everything would also pass the test above, and
    would be just as broken."""
    cfg = _cfg(regime_exposure={"enabled": True, "down_max_gross_pct": 50})
    positions = {"AAPL": {"market_value": 20_000.0},
                 "TSLA": {"market_value": -20_000.0}}
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


def test_the_short_under_the_cap_is_permitted():
    """The paired half: the same short, sized so $9,000 + $500 stays under
    the $10,000 cap. Proves the rail measures magnitude without over-firing
    on a short that is legitimately within bounds."""
    cfg = _cfg(max_position_pct=10.0)
    positions = {"TSLA": {"market_value": -9_000.0}}
    risk.pure_checks("buy", "TSLA", 5, 100.0, ACCOUNT, positions, cfg)


# ---- shorts are ENTRIES; covers are EXITS ----

def test_a_short_passes_through_the_entry_rails():
    """A short is an entry and must meet every entry rail. Gating the entry
    block on `action == "buy"` would let shorts bypass the concentration cap,
    the drawdown breaker and the slot limits entirely."""
    cfg = _cfg(max_position_pct=10.0)
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 200, 100.0, ACCOUNT, {}, cfg)
    assert e.value.rail == "position_cap"


def test_a_new_short_under_the_cap_is_permitted():
    """The paired half: a short opening a brand-new position, sized so its
    $5,000 order value stays under the $10,000 cap on $100k equity. Proves
    the entry gate subjects a short to the concentration rail rather than
    blocking every short outright."""
    cfg = _cfg(max_position_pct=10.0)
    risk.pure_checks("short", "TSLA", 50, 100.0, ACCOUNT, {}, cfg)


def test_a_cover_with_no_position_is_a_desync():
    """The mirror of the sell guard. Covering something you are not short is a
    state desync, and sending it would OPEN a long."""
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, {}, _cfg())
    assert e.value.rail == "desync_cover"


def test_a_cover_against_a_position_held_long_is_also_a_desync():
    """Presence alone is not enough: TSLA IS in `positions` here, but held
    LONG. `symbol not in positions` would pass this straight through, and the
    cover would submit a BUY that ADDS TO the long — the guard has to check
    the SIGN of the existing position, not merely whether one is on file."""
    positions = {"TSLA": {"market_value": 5_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())
    assert e.value.rail == "desync_cover"


def test_a_cover_against_a_genuine_short_is_allowed():
    """The paired half — proves the guard passes a REAL short rather than
    merely 'symbol is present in positions', which the long case above would
    also have satisfied under a presence-only check."""
    positions = {"TSLA": {"market_value": -5_000.0}}
    risk.pure_checks("cover", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())


def test_a_sell_against_a_position_held_short_is_a_desync():
    """The mirror defect on the sell side: TSLA IS in `positions` here, but
    held SHORT. `symbol not in positions` would pass this straight through,
    and the sell would submit a SELL that ADDS TO the short."""
    positions = {"TSLA": {"market_value": -5_000.0}}
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("sell", "TSLA", 10, 100.0, ACCOUNT, positions, _cfg())
    assert e.value.rail == "desync_sell"


# ---- the net-exposure band ----

BAND = {"net_exposure_pct": {"min": 80, "max": 120}}


def test_a_short_that_would_push_net_below_the_floor_is_refused():
    positions = {"AAPL": {"market_value": 85_000.0}}      # net 85%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "TSLA", 100, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # -10% -> net 75%
    assert e.value.rail == "net_exposure"


def test_a_smaller_short_inside_the_band_is_allowed():
    """The paired half: same book, same direction, smaller size."""
    positions = {"AAPL": {"market_value": 85_000.0}}
    risk.pure_checks("short", "TSLA", 10, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))                         # -1% -> net 84%


def test_a_buy_that_would_push_net_above_the_ceiling_is_refused():
    positions = {"AAPL": {"market_value": 118_000.0}}      # net 118%
    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "MSFT", 50, 100.0, ACCOUNT, positions,
                         _cfg(**BAND))                     # +5% -> net 123%
    assert e.value.rail == "net_exposure"


def test_a_smaller_buy_inside_the_band_is_allowed():
    """The paired half missing from the ceiling case above: same book, same
    direction, smaller size. Without this, the ceiling test could be tripping
    on any threshold at all and nothing would tell you the rail also lets a
    compliant buy through."""
    positions = {"AAPL": {"market_value": 118_000.0}}
    risk.pure_checks("buy", "MSFT", 10, 100.0, ACCOUNT, positions,
                     _cfg(**BAND))                         # +1% -> net 119%


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
        "TSLA", 10, stop_price=110.0, side="short", entry_price=100.0)
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


def test_a_short_bracket_accepts_a_stop_ABOVE_the_entry():
    """The paired half of the short-stop refusal above — a correctly-placed
    short stop must not be caught by the same check that blocks a bad one."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order(
        "TSLA", 10, stop_price=110.0, side="short", entry_price=100.0)
    assert fake.requests[0].side == OrderSide.SELL


def test_a_long_bracket_accepts_a_stop_BELOW_the_entry():
    """The paired half of the long-stop refusal above."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order(
        "AAPL", 10, stop_price=90.0, side="buy", entry_price=100.0)
    assert fake.requests[0].side == OrderSide.BUY


def test_the_geometry_check_is_skipped_for_a_long_when_entry_price_is_omitted():
    """`entry_price` stays OPTIONAL for "buy" — src/main.py's only live call
    site never supplies one, and Phase 1 promises byte-identical live
    behaviour. A long submitted without it must still go through, unchecked,
    exactly as it does today."""
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order("AAPL", 10, stop_price=90.0)
    assert len(fake.requests) == 1


def test_a_short_bracket_without_an_entry_price_is_refused():
    """`entry_price` is REQUIRED for "short" — this is the hazard the task
    exists to eliminate. Without it there is nothing to check the stop
    against, and an unchecked short's loss is unbounded."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="entry_price"):
        _broker_with(fake).bracket_market_order(
            "TSLA", 10, stop_price=110.0, side="short")
    assert fake.requests == []


def test_a_short_bracket_WITH_an_entry_price_is_still_accepted():
    """The paired half — requiring entry_price for a short must not become
    refusing every short."""
    from alpaca.trading.enums import OrderSide
    fake = _FakeTrading()
    _broker_with(fake).bracket_market_order(
        "TSLA", 10, stop_price=110.0, side="short", entry_price=100.0)
    assert fake.requests[0].side == OrderSide.SELL


def test_a_short_with_entry_price_ZERO_is_refused():
    """0.0 passes `entry_price is not None`, so without this check a failed
    quote lookup that defaults to zero would slip a short through with the
    geometry check trivially satisfied for every stop — validated-looking,
    unvalidated in fact."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="non-positive"):
        _broker_with(fake).bracket_market_order(
            "TSLA", 10, stop_price=110.0, side="short", entry_price=0.0)
    assert fake.requests == []


def test_a_long_with_entry_price_ZERO_is_also_refused():
    """The mirror: 0.0 would make the long's `stop_price >= entry_price`
    check always true, spuriously refusing every otherwise-good long stop.
    That failure mode fails safe, but it is still wrong — a caller passing
    a placeholder zero should get a clear error, not an inscrutable one."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="non-positive"):
        _broker_with(fake).bracket_market_order(
            "AAPL", 10, stop_price=90.0, side="buy", entry_price=0.0)
    assert fake.requests == []


def test_a_short_stop_EQUAL_to_entry_is_refused():
    """The boundary itself. Every other geometry test sits 10 away from
    entry, which cannot tell <= from < — a stop that exactly equals entry
    triggers on any move and must be refused, not waved through."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="above"):
        _broker_with(fake).bracket_market_order(
            "TSLA", 10, stop_price=100.0, side="short", entry_price=100.0)
    assert fake.requests == []


def test_a_long_stop_EQUAL_to_entry_is_refused():
    """The mirror boundary, on the long side."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="below"):
        _broker_with(fake).bracket_market_order(
            "AAPL", 10, stop_price=100.0, side="buy", entry_price=100.0)
    assert fake.requests == []


def test_a_bracket_refuses_an_unknown_side():
    """"sell" used to be accepted here as a short alias and no longer is —
    this also covers any other typo. A function whose entire job is picking
    the side of a directional bet must not fall through to a silent BUY."""
    fake = _FakeTrading()
    with pytest.raises(ValueError, match="sell"):
        _broker_with(fake).bracket_market_order(
            "AAPL", 10, stop_price=90.0, side="sell")
    assert fake.requests == []


def test_a_short_brackets_return_value_reports_its_own_side_as_short():
    """The request sent to Alpaca is only half of it — the return dict is what
    main.py's ledger and trade-plan narration read. A hardcoded "buy" here
    would send the correct order while every downstream record still called
    it a long."""
    fake = _FakeTrading()
    result = _broker_with(fake).bracket_market_order(
        "TSLA", 10, stop_price=110.0, side="short", entry_price=100.0)
    assert result["side"] == "short"


def test_a_long_brackets_return_value_still_reports_its_side_as_buy():
    """The paired half — the default return shape must not move."""
    fake = _FakeTrading()
    result = _broker_with(fake).bracket_market_order("AAPL", 10, stop_price=90.0)
    assert result["side"] == "buy"


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


def test_a_broker_that_explicitly_reports_shorting_disabled_reads_as_NOT_shortable():
    """The paired half of the capability test: `shorting_enabled` present and
    explicitly False must read the same as absent, not as some third state."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"; shorting_enabled = False; multiplier = "1"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["shorting_enabled"] is False
    assert a["multiplier"] == 1.0


def test_multiplier_None_falls_back_to_one():
    """`multiplier` present but explicitly None (distinct from the attribute
    being absent entirely) must not become `float(None)`, which raises — it
    must fall back the same as a missing field."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"; shorting_enabled = True; multiplier = None

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["multiplier"] == 1.0


def test_multiplier_string_zero_falls_back_to_one():
    """Alpaca reports numeric account fields as strings (see `equity`/`cash`
    fixtures throughout this file), so a truthy-but-zero-valued string is a
    real shape, not a contrived one. The naive `float(raw or 1)` checks
    truthiness of the RAW value — `"0" or 1` evaluates to `"0"`, so that
    version reads this as 0.0. The parse-first fix checks the PARSED number
    instead, so a string "0" lands on the same 1.0 safe default as a real
    zero would."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"; shorting_enabled = True; multiplier = "0"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["multiplier"] == 1.0


def test_multiplier_negative_falls_back_to_one():
    """A negative multiplier is not a real leverage ratio. Same class of
    trap as string "0": `"-2" or 1` is also truthy, so only a check on the
    parsed number catches this."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"; shorting_enabled = True; multiplier = "-2"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["multiplier"] == 1.0


def test_multiplier_unparseable_falls_back_to_one():
    """A garbled value (partial write, API change, anything) must not raise
    ValueError out of account() — the whole cycle would abort on a field
    nothing else depends on being precise."""
    import broker as broker_mod

    class _Acct:
        equity = "100000"; cash = "50000"; last_equity = "99000"
        buying_power = "100000"; shorting_enabled = True; multiplier = "abc"

    b = object.__new__(broker_mod.Broker)
    b.trading = type("T", (), {"get_account": lambda self: _Acct()})()
    a = b.account()
    assert a["multiplier"] == 1.0


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
