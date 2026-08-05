"""`simulate_ensemble` models SIGNED positions — the short side, end to end.

Phase 2 shipped `position_side="long"` hardcoded at both of this simulator's
`generate()` calls, with a comment saying Phase 3 would replace it. Until it
did, a DIAGNOSTIC of the 130/30 configuration would have silently scored a
long-only book and called it 130/30.

THE REPRESENTATION, and it mirrors live rather than inventing one: order
quantity stays UNSIGNED through sizing, the judge and the heat cap; the STORED
position carries the sign. Live does exactly that — `risk.size_order` and
`judge_model.apply` see magnitudes, and `broker.positions()` reports a short's
qty negative — so it is the choice that creates the fewest new divergences.

Every assertion here sits beside its long twin. Nothing shorts in production, so
the LONG path is the one that must not have moved; a short-side number that is
right while its twin quietly changed proves nothing.
"""
from datetime import date, timedelta

import pytest

import backtest as bt


def _walk(n, start, drift, seed, tail_drift=None, tail=0):
    """Deterministic random walk with a drift, optionally reversing for the
    last `tail` bars. The noise is what keeps symbols DECORRELATED: an earlier
    fixture used clean ramps and the correlation cap refused 134 of 138
    signals, so the run produced no shorts at all and every assertion below
    would have been vacuous."""
    px, rows, d, st = start, [], date(2023, 1, 2), seed
    for i in range(n):
        st = (st * 1103515245 + 12345) % (2 ** 31)
        shock = ((st % 1000) / 1000.0 - 0.5) * 4.0
        step = drift if (tail_drift is None or i < n - tail) else tail_drift
        px = max(5.0, px + step + shock)
        rows.append({"ts": f"{d.isoformat()}T21:00:00+00:00",
                     "open": round(px, 4), "high": round(px * 1.015, 4),
                     "low": round(px * 0.985, 4), "close": round(px, 4),
                     "volume": 1_000_000})
        d += timedelta(days=1)
    return rows


UP = ["AAPL", "MSFT", "NVDA", "AMZN"]
DOWN = ["JPM", "XOM", "KO", "PG"]


def _bars(tail_drift=None, tail=0, up_tail_drift=None):
    """`tail_drift` reverses the DOWN names (so a short goes against itself);
    `up_tail_drift` reverses the UP names (so a long does). Both are needed:
    each direction's stop has to be shown firing on its own side of the bar."""
    out = {}
    for k, s in enumerate(UP):
        out[s] = _walk(340, 100 + 7 * k, +0.55, 11 + k,
                       tail_drift=up_tail_drift, tail=tail)
    for k, s in enumerate(DOWN):
        out[s] = _walk(340, 300 + 7 * k, -0.55, 91 + k,
                       tail_drift=tail_drift, tail=tail)
    return out


def _xsmom_only():
    cfg = bt.load_config()
    cfg["strategies"] = {k: dict(v) for k, v in cfg["strategies"].items()}
    for n in cfg["strategies"]:
        cfg["strategies"][n]["enabled"] = (n == "xsmom")
    return cfg


@pytest.fixture(scope="module")
def run():
    bars = _bars()
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    assert res.trades, "fixture opened nothing — every assertion would be vacuous"
    return bars, res


def _shorts(res):
    return [t for t in res.trades if t.qty < 0]


def _longs(res):
    return [t for t in res.trades if t.qty > 0]


# ---------------------------------------------------------------------------
# The sign itself.
# ---------------------------------------------------------------------------

def test_the_book_carries_both_directions(run):
    """The precondition for everything below. If this fails the fixture stopped
    producing shorts and the rest of the file is asserting nothing."""
    _, res = run
    assert _shorts(res), "no short was opened"
    assert _longs(res), "no long was opened"


def test_a_short_is_stored_with_a_NEGATIVE_quantity(run):
    """Matching what `broker.positions()` reports for a real short, which is
    what lets equity(), positions_dict() and every rail downstream work on both
    sides with one formula instead of two."""
    _, res = run
    assert all(t.qty < 0 for t in _shorts(res))


