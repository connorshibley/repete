"""Second-source price cross-check (2026-07-21) — vendor-risk rail.

The 2026-07-16 incident class: ONE data vendor quietly serves wrong prices
and nothing independent disagrees. Every cycle, SPY's latest daily close from
Alpaca is compared against yfinance's close for the SAME session; divergence
beyond risk.data_crosscheck.max_divergence_pct means one vendor is lying and
we can't know which — so ENTRIES are blocked for the cycle (exits and
protective actions are never blocked).

Fail-open semantics are deliberate and narrow: a yfinance outage or a
no-overlap session returns None with a log line only (a vendor outage must
not spam the degradation SLO). The rail fires ONLY when both vendors report
the same session and disagree.
"""
import logging

log = logging.getLogger("datacheck")


def divergence_reason(alpaca_ts: str, alpaca_close: float,
                      other_ts: str, other_close: float,
                      max_divergence_pct: float) -> str | None:
    """Pure comparison: same session, both closes present. Reason or None."""
    if alpaca_ts[:10] != other_ts[:10]:
        return None  # different sessions — nothing comparable
    if not alpaca_close or not other_close:
        return None
    div = abs(alpaca_close - other_close) / other_close * 100
    if div > max_divergence_pct:
        return (f"data_crosscheck: SPY close diverges {div:.2f}% between "
                f"vendors on {alpaca_ts[:10]} (alpaca {alpaca_close:.2f} vs "
                f"yfinance {other_close:.2f}, cap {max_divergence_pct}%) — "
                f"one feed is wrong, entries blocked")
    return None


def _yf_spy_close() -> tuple[str, float] | None:
    """(iso_date, close) of yfinance's latest SPY daily bar, or None."""
    try:
        import yfinance as yf
        hist = yf.Ticker("SPY").history(period="5d", interval="1d")
        if hist is None or hist.empty:
            return None
        ts = hist.index[-1]
        return (ts.strftime("%Y-%m-%d"), float(hist["Close"].iloc[-1]))
    except Exception as e:  # noqa: BLE001 — outage = no check, not a failure
        log.warning("yfinance cross-check unavailable: %s", e)
        return None


def crosscheck_spy(spy_bars: list[dict], cfg: dict) -> str | None:
    """Divergence reason when both vendors report the same session and
    disagree beyond the cap; None otherwise (including any outage)."""
    ccfg = cfg["risk"].get("data_crosscheck") or {}
    if not ccfg.get("enabled") or not spy_bars:
        return None
    other = _yf_spy_close()
    if other is None:
        return None
    return divergence_reason(spy_bars[-1]["ts"], spy_bars[-1]["close"],
                             other[0], other[1],
                             float(ccfg.get("max_divergence_pct", 0.5)))
