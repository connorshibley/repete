"""Midday opportunity scan (src/opportunity_scan.py) — alerts, never trades.

The single most important test here is `test_module_has_no_order_call_path`.
This module reads a STILL-FORMING bar, which is an input no gate has ever
measured (§19a declined trading on it deliberately). Looking at that input is
safe; acting on it is not. So "it cannot trade" must be a structural property
of the module, not a promise in a docstring.
"""
import opportunity_scan as osc


CFG = {
    "symbols": ["AAA", "BBB", "CCC"],
    "strategy": {"timeframe": "1Day", "lookback_bars": 100},
    "strategies": {
        "meanrev": {"enabled": True, "priority": 1, "rsi_period": 2,
                    "rsi_buy_below": 10, "trend_sma_period": 20,
                    "exit_sma_period": 5, "max_hold_days": 7},
    },
    "risk": {"min_holding_days": 2},
}


def bars(closes):
    from datetime import date, timedelta
    out, d = [], date(2025, 1, 1)
    for c in closes:
        out.append({"ts": f"{d.isoformat()}T21:00:00+00:00", "open": c,
                    "high": c * 1.01, "low": c * 0.99, "close": c,
                    "volume": 1_000_000})
        d += timedelta(days=1)
    return out


def dip_bars():
    """A real meanrev entry: steep uptrend, then a two-day flush.

    meanrev needs >= 31 bars (RSI history 30 + 1), RSI(2) below 10, AND price
    still above the trend SMA. Verified values at the last bar: RSI2 8.2,
    close 226 vs SMA20 223.15 — oversold, uptrend intact."""
    closes = [100 + i * 4 for i in range(40)]
    closes += [closes[-1] - 15, closes[-1] - 30]
    return bars(closes)


def calm_bars():
    """Same uptrend with no flush — nothing oversold, so no signal."""
    return bars([100 + i * 4 for i in range(42)])


# ---------------- THE safety property ----------------

def test_module_has_no_order_call_path():
    """Structural, not conventional. If someone later adds an order call here,
    this fails — which is the point, because this module reasons about a
    partial bar that no gate has validated for trading."""
    import inspect
    src = inspect.getsource(osc)
    forbidden = ["submit_order", "submit(", ".buy(", ".sell(",
                 "place_order", "cancel_open_orders", "log_decision",
                 "close_trade", "record_trade"]
    for verb in forbidden:
        assert verb not in src, f"opportunity_scan must not be able to {verb}"


def test_scan_returns_text_not_an_action():
    out = osc.scan({"AAA": dip_bars()}, CFG, notify=False)
    assert isinstance(out, str)


# ---------------- finding setups ----------------

def test_finds_a_qualifying_setup():
    found = osc.find_setups({"AAA": dip_bars()}, CFG)
    assert len(found) == 1
    assert found[0]["symbol"] == "AAA"
    assert found[0]["strategy"] == "meanrev"
    assert found[0]["reason"]


def test_finds_nothing_when_nothing_qualifies():
    assert osc.find_setups({"AAA": calm_bars()}, CFG) == []


def test_already_held_symbols_are_skipped():
    """A position you already own is not a new opportunity."""
    assert osc.find_setups({"AAA": dip_bars()}, CFG, held={"AAA"}) == []


def test_only_enabled_strategies_are_scanned():
    cfg = {**CFG, "strategies": {**CFG["strategies"]}}
    cfg["strategies"]["meanrev"] = {**cfg["strategies"]["meanrev"],
                                    "enabled": False}
    assert osc.find_setups({"AAA": dip_bars()}, cfg) == []


def test_symbols_outside_the_configured_universe_are_ignored():
    found = osc.find_setups({"ZZZ": dip_bars()}, CFG)
    assert found == []


def test_a_broken_symbol_does_not_sink_the_scan():
    """One bad series must not stop the others being reported."""
    found = osc.find_setups({"AAA": dip_bars(), "BBB": []}, CFG)
    assert [f["symbol"] for f in found] == ["AAA"]


# ---------------- the message ----------------

def test_alert_states_that_nothing_was_traded():
    msg = osc.format_alert([{"symbol": "AAA", "strategy": "meanrev",
                             "reason": "oversold dip", "indicators": {}}])
    assert "nothing traded yet" in msg
    assert "places no orders" in msg


def test_alert_flags_the_findings_as_provisional():
    """It must not imply certainty — the bar is still moving."""
    msg = osc.format_alert([{"symbol": "AAA", "strategy": "meanrev",
                             "reason": "oversold dip", "indicators": {}}])
    assert "STILL-FORMING" in msg
    assert "may not survive" in msg
    assert "15:45 ET" in msg


def test_alert_says_the_rails_still_apply():
    msg = osc.format_alert([{"symbol": "AAA", "strategy": "meanrev",
                             "reason": "r", "indicators": {}}])
    assert "risk rail" in msg


def test_empty_findings_produce_no_message():
    """No setups means silence, not a daily 'nothing happened' ping."""
    assert osc.format_alert([]) == ""
    assert osc.scan({"AAA": calm_bars()}, CFG, notify=False) == ""


def test_long_lists_are_truncated_with_a_count():
    many = [{"symbol": f"S{i}", "strategy": "meanrev", "reason": "r",
             "indicators": {}} for i in range(14)]
    msg = osc.format_alert(many)
    assert "and 4 more" in msg


# ---------------- scheduling ----------------

def test_scheduler_runs_it_midday_and_it_is_not_the_trading_cycle():
    import importlib.util, os
    path = os.path.join(os.path.dirname(__file__), "..", "scripts",
                        "scheduler.py")
    spec = importlib.util.spec_from_file_location("sched_osc", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    jobs = {j[0]: j for j in mod.JOBS}
    assert "midday-scan" in jobs
    _, weekdays, hour, minute, argv = jobs["midday-scan"]
    assert (hour, minute) == (12, 0)
    assert list(weekdays) == [0, 1, 2, 3, 4]
    joined = " ".join(argv)
    assert "opportunity_scan" in joined
    assert "src/main.py" not in joined, "the midday job must not run the trader"
