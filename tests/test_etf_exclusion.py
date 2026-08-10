"""ETFs leave the cross-section — for the strategy that opts in, and only that one.

The core universe is 38 symbols, EIGHT of them baskets (SPY, QQQ, DIA, IWM and
the four sector funds). Ranking a basket against its own constituents is a
category error: XLK is not a peer of AAPL, it is partly made of it, and a
percentile containing both measures two kinds of thing on one scale. §49 put a
number on the cost — 11 of the 13 symbol pairs at or above the 0.85 correlation
threshold involve an ETF, versus 2 of 435 stock-vs-stock pairs — so a short leg
that could short XLK against a book long AAPL/MSFT/NVDA would unwind the book's
own longs rather than add alpha.

Two things this file has to prove, and the second is the load-bearing one:

  1. `xsmom` (which opts in with `exclude_etfs: true`) can no longer rank or
     enter the eight names.
  2. NOTHING ELSE MOVED. ma_crossover, tsmom and meanrev were gated on all 38
     names with the ETFs in. Every exclusion assertion here therefore sits
     beside its twin asserting the opted-out strategies still see the full list
     — without the twin you cannot tell "xsmom was filtered" from "the universe
     shrank for everyone".

And one trap, already documented inside `prepare_one` and re-proved here: the
subtraction happens at the ENTRIES boundary, never inside `prepare()`. Drop a
symbol a strategy HOLDS from the cross-section and `generate` finds it absent
from `ranks`, answers "insufficient history for ranking", and holds it forever —
a filter refusing to close risk, which is the inversion this codebase refuses
everywhere.
"""
import os

import pytest
import yaml

import preflight
import strategies

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")

ETFS = ["SPY", "QQQ", "DIA", "IWM", "XLK", "XLF", "XLE", "XLV"]


@pytest.fixture
def shipped():
    """The real config.yaml by ABSOLUTE path — the autouse fixtures chdir into
    a tmp_path, and a relative open() would make every assertion here vacuous."""
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def _cfg(exclude=True, etfs=None, symbols=None):
    # Two stocks and two ETFs, and BOTH ETFs are named in the list. An earlier
    # draft listed only one of them, which made
    # test_holding_one_etf_does_not_readmit_the_others pass on the wrong
    # reason — the other ETF was rankable because it was never excluded, not
    # because the exclusion held.
    return {
        "symbols": symbols if symbols is not None else ["AAPL", "MSFT", "SPY", "XLK"],
        "etfs": ["SPY", "XLK"] if etfs is None else etfs,
        "strategies": {
            "xsmom": {"enabled": False, "exclude_etfs": exclude},
            "ma_crossover": {"enabled": True},
        },
    }


# ---------------------------------------------------------------------------
# excluded_etfs — opt-in, and inert for everyone else.
# ---------------------------------------------------------------------------

def test_a_strategy_that_opts_in_gets_the_configured_set():
    assert strategies.excluded_etfs(_cfg(), "xsmom") == {"SPY", "XLK"}


def test_a_strategy_that_does_not_opt_in_excludes_nothing():
    """The twin, and the reason the flag is opt-in rather than universal: the
    three enabled strategies must keep the universe their gates were run on."""
    assert strategies.excluded_etfs(_cfg(), "ma_crossover") == set()


def test_an_unknown_strategy_excludes_nothing_rather_than_raising():
    assert strategies.excluded_etfs(_cfg(), "not_a_strategy") == set()


def test_opting_in_with_no_etfs_list_excludes_nothing_in_code():
    """Code-level polarity: a missing list subtracts the empty set, so the
    strategy trades MORE than intended rather than nothing. That is the wrong
    direction to fail silently in, which is why preflight convicts it — see
    test_opting_in_without_a_list_is_refused below."""
    cfg = _cfg()
    del cfg["etfs"]
    assert strategies.excluded_etfs(cfg, "xsmom") == set()


# ---------------------------------------------------------------------------
# universe_for / in_universe — what may be ENTERED.
# ---------------------------------------------------------------------------

def test_the_opted_in_strategys_universe_drops_the_etfs():
    assert strategies.universe_for(_cfg(), "xsmom") == {"AAPL", "MSFT"}


def test_every_other_strategys_universe_is_untouched():
    assert strategies.universe_for(_cfg(), "ma_crossover") == {
        "AAPL", "MSFT", "SPY", "XLK"}


