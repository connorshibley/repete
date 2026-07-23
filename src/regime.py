"""Deterministic market-regime tagging from SPY daily bars.

Cheap, pure, and computed from data the cycle already fetches. Lessons and
judgments are tagged with the regime so the learning loop can discount
evidence gathered under different market conditions. Fixed vol thresholds
(not trailing terciles) because the live loop only fetches ~100 bars.
"""
import math

TRADING_DAYS = 252


def compute_regime(spy_bars: list[dict], rcfg: dict) -> dict | None:
    """Regime features from SPY daily bars, or None on insufficient data.

    trend: "up" if last close > SMA(sma_period) else "down"
    vol:   annualized stdev of the last vol_period daily log returns,
           bucketed by fixed thresholds -> "low" | "mid" | "high"
    label: "up/low" etc. — the compact tag stored on records.
    """
    sma_p = rcfg["sma_period"]
    vol_p = rcfg["vol_period"]
    # vol_p must leave at least 2 returns: the variance below divides by
    # (len(rets) - 1), so vol_p <= 1 was a ZeroDivisionError inside the cycle.
    # A nonsensical period means "no regime", never a crash.
    if sma_p < 1 or vol_p < 2:
        return None
    closes = [b["close"] for b in spy_bars]
    if len(closes) < max(sma_p, vol_p + 1):
        return None

    close = closes[-1]
    sma = sum(closes[-sma_p:]) / sma_p
    trend = "up" if close > sma else "down"

    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(len(closes) - vol_p, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    ann_vol = math.sqrt(var) * math.sqrt(TRADING_DAYS)

    if ann_vol < rcfg["vol_low"]:
        vol = "low"
    elif ann_vol > rcfg["vol_high"]:
        vol = "high"
    else:
        vol = "mid"

    return {"trend": trend, "vol": vol, "label": f"{trend}/{vol}",
            "spy_close": round(close, 2), "spy_sma": round(sma, 2),
            "ann_vol": round(ann_vol, 4)}


def describe(regime: dict | None) -> str:
    """Human/LLM-readable one-liner."""
    if not regime:
        return "unknown (regime data unavailable)"
    cmp_word = ">" if regime["trend"] == "up" else "<"
    return (f"{regime['label']} (SPY {regime['spy_close']} {cmp_word} "
            f"SMA {regime['spy_sma']}, vol {regime['ann_vol']:.0%})")
