"""§54 — what actually happens when `reclaim` and the `xsmom` short leg are on
together, and why the rail everyone points at is not what prevents it.

WHY THIS FILE EXISTS. §53's third failure mode recorded the one thing that
diagnostic did not test: `reclaim` was disabled in all twelve arms, so nothing
measured the interaction. `risk.py`'s own comment (corrected in PR #92, when the
"mechanically disjoint" claim was found to be false) says:

    So this rail is not defence in depth behind an upstream guarantee. It is
    the ONLY thing preventing the conflict, and whichever strategy reaches the
    name first (xsmom at priority 3, ahead of reclaim at 6) takes it while the
    other is REFUSED HERE.

THE SECOND HALF OF THAT SENTENCE IS ALSO WRONG, and this file is what proves it.
The other strategy is not refused at the rail. **It is never consulted.**

    sim  — `backtest.py:1157-1181`: a held symbol routes to `pos["owner"]` and
           the branch `continue`s. The per-strategy entry loop below it never
           runs for a name that is already held.
    live — `main.py:1615-1652`: the identical structure, `continue` at 1652.
    both — the entry loop `break`s on the first strategy to claim a FLAT symbol
           (`backtest.py:1266`, `main.py:1742`), so two strategies cannot queue
           opposite orders on one name in one cycle either.
    sim  — and if a queued order somehow outlives the bar it was queued on, the
           fill stage drops it as `already_held` (`backtest.py:939-944`) BEFORE
           `pure_checks` is reached.

So OWNERSHIP ROUTING is what prevents a long and a short in the same name.
`direction_conflict` is a last-gate backstop for a caller that did not come
through the cycle — which is a real thing to have (`pre_trade_checks` makes the
same argument for `halt` in its own comment) but is not what the rail's comment
claims, and is not something a backtest census can ever count.

This matters because §54 was designed to read `census["blocked"]
["direction_conflict"]` as its headline measurement. That number is structurally
zero. Reading it as "the collision is rare on this data" would have been a
finding about nothing — the exact reason the tests were sequenced before the run.

Every assertion below has a non-vacuity guard beside it. "reclaim traded
nothing" and "reclaim was correctly refused" produce identical output, and
distinguishing them is the whole job — the same trap `test_reclaim.py`'s header
names.
"""
import pytest

import backtest as bt
import risk
import strategies
from strategies.base import ENTRY_ACTIONS, Signal

from test_ensemble_sim import synth_bars


# The 23-name overlap is what makes the question real, so the fixture is built
# from it: every symbol here is in BOTH xsmom's universe (`symbols:` minus
# `etfs:`) and reclaim's (`sectors:`). Asserted, not assumed — the config is
# allowed to move and this premise is what the file rests on.
OVERLAP_PICKS = ("AAPL", "MSFT", "NVDA", "XOM", "CVX", "JNJ", "LLY", "JPM")


def _both_legs_cfg():
    """Only xsmom and reclaim enabled, both lookbacks compressed to fit a
    synthetic fixture.

    The compression is geometry, not behaviour: xsmom's shipped 231+21 and
    reclaim's 200+40 need ~1000 bars before either emits anything, and a fixture
    that long is slow enough that nobody runs it. The RATIOS are preserved so
    each strategy still means what it means.
    """
    cfg = bt.load_config()
    for name in cfg["strategies"]:
        cfg["strategies"][name]["enabled"] = False
    cfg["strategies"]["xsmom"]["enabled"] = True
    cfg["strategies"]["reclaim"]["enabled"] = True
    cfg["strategies"]["xsmom"].update(rank_lookback_bars=40, skip_bars=5)
    cfg["strategies"]["reclaim"].update(
        sector_sma_period=40, trend_sma_period=40, base_sma_period=8,
        min_days_below=10, base_slope_bars=4)
    return cfg


@pytest.fixture
def cfg():
    return _both_legs_cfg()


@pytest.fixture
def bars(cfg):
    picks = [s for s in cfg["symbols"] if s in OVERLAP_PICKS]
    assert len(picks) == len(OVERLAP_PICKS), "fixture symbols must all be core"
    xs = strategies.universe_for(cfg, "xsmom")
    rc = strategies.universe_for(cfg, "reclaim")
    assert set(picks) <= (xs & rc), (
        "fixture premise moved: every symbol must sit in BOTH universes, or "
        "'reclaim never got the name' is explained by the universe filter "
        "rather than by contention")
    return synth_bars(picks, n=400)