def test_an_etf_is_not_enterable_by_the_opted_in_strategy():
    assert strategies.in_universe(_cfg(), "xsmom", "SPY") is False


def test_the_same_etf_is_still_enterable_by_everyone_else():
    assert strategies.in_universe(_cfg(), "ma_crossover", "SPY") is True


def test_a_stock_stays_enterable_by_the_opted_in_strategy():
    """Without this the exclusion could be emptying the universe rather than
    filtering it — the failure mode `universe_for`'s docstring warns about for
    an unrecognised key."""
    assert strategies.in_universe(_cfg(), "xsmom", "AAPL") is True


def test_an_unrecognised_universe_key_still_yields_nothing():
    """The subtraction must not accidentally convert the empty set (a typo'd
    universe, which is meant to make the strategy trade NOTHING) into the core
    universe by taking a different branch."""
    cfg = _cfg()
    cfg["strategies"]["xsmom"]["universe"] = "typo"
    assert strategies.universe_for(cfg, "xsmom") == set()


# ---------------------------------------------------------------------------
# prepare_one — the held-position trap.
# ---------------------------------------------------------------------------

def _bars(n=300):
    return [{"ts": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00",
             "open": 100 + i, "high": 101 + i, "low": 99 + i,
             "close": 100 + i, "volume": 1_000_000} for i in range(n)]


def _xsmom_cfg():
    cfg = _cfg()
    cfg["strategies"]["xsmom"].update(
        {"rank_lookback_bars": 20, "skip_bars": 2,
         "buy_top_fraction": 0.25, "exit_below_fraction": 0.50})
    return cfg


def test_an_unheld_etf_is_absent_from_the_ranking():
    all_bars = {s: _bars() for s in ("AAPL", "MSFT", "SPY", "XLK")}
    ctx = strategies.prepare_one(_xsmom_cfg(), "xsmom", all_bars)
    assert "SPY" not in ctx["ranks"]
    assert "AAPL" in ctx["ranks"]


def test_a_HELD_etf_is_still_ranked_so_it_can_be_exited():
    """The trap. `prepare_one` adds back whatever the strategy holds, and the
    ETF subtraction must not defeat that: a held symbol missing from `ranks`
    makes generate() answer "insufficient history for ranking" and HOLD, every
    cycle, forever — an unexitable position created by a filter."""
    all_bars = {s: _bars() for s in ("AAPL", "MSFT", "SPY", "XLK")}
    ctx = strategies.prepare_one(_xsmom_cfg(), "xsmom", all_bars, held={"SPY"})
    assert "SPY" in ctx["ranks"]


def test_holding_one_etf_does_not_readmit_the_others():
    """The twin: `held` is an exception for the position that exists, not a
    switch that turns the whole exclusion off."""
    all_bars = {s: _bars() for s in ("AAPL", "MSFT", "SPY", "XLK")}
    ctx = strategies.prepare_one(_xsmom_cfg(), "xsmom", all_bars, held={"SPY"})
    assert "XLK" not in ctx["ranks"]


def test_the_explicit_universe_override_is_filtered_too():
    """`backtest.py --symbols ...` overrides the declared universe. If the
    override skipped the subtraction, the simulator would rank a cross-section
    the live cycle cannot rank — a strategy scored on a universe it does not
    trade, which is the §22 symbol-order failure in a new costume."""
    all_bars = {s: _bars() for s in ("AAPL", "MSFT", "SPY", "XLK")}
    ctx = strategies.prepare_one(_xsmom_cfg(), "xsmom", all_bars,
                                 universe={"AAPL", "MSFT", "SPY"})
    assert "SPY" not in ctx["ranks"]
    assert {"AAPL", "MSFT"} <= set(ctx["ranks"])


def test_an_opted_out_strategys_cross_section_would_keep_them():
    """The twin at the prepare() level, using xsmom's own module with the flag
    off — proves the filtering lives in the opt-in and not in prepare()."""
    cfg = _xsmom_cfg()
    cfg["strategies"]["xsmom"]["exclude_etfs"] = False
    all_bars = {s: _bars() for s in ("AAPL", "MSFT", "SPY", "XLK")}
    ctx = strategies.prepare_one(cfg, "xsmom", all_bars)
    assert "SPY" in ctx["ranks"]


