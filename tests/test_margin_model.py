"""§64 margin/financing model and macro gate.

Two properties carry everything here:

1. THE 1.0 PATH IS THE OLD CODE. Every recorded verdict was scored through
   `buying_power = cash` with no financing line. At the shipped default the
   ensemble must be byte-identical with the margin block present, absent, or
   explicitly 1.0 — proven by running all three, not by inspection.

2. FINANCING FAILS CLOSED. A levered arm that borrows for free is divergence
   #16 wearing a new name (§53's short leg was handed a cost-free borrow and
   still lost). Missing rate aux must fall back to the punitive flat rate,
   never to zero — and the charge itself must be mutation-detectable: zero it
   and `test_financing_actually_charged` goes red.
"""
import copy
from datetime import date, timedelta

import pytest

import backtest as bt
import risk
from backtest import SimAccount

from test_ensemble_sim import synth_bars


# --------------------------------------------------------------- account_dict

def test_default_buying_power_is_cash():
    acct = SimAccount(cash=50_000.0)
    acct.positions["SPY"] = {"qty": 100, "avg_entry": 400.0}
    d = acct.account_dict({"SPY": 500.0})
    assert d["buying_power"] == 50_000.0          # cash, NOT equity-derived


def test_levered_buying_power_formula():
    acct = SimAccount(cash=0.0, margin_multiplier=1.5)
    acct.positions["SPY"] = {"qty": 100, "avg_entry": 400.0}
    d = acct.account_dict({"SPY": 500.0})
    # equity 50k, gross 50k -> bp = 50k*1.5 - 50k = 25k
    assert d["buying_power"] == pytest.approx(25_000.0)


def test_levered_buying_power_never_negative():
    acct = SimAccount(cash=-40_000.0, margin_multiplier=1.5)
    acct.positions["SPY"] = {"qty": 200, "avg_entry": 400.0}
    d = acct.account_dict({"SPY": 450.0})
    # equity 50k, gross 90k -> raw bp = 75k - 90k < 0 -> clamped
    assert d["buying_power"] == 0.0


# ---------------------------------------------------------- financing_rate_pct

def _rate_bars(vals):
    d = date(2024, 1, 1)
    rows = []
    for v in vals:
        rows.append({"ts": f"{d.isoformat()}T00:00:00Z", "close": v})
        d += timedelta(days=1)
    return {"^IRX": rows}


def _mcfg(**kw):
    m = {"multiplier": 1.5, "spread_bps_annual": 150, "flat_rate_pct": 6.0}
    m.update(kw)
    return {"risk": {"margin": m}}


def test_rate_is_asof_plus_spread():
    bars = _rate_bars([4.0, 5.0, 99.0])
    # ts between bar 2 and bar 3: must read 5.0, never the later 99.0
    got = risk.financing_rate_pct(bars, "2024-01-02T12:00:00Z", _mcfg())
    assert got == pytest.approx(5.0 + 1.5)


def test_rate_fails_closed_to_flat_when_aux_missing():
    assert risk.financing_rate_pct(None, "2024-01-02T00:00:00Z",
                                   _mcfg()) == 6.0
    assert risk.financing_rate_pct({}, "2024-01-02T00:00:00Z",
                                   _mcfg(flat_rate_pct=8.5)) == 8.5


def test_rate_fails_closed_before_first_bar():
    bars = _rate_bars([4.0])
    got = risk.financing_rate_pct(bars, "2023-12-31T00:00:00Z", _mcfg())
    assert got == 6.0                              # nothing as-of yet -> flat


# -------------------------------------------------------------- macro_blocked

def _macro_series(vals, sym="UNRATE"):
    d = date(2024, 1, 1)
    rows = []
    for v in vals:
        rows.append({"ts": f"{d.isoformat()}T00:00:00Z", "close": v})
        d += timedelta(days=1)
    return {sym: rows}


