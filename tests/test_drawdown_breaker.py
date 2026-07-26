"""§31 — the peak-to-trough drawdown circuit breaker.

Before this, the only portfolio-level kill switch was `daily_loss_limit_pct`,
which measures ONE DAY. A book that bled 10% over three weeks — never losing
3% in a single session — passed every rail the bot had. At 5% deployment that
was academic; at 43.6% (§29) it is not.

The property this file cares about most is the exit exemption. A drawdown rail
that blocked selling would trap you in the losing book it was built to protect
you from, and it would do it precisely when getting out matters.
"""
import json
import os

import pytest

import risk


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def _cfg(dd=10.0):
    return {"risk": {
        "risk_per_trade_pct": 8.0,
        "max_position_pct": 10.0,
        "max_order_value_usd": 0,
        "max_open_positions": 0,
        "max_trades_per_day": 15,
        "max_drawdown_pct": dd,
    }}


def _acct(equity):
    return {"equity": equity, "buying_power": equity, "last_equity": equity}


# ---- the arithmetic ----

def test_drawdown_from_peak():
    assert risk.drawdown_pct(90_000, 100_000) == pytest.approx(10.0)
    assert risk.drawdown_pct(100_000, 100_000) == 0.0


def test_a_new_high_is_never_a_drawdown():
    assert risk.drawdown_pct(120_000, 100_000) == 0.0


def test_unseeded_peak_is_not_a_drawdown():
    assert risk.drawdown_pct(100_000, 0) == 0.0


# ---- the rail ----

def test_entries_blocked_at_the_limit():
    with pytest.raises(risk.RiskRejection, match="drawdown circuit breaker"):
        risk.pure_checks("buy", "AAA", 10, 100.0, _acct(90_000), {}, _cfg(),
                         peak_equity=100_000)


def test_entries_allowed_just_inside_the_limit():
    risk.pure_checks("buy", "AAA", 10, 100.0, _acct(90_500), {}, _cfg(),
                     peak_equity=100_000)


def test_EXITS_ARE_NEVER_BLOCKED():
    """The one that matters. A rail that stopped you selling during a drawdown
    would trap you in the book it exists to protect you from."""
    risk.pure_checks("sell", "AAA", 10, 100.0, _acct(50_000),
                     {"AAA": {"qty": 10, "market_value": 1000}}, _cfg(),
                     peak_equity=100_000)


def test_zero_disables_the_breaker():
    risk.pure_checks("buy", "AAA", 10, 100.0, _acct(10_000), {}, _cfg(dd=0),
                     peak_equity=100_000)


def test_absent_peak_does_not_block():
    """The sim and live both pass a peak; None means the caller opted out.
    It must not silently block every entry."""
    risk.pure_checks("buy", "AAA", 10, 100.0, _acct(50_000), {}, _cfg(),
                     peak_equity=None)


# ---- the high-water mark ----

def test_high_water_ratchets_up_only():
    assert risk.update_high_water(100_000) == 100_000
    assert risk.update_high_water(120_000) == 120_000
    assert risk.update_high_water(80_000) == 120_000, (
        "peak followed equity down — the breaker could then never fire")


def test_high_water_persists_across_restarts():
    risk.update_high_water(150_000)
    assert risk.read_high_water() == 150_000


def test_unreadable_high_water_fails_closed_then_self_heals():
    """Same polarity as the trade counter: unknown state assumes the worst.

    But it must not WEDGE — the next cycle rewrites the file from live equity,
    so a corrupt file costs one cycle of entries, not a dead bot.
    """
    os.makedirs("memory", exist_ok=True)
    with open(risk._HIGHWATER_FILE, "w") as f:
        f.write("{ this is not json")

    peak = risk.read_high_water()
    assert risk.drawdown_pct(100_000, peak) == float("inf")
    with pytest.raises(risk.RiskRejection, match="drawdown circuit breaker"):
        risk.pure_checks("buy", "AAA", 10, 100.0, _acct(100_000), {}, _cfg(),
                         peak_equity=peak)

    # self-heal
    assert risk.update_high_water(100_000) == 100_000
    risk.pure_checks("buy", "AAA", 10, 100.0, _acct(100_000), {}, _cfg(),
                     peak_equity=risk.read_high_water())


def test_high_water_write_is_atomic_and_leaves_no_litter():
    risk.update_high_water(100_000)
    leftovers = [f for f in os.listdir("memory") if ".tmp" in f]
    assert not leftovers, leftovers
    with open(risk._HIGHWATER_FILE) as f:
        assert json.load(f)["peak_equity"] == 100_000


# ---- live wiring ----

def test_live_path_ratchets_the_peak_before_checking_it():
    """A new equity high must not read as a drawdown because the file was
    written after the comparison."""
    risk.update_high_water(100_000)
    risk.pre_trade_checks("buy", "AAA", 10, 100.0, _acct(150_000), {}, _cfg())
    assert risk.read_high_water() == 150_000


def test_live_path_blocks_a_real_drawdown():
    risk.update_high_water(100_000)
    with pytest.raises(risk.RiskRejection, match="drawdown circuit breaker"):
        risk.pre_trade_checks("buy", "AAA", 1, 100.0, _acct(85_000), {}, _cfg())


def test_the_peak_keeps_tracking_while_entries_are_blocked():
    """If the ratchet only ran on successful entries the breaker could never
    clear itself once engaged."""
    risk.update_high_water(100_000)
    with pytest.raises(risk.RiskRejection):
        risk.pre_trade_checks("buy", "AAA", 1, 100.0, _acct(85_000), {}, _cfg())
    assert risk.read_high_water() == 100_000    # not lowered by the failed entry


# ---- sim/live agreement (divergence #9 prevention) ----

def test_the_backtester_enforces_the_same_rail():
    import inspect

    import backtest as bt

    src = inspect.getsource(bt)
    assert src.count("sim_peak = max(sim_peak, acct.equity(last_close))") == 2, (
        "a simulator path does not track the equity peak — the drawdown rail "
        "would exist live and not in the backtest, which is divergence #9")
    assert src.count("peak_equity=sim_peak") == 2


def test_both_sides_use_the_same_drawdown_arithmetic():
    """Live reads a file, the sim keeps a running max — but the percentage
    must be computed by one function, not two implementations."""
    import inspect

    assert "def drawdown_pct" in inspect.getsource(risk)
    assert "drawdown_pct(account[\"equity\"], peak_equity)" in inspect.getsource(
        risk.pure_checks)
