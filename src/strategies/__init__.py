"""Strategy registry — config-driven ensemble membership.

`enabled(cfg)` yields (name, params) in priority order for ENTRY evaluation;
exits always route to the position's owning strategy regardless of enabled
(see main.py ownership rules). A legacy config with only the old `strategy:`
section synthesizes an ma_crossover-only ensemble, so old configs and tests
keep working.
"""
from strategies import (ma_crossover, tsmom, xsmom, meanrev, donchian, lowvol,
                        reclaim, hi52)
from strategies.base import Signal, sma, rsi, total_return, true_range, atr  # noqa: F401

# Registered != enabled. `lowvol` (§32, 2026-07-27) and `hi52` (§59,
# 2026-08-10) both ship DISABLED and stay that way unless a pre-registered gate
# passes; being in this dict only means a config MAY name it.
#
# EVERY MODULE HERE MUST HAVE A `config.yaml` BLOCK. `strategy_params` returns
# None without one and `generate` answers `hold "no config for strategy <n>"` —
# so an unconfigured module is UNREACHABLE while looking registered. `lowvol`
# was in exactly that state from §32 until §58 found it, and its own test
# passed the whole time by asserting only that it was not enabled.
# `test_registry_is_reachable.py` is what stops that recurring.
REGISTRY = {m.NAME: m for m in (ma_crossover, tsmom, xsmom, meanrev,
                                donchian, lowvol, reclaim, hi52)}

DEFAULT_OWNER = "ma_crossover"  # legacy ledger records carry no strategy tag


def _config_map(cfg: dict) -> dict:
    """The strategies: mapping, or a legacy shim from the old strategy: keys."""
    if "strategies" in cfg:
        return cfg["strategies"]
    legacy = cfg.get("strategy", {})
    return {"ma_crossover": {"enabled": True, "priority": 1,
                             "fast_period": legacy.get("fast_period", 10),
                             "slow_period": legacy.get("slow_period", 30)}}


def strategy_params(cfg: dict, name: str) -> dict | None:
    return _config_map(cfg).get(name)


#: Value of a strategy's `universe:` key meaning "the `sectors:` map".
SECTORS_UNIVERSE = "sectors"


def sector_universe(cfg: dict) -> set:
    """Every symbol named anywhere in config's `sectors:` block."""
    return {s for syms in (cfg.get("sectors") or {}).values() for s in (syms or ())}


def excluded_etfs(cfg: dict, name: str) -> set:
    """The ETFs `name` must not rank or enter — empty unless it opts in with
    `exclude_etfs: true`.

    WHY A STRATEGY WOULD OPT IN. The core universe is 38 symbols, EIGHT of them
    ETFs (SPY, QQQ, DIA, IWM, and the four sector funds). Ranking a basket
    against its own constituents is a category error in a cross-section: XLK is
    not a peer of AAPL, it is partly made of it, so a percentile that contains
    both is measuring two different kinds of thing on one scale.

    It stops being merely wrong and starts being expensive once a short leg
    exists. §49 measured the overlap: 11 of the 13 symbol pairs at or above the
    0.85 correlation threshold involve an ETF, against 2 of 435 stock-vs-stock
    pairs. Shorting XLK while the ensemble is long AAPL, MSFT and NVDA is not
    alpha — it unwinds the book's own longs, and the correlation cap is the only
    rail that would notice, one position at a time.

    OPT-IN, not universal. ma_crossover, tsmom and meanrev were gated ON the
    38-name list including its ETFs; silently removing eight names from under
    them would change live behaviour on strategies whose whole evidence record
    was measured with them in.
    """
    params = strategy_params(cfg, name) or {}
    if not params.get("exclude_etfs"):
        return set()
    return set(cfg.get("etfs") or ())


def universe_for(cfg: dict, name: str) -> set:
    """The symbols `name` may ENTER.

    The default — and the value for every strategy that predates this — is the
    CORE universe, `cfg["symbols"]`. That default is load-bearing: "no
    `universe:` key" must mean the 38 names those strategies were gated on, NOT
    "every symbol we happen to be scanning". `scan_symbols` now carries the
    sector names too, so a permissive default would silently hand ma_crossover,
    tsmom and meanrev ~90 extra names to trade — a live behaviour change, on
    strategies whose entire evidence record was measured on 38.

    An UNRECOGNISED key yields the EMPTY set, not the core universe. Preflight
    refuses to start on one, so this is defence in depth; the polarity is chosen
    so a typo makes a strategy trade NOTHING rather than trade the wrong
    universe. A silent outage is recoverable and loud in the logs; a silently
    wrong universe corrupts the evidence record and looks normal.
    """
    params = strategy_params(cfg, name) or {}
    key = params.get("universe")
    if key is None:
        base = set(cfg.get("symbols") or ())
    elif key == SECTORS_UNIVERSE:
        base = sector_universe(cfg)
    else:
        return set()
    # Subtracted HERE, at the entries-only boundary, rather than inside
    # xsmom.prepare. prepare_one adds back whatever the strategy HOLDS, and the
    # comment there spells out why that matters: drop a held symbol from the
    # cross-section and generate() finds it absent from `ranks`, answers
    # "insufficient history for ranking", and HOLDS forever — a filter refusing
    # to close risk. Excluding inside prepare() would have no way to make that
    # exception, so it would turn an ETF this strategy somehow held into an
    # unexitable position.
    return base - excluded_etfs(cfg, name)