def _gcfg(enabled=True, ma_days=4, series="UNRATE"):
    return {"risk": {"macro_gate": {"enabled": enabled, "ma_days": ma_days,
                                    "series": series}}}


def test_macro_disabled_never_blocks():
    bars = _macro_series([4.0, 4.0, 4.0, 9.0])
    assert not risk.macro_blocked(bars, "2024-01-04T12:00:00Z",
                                  _gcfg(enabled=False))


def test_macro_blocks_when_latest_above_ma():
    bars = _macro_series([4.0, 4.0, 4.0, 9.0])
    assert risk.macro_blocked(bars, "2024-01-04T12:00:00Z", _gcfg())


def test_macro_permits_when_latest_below_ma():
    bars = _macro_series([9.0, 9.0, 9.0, 4.0])
    assert not risk.macro_blocked(bars, "2024-01-04T12:00:00Z", _gcfg())


def test_macro_fails_open_on_missing_or_short_series():
    assert not risk.macro_blocked(None, "2024-01-04T00:00:00Z", _gcfg())
    assert not risk.macro_blocked({}, "2024-01-04T00:00:00Z", _gcfg())
    short = _macro_series([4.0, 9.0])              # fewer than ma_days bars
    assert not risk.macro_blocked(short, "2024-01-02T12:00:00Z", _gcfg())


def test_macro_is_ts_bounded():
    """A backtest must not see a print the live bot could not have."""
    bars = _macro_series([4.0, 4.0, 4.0, 4.0, 99.0])
    # As of day 4 the 99.0 does not exist yet; window is flat -> no block.
    assert not risk.macro_blocked(bars, "2024-01-04T12:00:00Z", _gcfg())


# ------------------------------------------------- ensemble-level integration

def _cfg():
    return bt.load_config()


@pytest.fixture(scope="module")
def bars():
    cfg = bt.load_config()
    picks = ["SPY", "AAPL", "MSFT", "XOM", "JNJ"]
    ordered = [s for s in cfg["symbols"] if s in picks]
    return synth_bars(ordered)


def _strip_s64(cfg):
    """A config shaped like the pre-§64 file: no margin, no macro_gate."""
    out = copy.deepcopy(cfg)
    out["risk"].pop("margin", None)
    out["risk"].pop("macro_gate", None)
    return out


def test_shipped_default_is_byte_identical_to_pre_s64(bars):
    """Present-at-default, absent, and explicit-1.0 must all be the same bot."""
    shipped = bt.simulate_ensemble(bars, _cfg())
    absent = bt.simulate_ensemble(bars, _strip_s64(_cfg()))
    assert shipped.equity_curve == absent.equity_curve
    assert shipped.n_trades == absent.n_trades
    assert shipped.census == absent.census
    assert shipped.financing_paid_usd == 0.0 == absent.financing_paid_usd


def _levered_cfg(mult=1.5):
    cfg = _cfg()
    cfg["risk"]["margin"]["multiplier"] = mult
    # Sizing large enough that buying power, not the per-trade budget, binds —
    # otherwise leverage never engages and the test proves nothing.
    cfg["risk"]["risk_per_trade_pct"] = 400.0
    cfg["risk"]["max_position_pct"] = 400.0
    cfg["risk"]["max_portfolio_heat_pct"] = 10_000.0
    cfg["risk"]["max_open_positions"] = 8
    return cfg


def test_leverage_engages_and_is_capped(bars):
    res = bt.simulate_ensemble(bars, _levered_cfg(1.5))
    assert res.gross_exposure_max_pct > 100.0, \
        "sizing overrides never engaged leverage — the test is vacuous"
    # 1.5x cap plus one bar of drift headroom.
    assert res.gross_exposure_max_pct <= 160.0


def test_financing_actually_charged(bars):
    """Mutation target: zero the per-bar charge in simulate_ensemble and this
    must go red. Flat-rate path (no rate aux) so the charge is deterministic."""
    res = bt.simulate_ensemble(bars, _levered_cfg(1.5))
    assert res.gross_exposure_max_pct > 100.0
    assert res.financing_paid_usd > 0.0


