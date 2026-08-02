"""HALT has two modes, and they must not be able to drift into each other.

Why this file exists (2026-08-02)
---------------------------------
`scripts/halt.py` promised that under a HALT open positions "keep running to
their normal stops and exits". They did not. `_bootstrap_cycle` returned before
a single signal was evaluated, so the agent evaluated NO exits and only the
broker's own bracket legs protected the book. PR #72 corrected the wording;
this change makes the original promise real, as a mode:

    exits   entries blocked, cycle runs, positions still managed  (new default)
    freeze  the cycle does not run at all                         (was the only

The two failure directions are opposite and both bad, so both are pinned here:

  * `exits` failing to block an entry is the bot trading through a kill switch.
  * `freeze` quietly running anything is the bot touching a broker the operator
    halted BECAUSE they did not trust it.

And one silent direction that would be worst of all: a HALT engaged before
modes existed, or by an older build, must never be re-read as "keep trading".
It reads as `freeze`, and `test_a_legacy_halt_file_reads_as_freeze` is what
holds that.
"""
import os

import pytest

import main
import risk
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import FakeCycleBroker, cycle_env  # noqa: F401

BUY_CLOSES = [10] * 6 + [9, 9, 9, 20]      # SMA3 crosses ABOVE SMA5 on the last bar
SELL_CLOSES = BUY_CLOSES + [20, 4, 4]      # ...then back below: an exit signal
OLD_ENTRY = "2026-01-05T21:00:00+00:00"    # older than min_holding_days, so the
                                           # swing guard is not what blocks a sell


def _halt(mode: str | None, reason="test"):
    """Write a HALT file. `None` writes a LEGACY file with no mode marker."""
    if mode is None:
        with open(risk.HALT_FILE, "w") as f:
            f.write("2026-08-02T00:00:00+00:00 — MANUAL — legacy halt\n"
                    "Delete this file to re-enable trading.\n")
    else:
        risk.engage_halt(f"MANUAL — {reason}", mode=mode)


def _seed_open_position(cfg, symbol="SPY", qty=50, price=20.0):
    """An open position the ledger knows about, entered long enough ago that
    the swing guard permits an exit."""
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision(symbol, "buy", "seeded", {}, None, executed=True,
                     order={"id": "seed-1", "symbol": symbol, "qty": qty},
                     entry_price=price, qty=qty, entry_ts=OLD_ENTRY,
                     strategy="ma_crossover")
    return {symbol: {"qty": qty, "market_value": qty * price,
                     "avg_entry": price, "unrealized_pl": 0.0}}


def _events(cfg):
    return [r.get("event") for r in
            Ledger(cfg["memory"]["ledger_path"]).all_records()
            if r.get("type") == "event"]


# ---------------------------------------------------------------- the mode read

def test_no_halt_file_has_no_mode():
    assert risk.halt_mode() is None


