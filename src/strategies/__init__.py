"""Strategy registry — config-driven ensemble membership.

`enabled(cfg)` yields (name, params) in priority order for ENTRY evaluation;
exits always route to the position's owning strategy regardless of enabled
(see main.py ownership rules). A legacy config with only the old `strategy:`
section synthesizes an ma_crossover-only ensemble, so old configs and tests
keep working.
"""
from strategies import ma_crossover, tsmom, xsmom, meanrev, donchian, lowvol
from strategies.base import Signal, sma, rsi, total_return, true_range, atr  # noqa: F401

# Registered != enabled. `lowvol` (§32, 2026-07-27) ships DISABLED and stays
# that way unless its pre-registered gate passes; being in this dict only means
# a config MAY name it.
REGISTRY = {m.NAME: m for m in (ma_crossover, tsmom, xsmom, meanrev,
                                donchian, lowvol)}

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


def enabled(cfg: dict) -> list[tuple[str, dict]]:
    """(name, params) for enabled strategies, priority order (entries only)."""
    out = [(name, params) for name, params in _config_map(cfg).items()
           if params.get("enabled") and name in REGISTRY]
    return sorted(out, key=lambda t: t[1].get("priority", 99))


def max_lookback_bars(cfg: dict) -> int:
    """Bars to fetch per symbol: enough for every enabled strategy, plus any
    disabled strategy that may still own an open position (exits keep
    working after a strategy is disabled), floored by the legacy setting."""
    floor = cfg.get("strategy", {}).get("lookback_bars", 100)
    needs = [REGISTRY[name].required_lookback(params)
             for name, params in _config_map(cfg).items() if name in REGISTRY]
    return max([floor] + needs)


def generate(name: str, symbol: str, bars: list[dict], cfg: dict,
             holding: bool, cross_section=None,
             entry_ts: str | None = None) -> Signal:
    """Dispatch to one strategy with its own params sub-dict."""
    mod = REGISTRY[name]
    params = strategy_params(cfg, name)
    if params is None:
        return Signal(symbol, "hold", f"no config for strategy {name}",
                      strategy=name)
    if name == "meanrev":  # only strategy that needs the entry timestamp
        return mod.generate(symbol, bars, params, holding,
                            cross_section=cross_section, entry_ts=entry_ts)
    return mod.generate(symbol, bars, params, holding,
                        cross_section=cross_section)


def prepare_cross_sections(cfg: dict, all_bars: dict,
                           extra_owners: set | None = None) -> dict:
    """Once-per-cycle precompute for cross-sectional strategies: the enabled
    ones, plus any disabled strategy that still OWNS an open position — its
    exit logic must keep working after it's disabled (ownership rule)."""
    names = {name for name, _ in enabled(cfg)}
    names |= {o for o in (extra_owners or set()) if o in REGISTRY}
    out = {}
    for name in names:
        mod = REGISTRY[name]
        if mod.NEEDS_CROSS_SECTION:
            params = strategy_params(cfg, name)
            if params:
                out[name] = mod.prepare(all_bars, params)
    return out
