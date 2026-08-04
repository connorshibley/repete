"""A survivorship-free benchmark for gate scoring (§50, 2026-08-03).

Why this file exists
--------------------
Every benchmark this harness had — `beats_buy_hold`, `beats_exposure_matched`,
and `enablement_gate`'s internals — derives from the SNAPSHOT'S OWN UNIVERSE.
§48 measured what that costs. The wide snapshots are built from today's index
membership and back-projected, so every name in them survived to 2026 by
construction:

    2000-2006   equal-weight buy-and-hold  +138.74%
                SPY over the identical bars  +8.68%
                                            -------
                                             +130pp of pure survivorship

Every EDGE claim from §14 onward was scored against the inflated number.

`beats_benchmark_symbol` races ONE named instrument instead. An index ETF's
price already reflects constituent changes — names that failed left the index
and took their losses with them — so the benchmark carries no survivorship.

THE RESIDUAL ASYMMETRY, which §50 pre-registers rather than hides: the strategy
still trades a survivor-only universe, so the comparison runs IN ITS FAVOUR. A
FAIL is decisive; a PASS is not. Nothing in this file can fix that, and no test
here should be read as claiming it does.

Two properties are load-bearing and each is pinned in both directions:

  * an unmeasurable benchmark FAILS and never silently passes — §33 RUN 1
    printed VALIDATED out of checks that quietly did not run;
  * BOTH `Result` construction paths populate the fields. The ensemble path is
    the one every gate takes, and the single-strategy path is the one every
    unit test takes, so a field added to only one looks correct under test and
    scores None in production.
"""
import gzip
import json
import sys

import pytest

import backtest as bt
import gatespec as gs

sys.path.insert(0, "scripts")
import run_gate as rg                                        # noqa: E402

SNAP = "data/snapshots/bars_wide_2022-01-01_2026-07-24.json.gz"


def _bars(n=40, start=100.0, step=1.0):
    return [{"ts": f"2026-01-{i + 1:02d}T00:00:00+00:00",
             "open": start + i * step, "high": start + i * step + 1,
             "low": start + i * step - 1, "close": start + i * step,
             "volume": 1e6} for i in range(n)]


# ------------------------------------------------------- backtest.benchmark_stats

def test_benchmark_stats_measures_the_named_instrument():
    ret, dd = bt.benchmark_stats({"SPY": _bars()}, "SPY", 0, 0.0, 100_000.0)
    assert ret > 0                      # a rising series must return positive
    assert dd == pytest.approx(0.0)     # monotonic rise has no drawdown


def test_benchmark_stats_reports_a_real_drawdown():
    """Paired with the above. A benchmark that always reported 0% drawdown
    would look like a risk-free bar and quietly flatter every comparison."""
    falling = _bars(20) + _bars(20, start=139.0, step=-2.0)
    _, dd = bt.benchmark_stats({"X": falling}, "X", 0, 0.0, 100_000.0)
    assert dd > 10.0


def test_an_absent_symbol_is_None_not_zero():
    """None means 'could not measure'. A 0.0 here would be a bar that any
    profitable arm clears, i.e. a check that passes because it is broken."""
    assert bt.benchmark_stats({"SPY": _bars()}, "QQQ", 0, 0.0, 1e5) == (None, None)
    assert bt.benchmark_stats({}, "SPY", 0, 0.0, 1e5) == (None, None)


def test_a_one_bar_series_is_None():
    assert bt.benchmark_stats({"SPY": _bars(1)}, "SPY", 0, 0.0, 1e5) == (None, None)


def test_the_benchmark_pays_the_same_friction_as_the_strategy():
    """Like-for-like. A frictionless benchmark against a strategy that pays
    slippage and fees is a bar nobody could clear, and the gap would grow with
    the length of the run rather than with skill."""
    free, _ = bt.benchmark_stats({"SPY": _bars()}, "SPY", 0, 0.0, 1e5)
    charged, _ = bt.benchmark_stats({"SPY": _bars()}, "SPY", 50, 25.0, 1e5)
    assert charged < free


# ------------------------------------------- both Result construction paths

def _cfg():
    cfg = bt.load_config()
    cfg.setdefault("backtest", {})["slippage_bps"] = 5
    return cfg


def test_the_ENSEMBLE_path_populates_the_benchmark():
    """THE PATH EVERY GATE TAKES. `run_gate` scores `simulate_ensemble`, so a
    field present only on the single-strategy Result would score None in every
    real run while passing the unit tests below."""
    with gzip.open(SNAP) as f:
        data = json.load(f)
    sym_bars = {s: data[s] for s in ("SPY", "AAPL", "MSFT") if s in data}
    res = bt.simulate_ensemble(sym_bars, _cfg(), 100_000.0)
    assert res.benchmark_symbol == "SPY"
    assert res.benchmark_return_pct is not None
    assert res.benchmark_max_drawdown_pct is not None
    assert "benchmark_return_pct" in res.summary()


def test_the_SINGLE_STRATEGY_path_populates_the_benchmark():
    with gzip.open(SNAP) as f:
        data = json.load(f)
    sym_bars = {s: data[s] for s in ("SPY", "AAPL") if s in data}
    res = bt.simulate(sym_bars, _cfg(), start_cash=100_000.0)
    assert res.benchmark_symbol == "SPY"
    assert res.benchmark_return_pct is not None