def _run(bars, cfg, spy_generate=None):
    """Run the ensemble, optionally with an instrumented `strategies.generate`.

    Patched on `bt.strategies` — the module object the simulator actually calls
    through — rather than on `strategies` alone, so the indirection cannot make
    a passing test vacuous.
    """
    real = strategies.generate
    if spy_generate is None:
        return bt.simulate_ensemble(bars, cfg, 100_000.0), None
    log: list = []

    def spy(name, symbol, b, c, holding, **kw):
        # The BAR, off the history slice the simulator passes (`hist` is
        # `sym_bars[sym][:i + 1]`), because the property is per-bar. Keyed on
        # symbol alone it is false and SHOULD be: a name xsmom owns, exits, and
        # reclaim later takes is correct behaviour, and an assertion that
        # forbade it would be asserting the wrong thing.
        log.append((b[-1]["ts"], symbol, name, holding))
        return real(name, symbol, b, c, holding, **kw)

    bt.strategies.generate = spy
    try:
        res = bt.simulate_ensemble(bars, cfg, 100_000.0)
    finally:
        bt.strategies.generate = real
    return res, log


# ---------------------------------------------------------------------------
# 1. Ownership routing is what prevents the collision.
# ---------------------------------------------------------------------------

def test_a_held_symbol_is_never_offered_to_a_second_strategy(bars, cfg):
    """THE STRUCTURAL PROPERTY, stated directly.

    If this holds, no second strategy can ever propose an order on a name that
    is already held — in either direction — and `direction_conflict` has nothing
    to refuse. Everything else in this file follows from it.
    """
    res, log = _run(bars, cfg, spy_generate=True)

    held_calls = [(ts, sym, name) for ts, sym, name, holding in log if holding]
    assert held_calls, "no position was ever held — the assertion would be vacuous"
    assert res.summary()["n_short_trades"] > 0, (
        "no short was ever opened, so this passes without exercising the one "
        "direction the collision is about")

    # Keyed on (bar, symbol): the claim is that ON ANY GIVEN BAR a held name is
    # offered to exactly one strategy, not that a name belongs to one strategy
    # forever.
    per_bar: dict = {}
    for ts, sym, name in held_calls:
        per_bar.setdefault((ts, sym), set()).add(name)
    contested = {k: n for k, n in per_bar.items() if len(n) > 1}
    assert not contested, (
        f"a held symbol was offered to more than one strategy on the same "
        f"bar: {contested}")


def test_the_already_held_guard_sits_above_the_rails_in_the_fill_stage():
    """The ORDERING, asserted against the source, because it cannot be reached
    at runtime.

    An order queued while a name was flat can outlive its bar — `pending`
    retains an order whose symbol has no bar today (`backtest.py:922-925`),
    which is what the `expired_unfilled` census bucket counts. The fill stage
    drops such an order as `already_held` (`backtest.py:939-944`) ABOVE
    `pure_checks`, so even that path cannot reach `direction_conflict`.

    BUT THAT BRANCH IS ITSELF UNREACHABLE, and mutation testing is what showed
    it: deleting the `already_held` guard entirely leaves every runtime
    assertion in this file green. The reason is the same ownership routing —
    while a symbol's bar is missing it is not in `today`, so the signal loop
    never runs for it and a SECOND order for that symbol can never join the
    queue; and a rail-blocked order is consumed rather than retained. Probed
    directly: twelve seeds with punched-out bars produced 6 `expired_unfilled`
    retentions and ZERO `already_held`.

    So `already_held` is redundant defensive code given the routing. That is
    worth having and worth knowing — the value of a second guard is that it
    holds if the first is changed — but a runtime test cannot distinguish the
    two, and pretending otherwise would be a test that proves nothing while
    looking like it proves something. What IS checkable is that the guard has
    not been moved BELOW the rails, which is precisely the mutation. Source
    inspection, in the style of `test_rail_interactions.py::_rails_in`.
    """
    import inspect
    src = inspect.getsource(bt.simulate_ensemble)
    guard = src.index('_blocked("already_held")')
    checks = src.index("risk.pure_checks(")
    assert guard < checks, (
        "the already_held drop no longer precedes risk.pure_checks in "
        "simulate_ensemble — a stale queued order can now reach the rails on a "
        "name that is already held")