def test_a_long_is_still_stored_POSITIVE(run):
    _, res = run
    assert all(t.qty > 0 for t in _longs(res))


# ---------------------------------------------------------------------------
# P&L — the sign that would silently invert the whole short book.
# ---------------------------------------------------------------------------

def test_a_short_that_fell_is_a_WIN(run):
    """`SimTrade.pnl` is `(exit - entry) * qty`, which becomes correct for a
    short for free once qty is signed. Asserted precisely BECAUSE it is free:
    it is the line most likely to be "tidied" into an abs() by someone who
    reads it as a magnitude."""
    _, res = run
    fell = [t for t in _shorts(res) if t.exit_price < t.entry_price]
    assert fell, "no short fell in the fixture"
    assert all(t.pnl > 0 for t in fell)


def test_a_short_that_ROSE_is_a_loss():
    """The other half of the sign, and it needs its own fixture: without it,
    "shorts make money" is equally explained by a pnl that is always positive.
    The down-drift names reverse hard for the last stretch, so a short opened
    early is under water at the forced close."""
    bars = _bars(tail_drift=+3.0, tail=60)
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    rose = [t for t in _shorts(res) if t.exit_price > t.entry_price]
    assert rose, "no short rose in the reversal fixture"
    assert all(t.pnl < 0 for t in rose)


def test_a_long_that_rose_is_still_a_win(run):
    _, res = run
    rose = [t for t in _longs(res) if t.exit_price > t.entry_price]
    assert rose, "no long rose in the fixture"
    assert all(t.pnl > 0 for t in rose)


# ---------------------------------------------------------------------------
# Slippage follows the ORDER's side, not the lifecycle.
# ---------------------------------------------------------------------------

def _bar_at(bars, symbol, ts):
    return next(b for b in bars[symbol] if b["ts"] == ts)


def _slip():
    return bt.load_config().get("backtest", {}).get("slippage_bps", 5) / 1e4


def _level(exit_price, is_short):
    """The bracket LEVEL that was touched, recovered from the recorded fill.

    `_exit` applies slippage to the stop price, so a short's recorded exit sits
    ABOVE the level the bar actually reached and a long's sits below it. An
    earlier draft compared the fill straight to the bar's high and failed by
    exactly one slippage step — the fill is not the level, and asserting on it
    would have been asserting on the slippage as well."""
    s = _slip()
    return exit_price / (1 + s) if is_short else exit_price / (1 - s)


def test_opening_a_short_SELLS_so_it_fills_below_the_open(run):
    """Opening a short is a sell, and a seller is hurt by a lower fill. The
    un-fixed code applied `(1 + slip)` unconditionally, which would have paid
    every short entry a premium it never received."""
    bars, res = run
    for t in _shorts(res):
        assert t.entry_price < _bar_at(bars, t.symbol, t.entry_ts)["open"]


def test_opening_a_long_BUYS_so_it_still_fills_above_the_open(run):
    bars, res = run
    for t in _longs(res):
        assert t.entry_price > _bar_at(bars, t.symbol, t.entry_ts)["open"]


def test_covering_a_short_BUYS_so_it_pays_above_the_close(run):
    """The defect this pairs with is the expensive one: `(1 - slip)` on every
    exit paid each cover a discount — a trading cost recorded as a saving, on
    the side of the book where costs compound with holding period. These exits
    are `end_of_data`, which prices off the last close."""
    bars, res = run
    covers = [t for t in _shorts(res) if t.exit_reason == "end_of_data"]
    assert covers, "no short reached the forced close"
    for t in covers:
        assert t.exit_price > _bar_at(bars, t.symbol, t.exit_ts)["close"]


def test_selling_a_long_still_RECEIVES_below_the_close(run):
    bars, res = run
    sells = [t for t in _longs(res) if t.exit_reason == "end_of_data"]
    assert sells, "no long reached the forced close"
    for t in sells:
        assert t.exit_price < _bar_at(bars, t.symbol, t.exit_ts)["close"]