def test_each_mode_round_trips(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for mode in (risk.HALT_MODE_EXITS, risk.HALT_MODE_FREEZE):
        risk.engage_halt("why", mode=mode)
        assert risk.halt_mode() == mode
        os.remove(risk.HALT_FILE)


def test_a_legacy_halt_file_reads_as_freeze(tmp_path, monkeypatch):
    """THE one that protects an operator who is not in this conversation.

    Someone who engaged a HALT before modes existed believed it stopped
    everything. Deploying this must not silently convert their halt into
    "actually, keep trading" on the strength of a line their file never had.
    """
    monkeypatch.chdir(tmp_path)
    _halt(None)
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE


def test_an_unrecognised_mode_reads_as_freeze(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with open(risk.HALT_FILE, "w") as f:
        f.write("ts — reason\nmode: exit\n")     # near-miss typo, not "exits"
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE


def test_engage_halt_defaults_to_freeze_and_rejects_junk(tmp_path, monkeypatch):
    """The FUNCTION default is the conservative one, so a caller that never
    thought about modes cannot accidentally leave the bot trading."""
    monkeypatch.chdir(tmp_path)
    risk.engage_halt("no mode given")
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE
    os.remove(risk.HALT_FILE)
    risk.engage_halt("nonsense mode", mode="whatever")
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE


# ------------------------------------------------------------------- the rail

def test_the_halt_rail_blocks_a_buy_but_never_a_sell(tmp_path, monkeypatch):
    """Under `exits` this check is reachable for the first time. Unguarded it
    would refuse the very sells the mode exists to allow."""
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    _halt(risk.HALT_MODE_EXITS)
    acct = {"equity": 100_000.0, "buying_power": 100_000.0,
            "last_equity": 100_000.0}
    cfg = {"risk": {"risk_per_trade_pct": 8.0, "max_position_pct": 10.0,
                    "max_order_value_usd": 0, "max_open_positions": 0,
                    "max_trades_per_day": 15, "min_holding_days": 2}}

    with pytest.raises(risk.RiskRejection) as ei:
        risk.pre_trade_checks("buy", "SPY", 10, 100.0, acct, {}, cfg)
    assert ei.value.rail == "halt"

    risk.pre_trade_checks("sell", "SPY", 10, 100.0, acct,
                          {"SPY": {"market_value": 1000.0}}, cfg,
                          entry_ts=OLD_ENTRY)          # must not raise


# ------------------------------------------------------------- end to end

def test_freeze_skips_the_cycle_entirely(cycle_env):
    """Today's behaviour, pinned. Adding a mode must not weaken the one that
    already existed."""
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    _halt(risk.HALT_MODE_FREEZE)

    main.run_cycle()

    assert broker.submitted == []
    assert "halted_cycle_skipped" in _events(cfg)
    assert "halted_exits_only" not in _events(cfg)


def test_a_legacy_halt_file_still_skips_the_cycle(cycle_env):
    """The compatibility guarantee, proven at the cycle rather than the parser."""
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    _halt(None)

    main.run_cycle()

    assert broker.submitted == []
    assert "halted_cycle_skipped" in _events(cfg)


def test_exits_mode_runs_the_cycle_but_opens_nothing(cycle_env):
    """The cycle RAN — proven by its own event — and still placed no order, on
    bars that are a textbook entry signal.

    Asserted at the broker, not at the block flag: `entries_blocked_reason`
    being set is not evidence that nothing got through to `market_order`.
    """
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    _halt(risk.HALT_MODE_EXITS)

    main.run_cycle()

    assert broker.submitted == []
    assert "halted_exits_only" in _events(cfg)
    assert "halted_cycle_skipped" not in _events(cfg)


def test_the_same_bars_DO_enter_without_a_halt(cycle_env):
    """The contrast that makes the test above mean something: without the HALT
    these bars buy. Otherwise 'no order' could just be a signal that never fired.
    """
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))

    main.run_cycle()

    assert len(broker.submitted) == 1
    assert broker.submitted[0]["side"] == "buy"


def test_a_blocked_entry_records_the_halt_rail(cycle_env):
    """Which rail refused it, queryable — not prose in `detail`."""
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    _halt(risk.HALT_MODE_EXITS)

    main.run_cycle()

    rails = [r.get("rail") for r in
             Ledger(cfg["memory"]["ledger_path"]).all_records()
             if r.get("type") == "decision" and not r.get("executed")]
    assert "halt" in rails


def test_an_exit_still_executes_under_an_exits_halt(cycle_env):
    """THE point of the whole change: a held position is still worked.

    Before this, a halted bot left every open position to its broker-side
    bracket legs and nothing else — while halt.py claimed otherwise.
    """
    cfg, install = cycle_env
    positions = _seed_open_position(cfg)
    broker = install(FakeCycleBroker(make_bars(SELL_CLOSES),
                                     positions=positions))
    _halt(risk.HALT_MODE_EXITS)

    main.run_cycle()

    sells = [o for o in broker.submitted if o.get("side") == "sell"]
    assert sells, "an exits-mode halt must still close positions"
    assert all(o.get("side") != "buy" for o in broker.submitted)


def test_a_freeze_does_NOT_execute_that_same_exit(cycle_env):
    """Same position, same bars, opposite mode — so the exit above is the mode
    working and not merely a cycle that would have sold anyway."""
    cfg, install = cycle_env
    positions = _seed_open_position(cfg)
    broker = install(FakeCycleBroker(make_bars(SELL_CLOSES),
                                     positions=positions))
    _halt(risk.HALT_MODE_FREEZE)

    main.run_cycle()

    assert broker.submitted == []