def test_the_rails_are_never_asked_to_open_a_name_that_is_already_held(bars, cfg):
    """The runtime half: whatever the reason, `pure_checks` is never handed an
    entry for a symbol present in the `positions` dict it is given.

    Asserted against the argument the function actually receives rather than
    against a source line. This holds for TWO independent reasons (ownership
    routing and the `already_held` drop) and cannot tell them apart — the test
    above covers the half this one cannot.
    """
    real_checks = risk.pure_checks
    seen: list = []

    def spy(action, symbol, qty, price, account, positions, cfg_, **kw):
        if action in ENTRY_ACTIONS:
            seen.append((symbol, symbol in positions))
        return real_checks(action, symbol, qty, price, account, positions,
                           cfg_, **kw)

    bt.risk.pure_checks = spy
    try:
        res = bt.simulate_ensemble(bars, cfg, 100_000.0)
    finally:
        bt.risk.pure_checks = real_checks

    assert seen, "no entry reached the rails — the assertion would be vacuous"
    assert res.n_trades > 0
    offenders = [s for s, held in seen if held]
    assert not offenders, (
        f"pure_checks was asked to open {offenders} while it was already held — "
        f"the already_held drop no longer precedes the rails")


def test_direction_conflict_never_fires_through_the_ensemble(bars, cfg):
    """The consequence, and the number §54 must NOT be read as measuring.

    A zero here is not "the collision is rare on this data". It is "the
    simulator cannot reach this rail". Anyone tempted to quote a §54 census
    line as evidence about the interaction should read the two tests above
    first.
    """
    res, _ = _run(bars, cfg)
    summary = res.summary()
    assert summary["n_short_trades"] > 0, "no shorts — vacuous"
    assert res.n_trades > 0
    blocked = (summary["census"] or {}).get("blocked", {})
    assert blocked.get("direction_conflict", 0) == 0, (
        "direction_conflict fired through the ensemble. That is not a "
        "regression — it means the ownership-routing argument in this file's "
        "docstring is now WRONG, and §54's write-up must be corrected before "
        "anything else is believed.")


# ---------------------------------------------------------------------------
# 2. What actually happens instead: contention, decided by priority.
# ---------------------------------------------------------------------------

def _always_buys(symbol, bars_, params, holding, cross_section=None,
                 entry_ts=None):
    """A reclaim that wants every flat name, every bar.

    Scripted rather than coaxed out of real bars, for the reason PR #93 scripted
    the cover path: reclaim's real trigger needs 40 consecutive bars below a
    200-SMA, then a base, then a cross, and a random-walk fixture produces that
    essentially never. A test where reclaim never signals cannot tell "refused"
    from "had nothing to say", which is the failure this file is guarding.
    """
    if holding:
        return Signal(symbol, "hold", "scripted: holds what it owns",
                      {}, "reclaim")
    return Signal(symbol, "buy", "scripted: wants everything", {}, "reclaim")


def test_reclaim_is_never_consulted_on_a_name_xsmom_holds_even_when_it_wants_it(
        bars, cfg, monkeypatch):
    """The sharp version of test 1: make reclaim maximally hungry, then show it
    still never gets asked about a held name.

    Without the scripted appetite this passes trivially. With it, the only thing
    keeping reclaim off those names is the routing.
    """
    monkeypatch.setattr(strategies.REGISTRY["reclaim"], "generate", _always_buys)
    res, log = _run(bars, cfg, spy_generate=True)

    assert res.n_trades > 0
    held = [(ts, sym, name) for ts, sym, name, holding in log if holding]
    assert held, "nothing was held — vacuous"
    xsmom_held = {(ts, sym) for ts, sym, name in held if name == "xsmom"}
    reclaim_held = {(ts, sym) for ts, sym, name in held if name == "reclaim"}
    assert xsmom_held, "xsmom held nothing — vacuous"
    assert reclaim_held, (
        "the scripted reclaim never held anything, so this cannot distinguish "
        "'never consulted on xsmom's names' from 'never consulted at all'")
    assert not (xsmom_held & reclaim_held), (
        f"reclaim was consulted on a name xsmom held, on the same bar: "
        f"{sorted(xsmom_held & reclaim_held)[:5]}")


def _always_shorts(symbol, bars_, params, holding, cross_section=None,
                   position_side=None):
    """An xsmom that wants to short every flat name, every bar."""
    if holding:
        return Signal(symbol, "hold", "scripted: holds what it owns", {}, "xsmom")
    return Signal(symbol, "short", "scripted: shorts everything", {}, "xsmom")