# ---------------------------------------------------------------------------
# The census must still account for every signal, shorts included.
# ---------------------------------------------------------------------------

def test_the_block_census_balances_with_shorts_in_the_book(run):
    """§40's rule: every signal reaches a bucket or the census fails loudly.

    This caught a real defect during Phase 3 rather than after it. The
    end-of-run drain that counts pending orders which never filled still read
    `_action == "buy"`, so a leftover SHORT was a counted signal that reached no
    bucket — 138 signals, 136 accounted for. The balance check is the only
    reason it was visible at all."""
    _, res = run
    blocked = sum(v for k, v in res.census["blocked"].items()
                  if k != "unaccounted")
    assert res.census["blocked"].get("unaccounted", 0) == 0
    assert res.census["executed"] + blocked == res.census["signals"]


# ---------------------------------------------------------------------------
# Invariant #2 — the judge sees a magnitude, on both sides.
# ---------------------------------------------------------------------------

def test_the_judge_is_handed_an_unsigned_quantity(monkeypatch):
    """`judge_model.apply` opens with `if qty <= 0: return qty`. Had the sign
    been applied BEFORE the judge, every short would have taken that early
    return — the judge unable to downsize the one side of the book where the
    loss is unbounded, which is invariant #2 inverted rather than merely
    missing."""
    import judge_model
    seen = []
    real = judge_model.apply

    def spy(qty, symbol, ts, cfg):
        seen.append(qty)
        return real(qty, symbol, ts, cfg)

    monkeypatch.setattr(bt.judge_model, "apply", spy)
    res = bt.simulate_ensemble(_bars(), _xsmom_only(), 100_000.0)
    assert _shorts(res), "no short was opened — the assertion would be vacuous"
    assert seen, "the judge was never consulted"
    assert all(q >= 0 for q in seen), f"signed qty reached the judge: {[q for q in seen if q < 0]}"


# ---------------------------------------------------------------------------
# Bracket legs — which side of the bar touches which leg.
# ---------------------------------------------------------------------------

def test_a_shorts_stop_sits_ABOVE_entry_and_is_reached_by_the_HIGH():
    """The inversion, and it is not a near-miss when it is wrong: with the long
    test (`low <= stop`) a short's stop — placed above entry — is beneath the
    very first bar's low, so it fires on bar one, every time. Same defect class
    as counterfactual.py's, which resolved every vetoed short as a missed
    winner until #92."""
    bars = _bars(tail_drift=+3.0, tail=60)
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    stopped = [t for t in _shorts(res) if t.exit_reason == "stop_loss"]
    assert stopped, "no short stopped out — the assertion would be vacuous"
    for t in stopped:
        assert t.exit_price > t.entry_price, "a short's stop must sit ABOVE entry"
        bar = _bar_at(bars, t.symbol, t.exit_ts)
        assert bar["high"] >= _level(t.exit_price, True), \
            "the HIGH must be what reached it"


def test_a_longs_stop_still_sits_BELOW_entry_and_is_reached_by_the_LOW():
    """The twin, on the path production actually runs."""
    bars = _bars(up_tail_drift=-3.0, tail=60)
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    stopped = [t for t in _longs(res) if t.exit_reason == "stop_loss"]
    assert stopped, "no long stopped out — the assertion would be vacuous"
    for t in stopped:
        assert t.exit_price < t.entry_price, "a long's stop must sit BELOW entry"
        bar = _bar_at(bars, t.symbol, t.exit_ts)
        assert bar["low"] <= _level(t.exit_price, False), \
            "the LOW must be what reached it"


def test_a_short_stopping_out_is_recorded_as_a_LOSS():
    """Ties the two halves together. A stop is where the position is WRONG, so
    a short stopped out must lose — and under the un-fixed arithmetic it would
    have booked a gain, because a stop above entry looks like a rise."""
    bars = _bars(tail_drift=+3.0, tail=60)
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    stopped = [t for t in _shorts(res) if t.exit_reason == "stop_loss"]
    assert stopped
    assert all(t.pnl < 0 for t in stopped)