def test_the_benchmark_is_not_the_universe_benchmark():
    """The whole point. If these two came back equal the rule would be an
    expensive alias for `beats_buy_hold` and would inherit the survivorship it
    exists to escape."""
    with gzip.open(SNAP) as f:
        data = json.load(f)
    subset = {s: data[s] for s in list(data)[:12] if s != "SPY"}
    subset["SPY"] = data["SPY"]
    res = bt.simulate_ensemble(subset, _cfg(), 100_000.0)
    assert res.benchmark_return_pct != res.buy_hold_return_pct


# --------------------------------------------------- the clause, in run_gate

def _spec(symbol="SPY"):
    return {"bonferroni_k": 14,
            "clauses": [{"id": "c", "rule": "beats_benchmark_symbol",
                         "symbol": symbol}]}


def _arms(total, bench, symbol="SPY", bench_dd=20.0):
    s = {"total_return_pct": total, "benchmark_return_pct": bench,
         "benchmark_symbol": symbol, "benchmark_max_drawdown_pct": bench_dd,
         "max_drawdown_pct": 12.0, "n_trades": 100}
    return {"baseline": (s, [1.0, -1.0])}


def _one(spec, arms):
    return rg.evaluate(spec, arms, "baseline", resamples=50)["clauses"][0]


def test_clause_passes_when_the_arm_beats_the_benchmark():
    assert _one(_spec(), _arms(80.0, 60.0))["pass"] is True


def test_clause_fails_when_the_arm_loses_to_the_benchmark():
    out = _one(_spec(), _arms(60.0, 80.0))
    assert out["pass"] is False
    assert "SPY" in out["detail"]


def test_a_tie_passes():
    """`>=`, matching `beats_buy_hold`. Pinned so the boundary cannot drift
    between two rules that a reader will assume behave alike."""
    assert _one(_spec(), _arms(70.0, 70.0))["pass"] is True


def test_an_unmeasurable_benchmark_FAILS(caplog):
    """THE LOAD-BEARING ASSERTION. §33 RUN 1 printed VALIDATED because checks
    that did not run were not counted as failures. A missing benchmark is
    'we could not measure', which must never read as 'it passed'."""
    out = _one(_spec(), _arms(999.0, None))
    assert out["pass"] is False
    assert "unavailable" in out["detail"]


def test_a_benchmark_computed_for_a_DIFFERENT_symbol_FAILS():
    """The spec is frozen against `symbol: SPY`. If the run computed something
    else, the number on screen would not be the number that was registered."""
    out = _one(_spec("SPY"), _arms(999.0, 10.0, symbol="QQQ"))
    assert out["pass"] is False
    assert "unavailable" in out["detail"]


def test_drawdown_is_reported_but_NOT_gated():
    """§44 tested matched drawdown and was REJECTED there; re-gating it here
    would re-run §44 under a new number. So an arm with far worse drawdown
    than the benchmark still passes on return alone — and the detail string
    must say so, or a return-only pass reads as a deployable one."""
    out = _one(_spec(), _arms(80.0, 60.0, bench_dd=5.0))
    assert out["pass"] is True
    assert "not gated" in out["detail"]
    assert "maxDD" in out["detail"]


# ------------------------------------------------------- gatespec validation

def _valid_spec(clause):
    return {"id": "sX", "claim": "EDGE", "title": "t",
            "snapshot": {"path": "p", "sha256": "0" * 64},
            "arms": [{"name": "baseline"}], "clauses": [clause],
            "prior": "Expected to be rejected; this is a validation "
                     "fixture and not a real registration.",
            "failure_modes": ["fixture — not a real failure mode"]}


def test_the_rule_is_accepted_and_needs_no_second_arm():
    """A SELF rule, so §39's single-arm shape stays legal — which is what lets
    §50 put the incumbent on trial with nothing to compare it to but the
    market."""
    gs.validate(_valid_spec({"id": "a", "rule": "beats_benchmark_symbol",
                             "symbol": "SPY"}))


def test_a_missing_symbol_is_rejected():
    """Its paired half. A defaulted symbol would let the instrument being
    raced change underneath a registered claim without moving the frozen
    hash."""
    with pytest.raises(gs.SpecError, match="symbol"):
        gs.validate(_valid_spec({"id": "a", "rule": "beats_benchmark_symbol"}))
    with pytest.raises(gs.SpecError, match="symbol"):
        gs.validate(_valid_spec({"id": "a", "rule": "beats_benchmark_symbol",
                                 "symbol": ""}))


def test_the_symbol_is_inside_the_frozen_hash():
    """If it were not, swapping SPY for a flattering instrument after
    registration would leave the goalpost hash unchanged — the exact tampering
    `canonical_sha256` exists to prevent."""
    a = _valid_spec({"id": "a", "rule": "beats_benchmark_symbol", "symbol": "SPY"})
    b = _valid_spec({"id": "a", "rule": "beats_benchmark_symbol", "symbol": "IWM"})
    assert gs.canonical_sha256(a) != gs.canonical_sha256(b)