# -------------------------------------------------- who engages which mode

def test_the_daily_loss_kill_switch_engages_a_FREEZE(tmp_path, monkeypatch):
    """Load-bearing, not stylistic.

    `_kill_switch_fired` engages HALT before flattening precisely so "the next
    scheduled cycle [does not] re-enter this path". Under `exits` the cycle
    WOULD run again, re-enter, and re-call flatten_all() every cycle while the
    daily loss stood — returning early before any exit ran, so it would not even
    buy the exits that mode is for.
    """
    monkeypatch.chdir(tmp_path)
    os.makedirs("memory", exist_ok=True)
    cfg = {"risk": {"daily_loss_limit_pct": 5.0},
           "memory": {"ledger_path": "memory/ledger.jsonl"}}
    account = {"equity": 90_000.0, "last_equity": 100_000.0}

    class FlatBroker:
        flattened = False

        def flatten_all(self):
            FlatBroker.flattened = True

    assert main._kill_switch_fired(FlatBroker(), Ledger("memory/ledger.jsonl"),
                                   account, cfg) is True
    assert FlatBroker.flattened
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE


def test_the_operator_cli_engages_EXITS_by_default_and_freeze_on_request(
        tmp_path, monkeypatch):
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "halt_cli_modes", os.path.join(root, "scripts", "halt.py"))
    halt_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(halt_cli)           # chdirs to the real repo root

    monkeypatch.chdir(tmp_path)                 # ...so move back out, every time
    (tmp_path / "memory").mkdir()
    (tmp_path / "config.yaml").write_text(
        "memory:\n  ledger_path: memory/ledger.jsonl\n")
    monkeypatch.setattr(halt_cli, "_load_env", lambda: None)

    assert halt_cli.main(["the", "market", "has", "gone", "mad"]) == 0
    assert risk.halt_mode() == risk.HALT_MODE_EXITS
    os.remove(risk.HALT_FILE)

    assert halt_cli.main(["--freeze", "broker", "sending", "bad", "fills"]) == 0
    assert risk.halt_mode() == risk.HALT_MODE_FREEZE


def test_re_engaging_does_not_silently_change_a_live_halts_mode(
        tmp_path, monkeypatch):
    """Escalating or relaxing an ENGAGED kill switch while reporting "already
    engaged" is the surprise halt.py exists to prevent. Clear it and re-engage.
    """
    import importlib.util
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "halt_cli_modes2", os.path.join(root, "scripts", "halt.py"))
    halt_cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(halt_cli)

    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir()
    (tmp_path / "config.yaml").write_text(
        "memory:\n  ledger_path: memory/ledger.jsonl\n")
    monkeypatch.setattr(halt_cli, "_load_env", lambda: None)

    halt_cli.main(["first", "reason"])
    assert risk.halt_mode() == risk.HALT_MODE_EXITS
    halt_cli.main(["--freeze", "second", "reason"])
    assert risk.halt_mode() == risk.HALT_MODE_EXITS      # unchanged
    assert "first reason" in open(risk.HALT_FILE).read()


# ------------------------------------------- the halted cycle stays cheap

def test_an_exits_halt_skips_the_entry_side_news_and_llm_work(cycle_env,
                                                              monkeypatch):
    """News, nominations and their LLM pass feed ENTRIES only, which are
    refused — so running them spends budget on decisions already made, during a
    period the operator has declared abnormal."""
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES),
                            positions=_seed_open_position(cfg)))
    called = []
    monkeypatch.setattr(main, "_market_context",
                        lambda *a, **k: called.append(1) or ({}, {}, ["SPY"]))
    _halt(risk.HALT_MODE_EXITS)

    main.run_cycle()

    assert called == [], "_market_context must not run under an exits halt"


def test_a_normal_cycle_STILL_does_the_entry_side_work(cycle_env, monkeypatch):
    """The contrast: the skip is conditional, not a deletion."""
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    called = []
    real = main._market_context
    monkeypatch.setattr(main, "_market_context",
                        lambda *a, **k: called.append(1) or real(*a, **k))

    main.run_cycle()

    assert called, "_market_context must still run when not halted"