# ---------------------------------------------------------------------------
# preflight — the list cannot quietly name nothing.
# ---------------------------------------------------------------------------

def _fails(cfg):
    return [f for f in preflight.run(cfg) if "etf" in f.lower()]


@pytest.fixture
def base(cfg):
    """conftest's cfg, extended with an etfs: list that is actually valid, so
    each test below changes exactly one thing."""
    cfg["symbols"] = ["AAPL", "MSFT", "SPY", "XLK"]
    cfg["etfs"] = ["SPY", "XLK"]
    cfg.setdefault("strategies", {})["xsmom"] = {
        "enabled": False, "exclude_etfs": True}
    return cfg


def test_a_valid_etf_list_is_accepted(base):
    assert _fails(base) == []


def test_an_etf_that_is_not_in_symbols_is_refused(base):
    """The typo case, and the whole reason this guard exists: `excluded_etfs`
    subtracts a SET, so a name that is not in the universe subtracts nothing
    and the exclusion silently does not happen."""
    base["etfs"] = ["SPY", "XLKK"]
    assert any("XLKK" in f for f in _fails(base))


def test_a_duplicated_etf_is_refused(base):
    base["etfs"] = ["SPY", "SPY"]
    assert any("duplicate" in f for f in _fails(base))


def test_an_empty_etf_list_is_refused(base):
    base["etfs"] = []
    assert _fails(base)


def test_opting_in_without_a_list_is_refused(base):
    """The mirror: a strategy that believes it excludes ETFs and does not.
    Without this it reads identically to "this universe simply has no ETFs"."""
    del base["etfs"]
    assert any("exclude_etfs" in f for f in _fails(base))


def test_no_etfs_key_and_nobody_opting_in_is_fine(base):
    """The twin of the mirror — this is every config that predates the flag,
    and it must still start."""
    del base["etfs"]
    del base["strategies"]["xsmom"]
    assert _fails(base) == []


# ---------------------------------------------------------------------------
# The SHIPPED config — the artifact, not the fixture.
# ---------------------------------------------------------------------------

def test_the_shipped_config_names_eight_etfs_all_of_them_real(shipped):
    assert shipped["etfs"] == ETFS
    core = set(shipped["symbols"])
    assert set(ETFS) <= core


def test_only_CROSS_SECTIONAL_never_gated_strategies_opt_in(shipped):
    """Pins WHICH strategies subtract. If ma_crossover, tsmom or meanrev ever
    picked this up, eight names would leave a universe those three were gated
    on — a live behaviour change to the evidence record, arriving through a
    config key rather than through a gate.

    `hi52` joined on 2026-08-10 for the same reason xsmom is here and under the
    same conditions: it ranks a cross-section, so a basket sharing a scale with
    its own constituents is a category error, and it has never passed a gate, so
    opting it in cannot invalidate evidence that does not exist. The assertion
    is written as a PROPERTY rather than a literal list, so the next strategy
    added has to satisfy the reason rather than just extend the line."""
    opted = {n for n, p in shipped["strategies"].items()
             if (p or {}).get("exclude_etfs")}
    assert opted == {"xsmom", "hi52"}
    for name in opted:
        assert strategies.REGISTRY[name].NEEDS_CROSS_SECTION, \
            f"{name} is not cross-sectional — exclude_etfs is not its fix"
        assert shipped["strategies"][name]["enabled"] is False, \
            f"{name} is ENABLED and subtracts eight names from a live universe"


def test_the_shipped_config_leaves_thirty_rankable_names_for_xsmom(shipped):
    """38 - 8. Stated as a number because "the ETFs are gone" is satisfied by a
    universe of one, and because xsmom's percentile IS its meaning: top 25% of
    30 is a different strategy from top 25% of 38, which is why the gate it
    failed on 2026-07-15 no longer describes it."""
    assert len(strategies.universe_for(shipped, "xsmom")) == 30


def test_the_shipped_config_still_gives_the_enabled_strategies_all_38(shipped):
    """The twin that matters most in this file. These three are what actually
    runs; if this number moves, the change was not a no-op."""
    for name in ("ma_crossover", "tsmom", "meanrev"):
        assert len(strategies.universe_for(shipped, name)) == 38, name