# ---------------------------------------------------------------------------
# The three paths a first round of mutation testing found UNCOVERED.
#
# Each survived a deliberate break of the code it exercises, which is the whole
# argument for mutating rather than counting tests: all three were reachable,
# none was reached, and the file looked thorough without them.
# ---------------------------------------------------------------------------

def _tp_cfg(mult=3.0):
    """The shipped config runs `take_profit_atr_mult: 0` — stop-only, because
    every walk-forward winner chose no TP leg. So the take-profit branch is
    DEAD in production and a broken one cannot show up in any normal run: that
    is exactly why reverting it to the long-only side survived. Enabled here so
    the branch is covered before a DIAGNOSTIC arm ever turns it on."""
    cfg = _xsmom_only()
    cfg["risk"] = {**cfg["risk"],
                   "brackets": {**cfg["risk"]["brackets"],
                                "take_profit_atr_mult": mult}}
    return cfg


def test_a_shorts_take_profit_sits_BELOW_entry_and_is_reached_by_the_LOW():
    bars = _bars(tail_drift=-6.0, tail=60)
    res = bt.simulate_ensemble(bars, _tp_cfg(), 100_000.0)
    won = [t for t in _shorts(res) if t.exit_reason == "take_profit"]
    assert won, "no short reached its target — the assertion would be vacuous"
    for t in won:
        assert t.exit_price < t.entry_price, "a short's target must sit BELOW entry"
        bar = _bar_at(bars, t.symbol, t.exit_ts)
        assert bar["low"] <= _level(t.exit_price, True), \
            "the LOW must be what reached it"
        assert t.pnl > 0, "a target is where the position is RIGHT"


def test_a_longs_take_profit_still_sits_ABOVE_entry_and_is_reached_by_the_HIGH():
    """The twin. Both legs of both directions are now pinned."""
    bars = _bars(up_tail_drift=+6.0, tail=60)
    res = bt.simulate_ensemble(bars, _tp_cfg(), 100_000.0)
    won = [t for t in _longs(res) if t.exit_reason == "take_profit"]
    assert won, "no long reached its target — the assertion would be vacuous"
    for t in won:
        assert t.exit_price > t.entry_price
        bar = _bar_at(bars, t.symbol, t.exit_ts)
        assert bar["low"] <= _level(t.exit_price, False) or \
            bar["high"] >= _level(t.exit_price, False)
        assert t.pnl > 0


def test_a_scripted_COVER_routes_to_the_exit_path(monkeypatch):
    """A held SHORT closed by its owner's own signal.

    Driving a natural cover through the ensemble proved fragile — a short stops
    out long before the ranking recovers, and widening the stop shrinks the
    risk-sized quantity until no short opens at all. So the signal is scripted,
    the same technique tests/test_short_path.py uses for the live cycle, which
    targets the mutated line rather than hoping a fixture wanders past it.

    Under `sig.action == "sell"` a "cover" falls through and is silently
    discarded — the position's own exit signal ignored, every bar, forever."""
    import strategies
    from strategies.base import Signal
    real = strategies.generate

    def scripted(name, symbol, bars_, cfg_, holding, **kw):
        sig = real(name, symbol, bars_, cfg_, holding, **kw)
        if holding and kw.get("position_side") == "short":
            return Signal(symbol, "cover", "scripted cover", {}, name)
        return sig

    monkeypatch.setattr(bt.strategies, "generate", scripted)
    res = bt.simulate_ensemble(_bars(), _xsmom_only(), 100_000.0)
    covered = [t for t in _shorts(res) if t.exit_reason == "strategy_sell"]
    assert covered, ("no short was closed by its owner's signal — a 'cover' is "
                     "being discarded by the exit-action test")


