"""Every module in REGISTRY must be REACHABLE — §58, 2026-08-10.

The defect this exists to end: `lowvol` was added to REGISTRY in §32 with no
`config.yaml` block. `strategy_params` returned None, so `strategies.generate`
answered `hold "no config for strategy lowvol"` for every symbol on every bar.
Setting `enabled: true` would not have made it trade. It was a registered
strategy that could not run.

Nothing failed, because nothing asked. `test_lowvol.py` asserted only that the
strategy was *not enabled* — which is true of a working disabled strategy and
equally true of one that does not exist. `ci.yml:55-69` states the standard
outright: a check that cannot fail is worse than no check, because it buys
confidence without buying information.

This file asks the question that was missing, and it asks it of the WHOLE
registry rather than of `lowvol`, so the next module added without a block is
caught on the commit that adds it rather than eighteen sections later.
"""
import sys

import pytest
import yaml

sys.path.insert(0, "src")
import strategies                                             # noqa: E402

with open("config.yaml") as _f:
    CFG = yaml.safe_load(_f)

NAMES = sorted(strategies.REGISTRY)

#: What a strategy answers when it has no config block. Matched on rather than
#: reproduced by hand at each call site, so a reworded sentinel breaks here once.
NO_CONFIG = "no config for strategy"


def _bars(n=300, start=100.0, step=0.1):
    out, price = [], start
    for _ in range(n):
        price += step
        out.append({"open": price, "high": price * 1.01, "low": price * 0.99,
                    "close": price, "volume": 1_000_000,
                    "timestamp": "2024-01-01T00:00:00Z"})
    return out


@pytest.mark.parametrize("name", NAMES)
def test_every_registered_strategy_has_a_config_block(name):
    """REGISTRY membership without a block is the exact `lowvol` state: listed,
    referenced, documented, and unable to produce a signal."""
    assert strategies.strategy_params(CFG, name) is not None, (
        f"{name} is in REGISTRY with no `strategies.{name}:` block, so every "
        f"call answers '{NO_CONFIG} {name}'. Add a block with enabled: false "
        f"rather than leaving it accidentally absent.")


@pytest.mark.parametrize("name", NAMES)
def test_every_registered_strategy_actually_produces_a_signal(name):
    """The half a config-key check would still miss: a block can exist and be
    missing the key the module indexes with `params["..."]`, which raises a
    KeyError deep in the entry loop rather than returning a Signal.

    Cross-sectional strategies get a real `prepare()` context built from four
    symbols, because handing them None would exercise the "cross-section
    unavailable" early return and prove nothing about their ranking path."""
    params = strategies.strategy_params(CFG, name)
    assert params is not None
    ctx = None
    if strategies.REGISTRY[name].NEEDS_CROSS_SECTION:
        universe = {"AAA": _bars(), "BBB": _bars(step=0.2),
                    "CCC": _bars(step=0.05), "DDD": _bars(step=0.3)}
        ctx = strategies.prepare_one(CFG, name, universe,
                                     universe=set(universe))
        assert ctx is not None, f"{name} declares NEEDS_CROSS_SECTION but " \
                                f"prepare_one returned None"
    sig = strategies.generate(name, "AAA", _bars(), CFG, False,
                              cross_section=ctx, entry_ts=None,
                              position_side=None)
    assert NO_CONFIG not in sig.reason, (
        f"{name} is unreachable: {sig.reason}")
    assert sig.strategy == name
    assert sig.action in ("buy", "sell", "short", "cover", "hold")


@pytest.mark.parametrize("name", NAMES)
def test_a_registered_strategy_declares_its_own_lookback(name):
    """`max_lookback_bars` calls this for every configured module, so a block
    missing the key it reads takes down the whole fetch, not just its own
    strategy."""
    need = strategies.REGISTRY[name].required_lookback(
        strategies.strategy_params(CFG, name))
    assert isinstance(need, int) and need > 0


def test_the_unreachable_state_is_still_DETECTABLE():
    """The paired half, and the one that makes the tests above mean something.

    If `strategy_params` had stopped returning None for a missing block — or
    `generate` had stopped emitting the sentinel — every assertion above would
    pass vacuously on a registry full of unconfigured modules. So: strip the
    block and require the failure."""
    stripped = {**CFG, "strategies": {k: v for k, v in CFG["strategies"].items()
                                      if k != "lowvol"}}
    assert strategies.strategy_params(stripped, "lowvol") is None
    sig = strategies.generate("lowvol", "AAA", _bars(), stripped, False)
    assert NO_CONFIG in sig.reason