def in_universe(cfg: dict, name: str, symbol: str) -> bool:
    """ENTRIES ONLY — never consult this on an exit.

    Exits route to the position's OWNING strategy regardless of `enabled`, and
    they must route regardless of universe too: a symbol can leave a universe
    while a position in it is still open (a config edit, a re-frozen sector
    map). Filtering the exit path would leave that position with no strategy
    willing to close it — a rail refusing an EXIT, which is the risk-enlarging
    inversion this codebase refuses everywhere else. That is why this filter
    lives at main.py's entry loop and NOT inside `generate()`, which both paths
    call.
    """
    return symbol in universe_for(cfg, name)


def enabled(cfg: dict) -> list[tuple[str, dict]]:
    """(name, params) for enabled strategies, priority order (entries only)."""
    out = [(name, params) for name, params in _config_map(cfg).items()
           if params.get("enabled") and name in REGISTRY]
    return sorted(out, key=lambda t: t[1].get("priority", 99))


def max_lookback_bars(cfg: dict) -> int:
    """Bars to fetch per symbol: enough for every enabled strategy, plus any
    disabled strategy that may still own an open position (exits keep
    working after a strategy is disabled), floored by the legacy setting.

    RAILS COUNT TOO, and until §58 they did not. Every strategy declares its own
    `required_lookback` and the shipped config's answer is 253 — set by the
    DISABLED xsmom, since a disabled strategy's exits still need history. But
    `risk.contraction_blocked` needs 272, and a rail handed less than it needs
    does not raise. It returns None, FAILS OPEN, and permits every entry. The
    filter would appear in config, appear in the census key list, be called on
    every signal, and block nothing: a check that cannot fail, which `ci.yml`
    names as worse than no check at all.

    `risk.extra_lookback_bars` returns 0 while the knob is unset, so the shipped
    configuration's answer is unchanged and provably so — `test_contraction.py`
    pins both the 253 and the 272.
    """
    floor = cfg.get("strategy", {}).get("lookback_bars", 100)
    needs = [REGISTRY[name].required_lookback(params)
             for name, params in _config_map(cfg).items() if name in REGISTRY]
    # Local import: risk imports strategies.base, so a module-level import here
    # would close the cycle strategies -> risk -> strategies.base -> strategies.
    from risk import extra_lookback_bars
    return max([floor, extra_lookback_bars(cfg)] + needs)


def generate(name: str, symbol: str, bars: list[dict], cfg: dict,
             holding: bool, cross_section=None,
             entry_ts: str | None = None,
             position_side: str | None = None) -> Signal:
    """Dispatch to one strategy with its own params sub-dict."""
    mod = REGISTRY[name]
    params = strategy_params(cfg, name)
    if params is None:
        return Signal(symbol, "hold", f"no config for strategy {name}",
                      strategy=name)
    # DECLARED, not name-checked. This was `if name == "meanrev"`, which was
    # accurate while meanrev was the only strategy with a max-hold rule and
    # became a silent trap the moment a second one existed: `reclaim` would have
    # been handed entry_ts=None, its max_hold_days would have measured nothing,
    # and no test would fail — the strategy would just never time out.
    #
    # Accumulated into kwargs rather than branched: two independent optional
    # arguments make four combinations, and an if/elif chain over them is the
    # shape that quietly drops one. A module that declares neither flag is
    # called with EXACTLY the arguments it was called with before.
    extra: dict = {}
    if getattr(mod, "NEEDS_ENTRY_TS", False):
        extra["entry_ts"] = entry_ts
    if getattr(mod, "NEEDS_POSITION_SIDE", False):
        extra["position_side"] = position_side
    return mod.generate(symbol, bars, params, holding,
                        cross_section=cross_section, **extra)