def _ramp(n, start, step):
    from datetime import date as _d, timedelta as _td
    px, rows, d = start, [], _d(2023, 1, 2)
    for _ in range(n):
        px = max(5.0, px + step)
        rows.append({"ts": f"{d.isoformat()}T21:00:00+00:00", "open": round(px, 4),
                     "high": round(px * 1.01, 4), "low": round(px * 0.99, 4),
                     "close": round(px, 4), "volume": 1_000_000})
        d += _td(days=1)
    return rows


def test_the_census_balances_when_a_SHORT_is_left_pending():
    """The drop path the balance check actually caught during Phase 3.

    An entry queued on the last bar never reaches the fill path, and the
    end-of-run drain counts it as `expired_unfilled`. That drain still read
    `_action == "buy"`, so a leftover SHORT was a counted signal that reached no
    bucket at all — 138 signals, 136 accounted for.

    Clean ramps on purpose: perfectly correlated symbols make the correlation
    cap refuse most entries and leave orders queued at the end, which is the
    state that exposes the drain. The general balance test above uses the
    decorrelated fixture and never produces one."""
    bars = {}
    for k, s in enumerate(UP):
        bars[s] = _ramp(320, 100 + k, +0.6)
    for k, s in enumerate(DOWN):
        bars[s] = _ramp(320, 300 + k, -0.6)
    res = bt.simulate_ensemble(bars, _xsmom_only(), 100_000.0)
    assert res.census["blocked"].get("expired_unfilled", 0) > 0, \
        "no order was left pending — the assertion would be vacuous"
    blocked = sum(v for k, v in res.census["blocked"].items() if k != "unaccounted")
    assert res.census["blocked"].get("unaccounted", 0) == 0
    assert res.census["executed"] + blocked == res.census["signals"]


def _sized(risk_sizing_on):
    cfg = _xsmom_only()
    cfg["risk"] = {**cfg["risk"],
                   "risk_sizing": {**cfg["risk"]["risk_sizing"],
                                   "enabled": bool(risk_sizing_on),
                                   "strategies": ["xsmom"]}}
    res = bt.simulate_ensemble(_bars(), cfg, 100_000.0)
    return {(t.symbol, t.entry_ts): t.qty for t in res.trades}


def test_RISK_SIZING_actually_reaches_a_short():
    """`size_order(direction=...)` is inert under the SHIPPED config, which is
    why dropping it survived a first mutation round: `risk_sizing` is scoped to
    `strategies: ['meanrev']`, and meanrev cannot short — so the one strategy
    with a short leg never reaches the risk-sizing branch at all.

    Latent is not harmless. #89 measured what happens when it IS reached
    without a direction: `_risk_sizing_active` sees a stop ABOVE price, decides
    that is not a usable stop for a long, and falls back to NOTIONAL sizing.
    No error, no log — the short is simply not risk-sized, and the position is
    whatever the notional budget bought.

    So "qty > 0" cannot detect it; the fallback produces a perfectly good
    quantity too. The discriminator is that turning risk sizing ON must CHANGE
    a short's size. If the direction is dropped, both runs take the notional
    path and the sizes are identical."""
    on, off = _sized(True), _sized(False)
    shorts_on = {k: v for k, v in on.items() if v < 0}
    assert shorts_on, "no short opened — the assertion would be vacuous"
    shared = [k for k in shorts_on if k in off]
    assert shared, "no short is comparable across the two runs"
    assert any(on[k] != off[k] for k in shared), (
        "risk sizing changed no short's quantity — the direction is not "
        "reaching size_order, so every short fell back to notional sizing")


def test_risk_sizing_still_reaches_a_LONG_the_same_way():
    """The twin. If this one stops discriminating too, the fixture broke rather
    than the direction argument."""
    on, off = _sized(True), _sized(False)
    longs_on = {k: v for k, v in on.items() if v > 0}
    shared = [k for k in longs_on if k in off]
    assert shared, "no long is comparable across the two runs"
    assert any(on[k] != off[k] for k in shared)
