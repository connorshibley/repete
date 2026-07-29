
import regime
from conftest import make_bars


RCFG = {"sma_period": 5, "vol_period": 3, "vol_low": 0.15, "vol_high": 0.25}


def _bars_with_vol(base, drift, wiggle, n=10):
    closes, price = [], base
    for i in range(n):
        price = price * (1 + drift + (wiggle if i % 2 == 0 else -wiggle))
        closes.append(round(price, 4))
    return make_bars(closes)


def test_uptrend_low_vol():
    bars = _bars_with_vol(100, 0.004, 0.001)
    r = regime.compute_regime(bars, RCFG)
    assert r["trend"] == "up" and r["vol"] == "low"
    assert r["label"] == "up/low"


def test_downtrend_high_vol():
    bars = _bars_with_vol(100, -0.01, 0.03)
    r = regime.compute_regime(bars, RCFG)
    assert r["trend"] == "down" and r["vol"] == "high"


def test_mid_vol_bucket():
    # wiggle tuned so annualized vol lands between 15% and 25%
    bars = _bars_with_vol(100, 0.004, 0.011)
    r = regime.compute_regime(bars, RCFG)
    assert 0.15 <= r["ann_vol"] <= 0.25
    assert r["vol"] == "mid"


def test_insufficient_data_returns_none():
    assert regime.compute_regime(make_bars([100, 101]), RCFG) is None


def test_describe():
    r = regime.compute_regime(_bars_with_vol(100, 0.004, 0.001), RCFG)
    assert "up/low" in regime.describe(r)
    assert regime.describe(None).startswith("unknown")