def prepare_one(cfg: dict, name: str, all_bars: dict,
                held: set | None = None, universe: set | None = None):
    """THE single implementation of "prepare one strategy's cross-section".

    One entry point on purpose, and for the same reason `scan_order` is one:
    both offline simulators called `mod.prepare(hist_by_sym, sparams)` directly
    (backtest.py, the single-strategy and ensemble loops), so any scoping rule
    added here and not there becomes a live/sim divergence. Four of those have
    already cost real rework (§13 missing rails, §19a fill timing, §19b global
    counters, §22 symbol order), and this one would have been silent in the
    worst way: `reclaim` in the simulator would receive no `cfg`, find no sector
    map, rank no laggards, and simply never trade — a strategy that scores zero
    because it never ran, indistinguishable in the output from one that ran and
    found nothing.

    `universe` OVERRIDES the strategy's declared universe, and exists for one
    caller: the SINGLE-STRATEGY backtester, where the symbol list under test is
    a property of the RUN, not of config — `backtest.py --symbols A B C` means
    "score this strategy on A, B, C". Scoping that run by `cfg["symbols"]`
    instead would hand a cross-sectional strategy an empty ranking and report
    zero trades, which reads identically to "the strategy found nothing".
    The ENSEMBLE backtester deliberately does NOT pass it: there, several
    strategies with different universes run together and must be scoped exactly
    as they are live, or the simulator stops mirroring the thing it simulates.

    Returns None when `name` has no config or is not cross-sectional.
    """
    mod = REGISTRY[name]
    params = strategy_params(cfg, name)
    if params is None or not mod.NEEDS_CROSS_SECTION:
        return None
    # EACH STRATEGY RANKS ITS OWN UNIVERSE, PLUS WHATEVER IT HOLDS.
    #
    # The universe half: in the live cycle `all_bars` is keyed by
    # `scan_symbols`, which is the UNION of every universe plus positions plus
    # news nominations. Passing it through unfiltered would silently change what
    # `xsmom` MEANS — its ranking is a PERCENTILE over the set it is given, and
    # "top 25% of 38" and "top 25% of ~150" are different strategies. Nothing
    # would fail; the numbers would just quietly stop being the ones its gate
    # was run on.
    #
    # The `held` half is not a refinement, it is a correctness requirement, and
    # scoping without it is a TRAP. A strategy can own a position in a symbol
    # outside its universe — removed from config.yaml, or entered through a past
    # news nomination; the scan-list comment in main.py names exactly this case.
    # Drop that symbol from the cross-section and `xsmom.generate` finds
    # `symbol not in ranks`, answers "insufficient history for ranking", and
    # HOLDS — forever. The position becomes unexitable: a filter refusing to
    # close risk, the same inversion `in_universe` is entries-only to avoid, one
    # layer down. Including a held symbol cannot corrupt the percentile in the
    # other direction either, since it is a position the strategy already owns
    # and must be able to rank in order to exit.
    # The ETF subtraction is applied to the OVERRIDE too, not only to
    # universe_for's answer. `backtest.py --symbols ...` names a symbol list for
    # the run, and if that list carried ETFs the simulator would rank a
    # cross-section the live cycle cannot rank — a strategy scored on a universe
    # it does not trade, which is the §22 symbol-order failure in a new costume.
    # Re-subtracting on the non-override path is a no-op; universe_for already
    # did it.
    base = universe if universe is not None else universe_for(cfg, name)
    allowed = (base - excluded_etfs(cfg, name)) | (held or set())
    scoped = {s: b for s, b in all_bars.items() if s in allowed}
    # `cfg` as well as `params`: a cross-section can depend on config OUTSIDE
    # the strategy's own sub-dict — `reclaim` needs the top-level `sectors:`
    # map, which cannot live under `strategies.reclaim` because `risk.py`'s
    # concentration rail reads it too and must not reach into a strategy's
    # params. xsmom ignores it.
    return mod.prepare(scoped, params, cfg)


def prepare_cross_sections(cfg: dict, all_bars: dict,
                           extra_owners: set | None = None,
                           held: set | None = None) -> dict:
    """Once-per-cycle precompute for cross-sectional strategies: the enabled
    ones, plus any disabled strategy that still OWNS an open position — its
    exit logic must keep working after it's disabled (ownership rule).

    Scoping lives in `prepare_one`, which the offline simulators call too."""
    names = {name for name, _ in enabled(cfg)}
    names |= {o for o in (extra_owners or set()) if o in REGISTRY}
    out = {}
    for name in names:
        ctx = prepare_one(cfg, name, all_bars, held=held)
        if ctx is not None:
            out[name] = ctx
    return out