def test_the_higher_priority_strategy_claims_a_contested_flat_name(
        bars, cfg, monkeypatch):
    """`strategies.enabled` sorts by `priority` and the entry loop `break`s on
    the first claim, so when BOTH want the same flat name xsmom (3) takes it and
    reclaim (6) gets nothing.

    Both sides are scripted, because the tiebreak only decides a name when both
    want it on the same bar. An earlier draft scripted only reclaim and asserted
    it got nothing — that failed, correctly: real xsmom signals on the top and
    bottom quartiles and holds the middle 50%, so it simply passes on most names
    and reclaim takes them uncontested. That is contention working, not
    displacement, and conflating the two would have put a false claim in the
    §54 write-up.

    The twin is what makes this a measurement: with xsmom off, the SAME scripted
    reclaim trades. So "reclaim got nothing" is attributable to displacement
    rather than to appetite.
    """
    monkeypatch.setattr(strategies.REGISTRY["reclaim"], "generate", _always_buys)
    monkeypatch.setattr(strategies.REGISTRY["xsmom"], "generate", _always_shorts)
    res, log = _run(bars, cfg, spy_generate=True)
    claimed = {sym for _, sym, name, holding in log
               if holding and name == "reclaim"}
    assert res.n_trades > 0, "nothing traded — vacuous"
    assert not claimed, f"reclaim claimed names despite lower priority: {claimed}"

    solo = _both_legs_cfg()
    solo["strategies"]["xsmom"]["enabled"] = False
    res_solo, log_solo = _run(bars, solo, spy_generate=True)
    solo_owned = {sym for _, sym, name, holding in log_solo
                  if holding and name == "reclaim"}
    assert solo_owned, (
        "the scripted reclaim traded nothing even with the field to itself — "
        "the assertion above proved nothing about displacement")
    assert res_solo.n_trades > 0


def test_reclaim_never_owns_a_short(bars, cfg, monkeypatch):
    """reclaim does not declare `NEEDS_POSITION_SIDE`, so `strategies.generate`
    never hands it a `position_side` and its exit branch assumes the position is
    long. That is safe ONLY because it can never own a short.

    Assert it rather than imply it: a future short leg on reclaim, or an
    ownership hand-off, would make the assumption silently false and the
    strategy would answer "sell" to a short — the doubling failure
    `strategies/base.py` documents.
    """
    monkeypatch.setattr(strategies.REGISTRY["reclaim"], "generate", _always_buys)
    real = strategies.generate
    sides: list = []

    def spy(name, symbol, b, c, holding, **kw):
        if holding and name == "reclaim":
            sides.append((symbol, kw.get("position_side")))
        return real(name, symbol, b, c, holding, **kw)

    bt.strategies.generate = spy
    try:
        solo = _both_legs_cfg()
        solo["strategies"]["xsmom"]["enabled"] = False
        res = bt.simulate_ensemble(bars, solo, 100_000.0)
    finally:
        bt.strategies.generate = real

    assert res.n_trades > 0
    assert sides, "reclaim held nothing — vacuous"
    assert res.summary()["n_short_trades"] == 0, (
        "a short was opened in a book whose only strategy is reclaim")


# ---------------------------------------------------------------------------
# 3. The rail is still a real backstop — for a caller outside the cycle.
# ---------------------------------------------------------------------------

def test_the_rail_still_refuses_a_direct_caller_in_both_directions():
    """Unreachable through the ensemble is not the same as useless.

    `pure_checks` is the last gate before an order and holds for a caller that
    never went through `_run_cycle` — the argument `pre_trade_checks` already
    makes for `halt` in its own comment. The realistic trigger is a broker-side
    position the bot did not open (a manual trade, a reconcile gap), where the
    ownership map and the account disagree.

    The flat twin is beside each half so "refuses everything" cannot pass.
    """
    cfg = {"risk": {"max_position_pct": 100.0, "max_open_positions": 0},
           "sectors": {}, "symbols": ["AAPL"]}
    account = {"equity": 100_000.0}

    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("buy", "AAPL", 1, 1.0, account,
                         {"AAPL": {"market_value": -5_000.0}}, cfg)
    assert e.value.rail == "direction_conflict"
    risk.pure_checks("buy", "AAPL", 1, 1.0, account, {}, cfg)

    with pytest.raises(risk.RiskRejection) as e:
        risk.pure_checks("short", "AAPL", 1, 1.0, account,
                         {"AAPL": {"market_value": 5_000.0}}, cfg)
    assert e.value.rail == "direction_conflict"
    risk.pure_checks("short", "AAPL", 1, 1.0, account, {}, cfg)
