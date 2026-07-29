"""DIVERGENCE #12 — live read RAW bars while every snapshot was ADJUSTED.

The defect
----------
`alpaca-py`'s `StockBarsRequest.adjustment` defaults to `None`, which the API
treats as **raw**. Neither `broker.bars()` (the live path) nor
`backtest.fetch_bars()` passed it.

Meanwhile every frozen snapshot is split- and dividend-adjusted:
`scripts/build_snapshot.py` uses yfinance `auto_adjust=True`, and
`scripts/build_intraday_snapshot.py` uses `adjustment=Adjustment.ALL`. So the
live bot has been reading a different price series from the one every gate
verdict was scored on.

Why it is not theoretical
-------------------------
`data/snapshots/MANIFEST.json` already records the consequence, from the one
time this was caught: the first intraday build went out RAW and carried **11
unadjusted splits** that "the simulator read as overnight collapses — it voided
a gate run."

A 2-for-1 split in a raw series is a 50% overnight gap that never happened. It
collapses an SMA, spikes ATR, and can fire an entry signal or trip a stop on an
event that did not change the value of a single share held. The bot cannot tell
that gap from a crash.

What is asserted here
---------------------
That both request builders pass `Adjustment.ALL`, and that the shipped code
does not quietly regress to the default. These are structural assertions
because the real check needs Alpaca credentials and a network — which the suite
is deliberately without (`tests/` is fully offline by design). A structural
test that names the exact enum is worth more than an integration test that
never runs.
"""
import os

import pytest


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(name):
    with open(os.path.join(REPO, "src", name)) as f:
        return f.read()


# ---- the live path ----

def test_the_live_broker_requests_adjusted_bars():
    src = _src("broker.py")
    assert "from alpaca.data.enums import Adjustment" in src
    assert "adjustment=Adjustment.ALL," in src, (
        "broker.bars() omitted `adjustment`, so Alpaca served RAW bars while "
        "every snapshot is adjusted — divergence #12")


def test_the_backtest_fetch_path_requests_adjusted_bars():
    src = _src("backtest.py")
    assert "adjustment=Adjustment.ALL" in src, (
        "backtest.fetch_bars feeds src/revalidate.py — raw bars there would "
        "revalidate live decisions against a series no gate ever scored")


def test_neither_path_settles_for_split_only():
    """SPLIT alone would still leave a dividend mismatch against snapshots
    built with auto_adjust=True. The two must agree on BOTH corrections, or
    the divergence is narrowed rather than closed."""
    for name in ("broker.py", "backtest.py"):
        src = _src(name)
        assert "Adjustment.SPLIT" not in src, name
        assert "Adjustment.RAW" not in src, name


# ---- the snapshots this is being aligned TO ----

def test_every_snapshot_in_the_manifest_declares_an_adjustment():
    """If a snapshot ever ships unlabelled, the alignment above is aimed at
    nothing. `empty is not the same as absent` — an unstated adjustment is the
    one that silently reverts to raw."""
    import json
    with open(os.path.join(REPO, "data", "snapshots", "MANIFEST.json")) as f:
        man = json.load(f)

    entries = man if isinstance(man, list) else list(man.values())
    flat = []

    def walk(node):
        if isinstance(node, dict):
            if "source" in node:
                flat.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(entries)
    assert flat, "no snapshot entries found — the manifest shape changed"
    for e in flat:
        s = e["source"].lower()
        assert "adjust" in s, (
            f"snapshot source does not state its adjustment: {e['source']!r}")


def test_the_snapshot_builder_still_auto_adjusts():
    """The other half of the alignment. If build_snapshot.py ever dropped
    auto_adjust, closing #12 on the live side would re-open it from the other
    direction while every assertion above stayed green."""
    with open(os.path.join(REPO, "scripts", "build_snapshot.py")) as f:
        src = f.read()
    assert "auto_adjust=True" in src


def test_gutting_the_adjustment_would_fail_this_file():
    """Meta-assertion. Name the exact call shape so a refactor that drops the
    kwarg cannot leave this file green."""
    src = _src("broker.py")
    i = src.index("timeframe=_TIMEFRAMES[timeframe]")
    j = src.index(")", src.index("adjustment=Adjustment.ALL", i))
    assert "adjustment=Adjustment.ALL" in src[i:j + 1], (
        "the adjustment is no longer inside the StockBarsRequest that "
        "broker.bars() actually sends")
