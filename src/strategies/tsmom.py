"""Time-series momentum (Moskowitz/Ooi/Pedersen 2012): buy when the asset's
own trailing return is positive AND price sits above a long trend filter;
exit when the momentum sign flips.

Research note (2026): the effect is robust in the academic record across a
century of data, but recent daily-ETF out-of-sample evidence is weak — this
strategy must pass the walk-forward enablement gate before going live.
"""
from strategies.base import Signal, sma, total_return

NAME = "tsmom"
NEEDS_CROSS_SECTION = True  # only for the optional index gate in prepare()


def required_lookback(params: dict) -> int:
    return max(params["momentum_bars"] + params.get("skip_bars", 0),
               params["trend_sma_period"],
               params.get("index_sma_period", 0)) + 1


def prepare(all_bars: dict, params: dict) -> dict:
    """Optional index-regime gate: entries allowed only while the index
    (default SPY) closes above its SMA(index_sma_period). Inactive when the
    param is absent/0, and fails OPEN on insufficient index history so the
    ungated baseline behavior is preserved. Exits are never gated."""
    period = params.get("index_sma_period", 0)
    if not period:
        return {"index_ok": True}
    idx_symbol = params.get("index_symbol", "SPY")
    closes = [b["close"] for b in all_bars.get(idx_symbol, [])]
    if len(closes) < period:
        return {"index_ok": True}
    return {"index_ok": closes[-1] > sma(closes, period),
            "index_symbol": idx_symbol, "index_sma_period": period}


def generate(symbol: str, bars: list[dict], params: dict, holding: bool,
             cross_section=None) -> Signal:
    need = required_lookback(params)
    closes = [b["close"] for b in bars]
    if len(closes) < need:
        return Signal(symbol, "hold",
                      f"insufficient history ({len(closes)} bars, need {need})",
                      strategy=NAME)

    mom = total_return(closes, params["momentum_bars"], params.get("skip_bars", 0))
    trend_sma = sma(closes, params["trend_sma_period"])
    close = closes[-1]
    ind = {"close": round(close, 2), "momentum_pct": round(mom * 100, 2),
           f"sma{params['trend_sma_period']}": round(trend_sma, 2)}

    if not holding and mom > 0 and close > trend_sma:
        gate = cross_section or {}
        if not gate.get("index_ok", True):
            return Signal(symbol, "hold",
                          f"index gate: {gate.get('index_symbol', 'SPY')} below its "
                          f"SMA{gate.get('index_sma_period')} — long momentum entries "
                          "blocked in a down regime", ind, NAME)
        return Signal(symbol, "buy",
                      f"{params['momentum_bars']}-bar momentum {mom:+.1%} with price "
                      f"above SMA{params['trend_sma_period']} — trend intact", ind, NAME)
    if holding and (mom <= 0 or close < trend_sma):
        why = "momentum flipped negative" if mom <= 0 else \
              f"price broke below SMA{params['trend_sma_period']}"
        return Signal(symbol, "sell", f"{why} — trend over, exiting", ind, NAME)

    state = "holding position" if holding else "flat"
    return Signal(symbol, "hold", f"no tsmom change ({state})", ind, NAME)