def test_financing_lowers_the_curve(bars):
    """Same book, cheaper money -> strictly more terminal equity. Proves the
    charge flows into the curve rather than a side counter nobody reads."""
    dear = _levered_cfg(1.5)
    cheap = _levered_cfg(1.5)
    cheap["risk"]["margin"]["flat_rate_pct"] = 0.001
    r_dear = bt.simulate_ensemble(bars, dear)
    r_cheap = bt.simulate_ensemble(bars, cheap)
    assert r_dear.financing_paid_usd > r_cheap.financing_paid_usd
    assert r_dear.equity_curve[-1] < r_cheap.equity_curve[-1]


def test_unlevered_book_pays_no_financing(bars):
    res = bt.simulate_ensemble(bars, _cfg())
    assert res.financing_paid_usd == 0.0


def test_macro_gate_blocks_entries_in_ensemble(bars):
    """Wire-through proof: a permanently risk-off series must land blocks in
    the census 'macro' bucket, and the same run with the gate off must not."""
    n = len(next(iter(bars.values())))
    d0 = date(2023, 1, 1)
    rising = {"UNRATE": [
        {"ts": f"{(d0 + timedelta(days=i)).isoformat()}T00:00:00Z",
         "close": 4.0 + (0.01 * i if i > n // 2 else 0.0)}
        for i in range(n + 400)]}
    cfg = _cfg()
    cfg["risk"]["macro_gate"] = {"enabled": True, "series": "UNRATE",
                                 "ma_days": 30}
    gated = bt.simulate_ensemble(bars, cfg, macro=rising)
    plain = bt.simulate_ensemble(bars, _cfg(), macro=rising)
    assert gated.census["blocked"].get("macro", 0) > 0
    assert "macro" not in plain.census["blocked"]


# ------------------------------------------------------- gem.prepare contract

def test_gem_prepare_computes_returns_from_real_bars():
    """The s67 crash test: prepare() must accept BAR DICTS (the only thing the
    simulator ever hands it) — the first run passed bars straight into
    total_return, which wants closes, and the reachability fixture missed it
    because its universe carries no SPY spine."""
    from strategies import gem as g
    from datetime import date, timedelta
    d0 = date(2023, 1, 2)
    def mk(px0, drift):
        rows, px = [], px0
        for i in range(304):   # Jan 2 + 303d = Nov 1: last bar opens a month
            px += drift
            rows.append({"ts": f"{(d0 + timedelta(days=i)).isoformat()}"
                               f"T21:00:00+00:00", "open": px, "high": px,
                         "low": px, "close": px, "volume": 1})
        return rows
    bars = {"SPY": mk(400, 0.5), "EFA": mk(70, 0.01), "AGG": mk(95, 0.0)}
    rates = {"^IRX": [{"ts": r["ts"], "close": 5.0} for r in bars["SPY"]]}
    params = {"us": "SPY", "intl": "EFA", "bonds": "AGG", "lookback_days": 252}
    got = g.prepare(bars, params, {"_rate_bars": rates})
    assert got["eval"] is True
    assert got["returns"]["us"] is not None
    assert got["target"] == "SPY"          # strong US trend beats EFA + T-bill


def test_gem_fails_closed_without_rate_series():
    from strategies import gem as g
    from datetime import date, timedelta
    d0 = date(2023, 1, 2)
    rows = [{"ts": f"{(d0 + timedelta(days=i)).isoformat()}T21:00:00+00:00",
             "open": 1, "high": 1, "low": 1, "close": 400 + i, "volume": 1}
            for i in range(304)]
    params = {"us": "SPY", "intl": "EFA", "bonds": "AGG", "lookback_days": 252}
    got = g.prepare({"SPY": rows, "EFA": rows, "AGG": rows}, params, {})
    assert got["eval"] is True and got["target"] is None
