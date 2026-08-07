"""End-to-end run_cycle() integration tests with a fake broker — the whole
orchestrator path (signal -> LLM fallback -> rails -> bracket execution ->
ledger -> reconciliation on the next cycle) exercised fully offline.
"""
import json

import yaml

import pytest

import journal
import main
import risk
import sitepaths
from ledger import Ledger

from conftest import make_bars
from fakes.broker import ConformantBroker

BUY_CLOSES = [10] * 6 + [9, 9, 9, 20]  # SMA3 crosses above SMA5 on the last bar


# `FakeCycleBroker` is now an ALIAS, not a class. Nine test files import this
# name; making the shared fake conformant here fixes all of them at once rather
# than repointing nine import lines and leaving the tenth behind.
#
# The old inline class omitted `latest_price`, `open_stop_orders` and
# `replace_stop`. The first of those mattered: src/main.py's entry drift guard
# calls `latest_price`, so every run_cycle() here silently took the guard's
# fail-OPEN branch and wrote a `degradation` event that had nothing to do with
# the code under test. See tests/fakes/broker.py for the full account.
FakeCycleBroker = ConformantBroker


@pytest.fixture
def cycle_env(tmp_path, monkeypatch, cfg):
    """chdir to tmp, write a config.yaml there, patch main.Broker."""
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["brackets"]["atr_period"] = 3  # fixture bars are short
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    def _install(broker):
        monkeypatch.setattr(main, "Broker", lambda cfg: broker)
        return broker
    return cfg, _install


def test_cycle_executes_bracket_buy(cycle_env):
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))

    main.run_cycle()

    assert len(broker.submitted) == 1
    order = broker.submitted[0]
    assert order["order_class"] == "bracket"
    # entry 20, ATR(3) of flat bars = (0+0+11)/3; stop = 20 - 2*ATR, tp = 20 + 3*ATR
    atr = 11 / 3
    assert order["stop_price"] == pytest.approx(round(20 - 2 * atr, 2))
    assert order["take_profit_price"] == pytest.approx(round(20 + 3 * atr, 2))
    assert order["qty"] == 50  # 1% of 100k at $20

    led = Ledger(cfg["memory"]["ledger_path"])
    open_trades = led.open_buys()
    assert len(open_trades) == 1
    rec = next(iter(open_trades.values()))
    assert rec["executed"] and rec["order"]["leg_ids"] == ["leg-stop", "leg-tp"]


def test_cycle_falls_back_to_plain_order_when_brackets_disabled(cycle_env):
    cfg, install = cycle_env
    cfg["risk"]["brackets"]["enabled"] = False
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))

    main.run_cycle()

    assert len(broker.submitted) == 1
    assert "order_class" not in broker.submitted[0]  # plain market order


def test_next_cycle_reconciles_broker_side_stop_fill(cycle_env):
    cfg, install = cycle_env
    # Cycle 1: bracket buy executes.
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    # Cycle 2: position vanished broker-side; the stop leg shows filled.
    hold_bars = make_bars(BUY_CLOSES + [20], start_day=2)  # no new crossover
    broker2 = install(FakeCycleBroker(hold_bars, positions={}, orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                    "filled_avg_price": 20.0, "filled_qty": 50, "legs": []},
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 12.67,
                     "filled_qty": 50, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                   "type": "OrderType.LIMIT", "filled_avg_price": None,
                   "filled_qty": 0, "legs": []},
    }))
    main.run_cycle()

    assert broker2.submitted == []  # reconciliation must not place orders
    led = Ledger(cfg["memory"]["ledger_path"])
    assert led.open_buys() == {}
    closed = led.closed_trades()
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "stop_loss"
    assert closed[0]["exit_price"] == 12.67


def test_cycle_logs_judgment_on_executed_buy(cycle_env, cfg):
    _, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    from judgments import JudgmentStore
    js = JudgmentStore(cfg["learning"]["judgments_path"]).replay()
    assert len(js) == 1
    j = next(iter(js.values()))
    assert j["kind"] == "llm" and j["executed"] and j["verdict"] == "approve"
    assert j["symbol"] == "SPY" and j["price_at_decision"] == 20
    assert j["stop_price"] is not None  # bracket snapshot captured


def test_cycle_logs_rails_judgment_on_rejection(cycle_env, cfg, monkeypatch):
    _, install = cycle_env
    monkeypatch.setattr(main.risk, "size_order", lambda *a, **k: 0)  # forces qty=0 rejection
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    from judgments import JudgmentStore
    js = JudgmentStore(cfg["learning"]["judgments_path"]).replay()
    j = next(iter(js.values()))
    assert j["kind"] == "rails" and not j["executed"]
    assert j["verdict"] == "rails_reject"


def test_cycle_logs_veto_judgment(cycle_env, cfg, monkeypatch):
    _, install = cycle_env
    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "veto", "scale": 1.0,
                                         "reasoning": "test veto"})
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()

    assert broker.submitted == []  # veto blocked the order
    from judgments import JudgmentStore
    js = JudgmentStore(cfg["learning"]["judgments_path"]).replay()
    j = next(iter(js.values()))
    assert j["verdict"] == "veto" and j["kind"] == "llm" and not j["executed"]


def test_cycle_learning_pass_marks_closed_trade_evaluated(cycle_env, cfg):
    # Cycle 1: buy. Cycle 2: broker-side stop fill -> reconcile close ->
    # inline learning pass marks the closed trade evaluated (no LLM, no lessons).
    _, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    hold_bars = make_bars(BUY_CLOSES + [20], start_day=2)
    install(FakeCycleBroker(hold_bars, positions={}, orders={
        "entry-1": {"id": "entry-1", "status": "OrderStatus.FILLED",
                    "filled_avg_price": 20.0, "filled_qty": 50, "legs": []},
        "leg-stop": {"id": "leg-stop", "status": "OrderStatus.FILLED",
                     "type": "OrderType.STOP", "filled_avg_price": 12.67,
                     "filled_qty": 50, "legs": []},
        "leg-tp": {"id": "leg-tp", "status": "OrderStatus.CANCELED",
                   "type": "OrderType.LIMIT", "filled_avg_price": None,
                   "filled_qty": 0, "legs": []},
    }))
    main.run_cycle()

    from lessons import LessonStore
    store = LessonStore(cfg["learning"]["lessons_path"])
    assert len(store.evaluated_trade_ids()) == 1
    # the realized judgment from cycle 1's approve resolved against the close
    from judgments import JudgmentStore
    js = JudgmentStore(cfg["learning"]["judgments_path"]).replay()
    j = next(iter(js.values()))
    assert j["resolution"] is not None
    assert j["resolution"]["assessment"] == "bad_approve"  # stop-out = loss


def _tsmom_cfg(cfg):
    """Enable a second strategy with tiny lookbacks that fires on BUY_CLOSES...
    actually on a steady-uptrend fixture."""
    cfg["strategies"] = {
        "ma_crossover": {"enabled": True, "priority": 1,
                         "fast_period": 3, "slow_period": 5},
        "tsmom": {"enabled": True, "priority": 2, "momentum_bars": 3,
                  "skip_bars": 0, "trend_sma_period": 5},
    }
    return cfg


def test_ensemble_priority_winner_takes_ownership(cycle_env, cfg):
    # Uptrend bars: tsmom says buy, ma_crossover says hold (no fresh cross).
    # tsmom (priority 2) enters because priority 1 produced no buy.
    cfg2, install = cycle_env
    _tsmom_cfg(cfg2)
    with open("config.yaml", "w") as f:
        import yaml
        yaml.safe_dump(cfg2, f)
    up_bars = make_bars([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    broker = install(FakeCycleBroker(up_bars))
    main.run_cycle()

    assert len(broker.submitted) == 1  # exactly one order despite two strategies
    led = Ledger(cfg2["memory"]["ledger_path"])
    rec = next(iter(led.open_buys().values()))
    assert rec["strategy"] == "tsmom"  # ownership tagged


def test_ensemble_exit_is_owner_only(cycle_env, cfg):
    # Position owned by tsmom; bars that would make ma_crossover sell must
    # not exit it — only tsmom's own exit logic runs.
    cfg2, install = cycle_env
    _tsmom_cfg(cfg2)
    with open("config.yaml", "w") as f:
        import yaml
        yaml.safe_dump(cfg2, f)
    up_bars = make_bars([10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
    install(FakeCycleBroker(up_bars))
    main.run_cycle()  # tsmom enters SPY

    # Next cycle: still uptrending (tsmom stays in) even though a fresh
    # ma-style crossover-down shape appears late in the series.
    hold_bars = make_bars([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
                          start_day=2)
    broker2 = install(FakeCycleBroker(hold_bars,
                                      positions={"SPY": {"qty": 50,
                                                         "market_value": 1000.0}}))
    main.run_cycle()
    assert broker2.submitted == []  # no sell: owner (tsmom) sees intact trend
    led = Ledger(cfg2["memory"]["ledger_path"])
    holds = [r for r in led.all_records()
             if r["type"] == "decision" and r["action"] == "hold"
             and r["symbol"] == "SPY" and r.get("strategy") == "tsmom"]
    assert holds  # the hold decision is attributed to the owning strategy


def test_portfolio_heat_counts_same_cycle_entries(cycle_env, cfg):
    """Regression: the portfolio-heat cap must count entries executed earlier in
    the SAME cycle. It read open_trades once at cycle start, so a second buy
    measured heat against an empty book and could collectively breach
    max_portfolio_heat_pct (the correlation cap already saw same-cycle entries
    via `positions` — this closes the inconsistency)."""
    cfg2, install = cycle_env
    cfg2["symbols"] = ["SPY", "QQQ"]
    cfg2["risk"]["max_open_positions"] = 5
    cfg2["risk"]["max_trades_per_day"] = 5
    # Each bracketed entry risks ~$366 (qty 50 x (20 - stop ~12.67)). Cap at
    # $500 (0.5% of 100k): the first passes; the second only breaches once the
    # first same-cycle entry's stop-risk is counted.
    cfg2["risk"]["max_portfolio_heat_pct"] = 0.5
    import yaml
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg2, f)
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))  # both symbols buy

    main.run_cycle()

    assert len(broker.submitted) == 1  # second entry blocked by the heat cap
    led = Ledger(cfg2["memory"]["ledger_path"])
    blocked = [r for r in led.all_records()
               if r.get("type") == "decision" and r.get("executed") is False
               and "portfolio heat" in (r.get("detail") or "").lower()]
    assert blocked


def test_max_open_positions_counts_same_cycle_entries(cycle_env, cfg):
    """Regression: entries executed earlier in the SAME cycle must count
    toward max_open_positions (the cap was read once at cycle start)."""
    cfg2, install = cycle_env
    cfg2["symbols"] = ["SPY", "QQQ"]
    cfg2["risk"]["max_open_positions"] = 1
    cfg2["risk"]["max_trades_per_day"] = 5
    import yaml
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg2, f)
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))  # both symbols buy

    main.run_cycle()

    assert len(broker.submitted) == 1  # second entry blocked by the cap
    from judgments import JudgmentStore
    js = JudgmentStore(cfg2["learning"]["judgments_path"]).replay()
    rails = [j for j in js.values() if j["kind"] == "rails"]
    assert any("max open positions" in j["reasoning"] for j in rails)


def test_halt_file_blocks_cycle_entirely(cycle_env):
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    risk.engage_halt("test")

    main.run_cycle()

    assert broker.submitted == []
    led = Ledger(cfg["memory"]["ledger_path"])
    events = [r for r in led.all_records() if r["type"] == "event"]
    assert any(e["event"] == "halted_cycle_skipped" for e in events)


def test_bracket_failure_refuses_naked_entry(cycle_env):
    """If the protective bracket can't be placed, the entry must be REFUSED —
    never downgraded to a naked market order. The quantity may have been stop-
    distance-sized (meanrev), and a young position with no broker-side stop can
    only be exited by the daily-loss kill switch (the swing guard blocks
    strategy exits before min_holding_days)."""
    cfg, install = cycle_env

    class BracketFailsBroker(FakeCycleBroker):
        def bracket_market_order(self, *a, **k):
            raise RuntimeError("bracket rejected by broker")

    broker = install(BracketFailsBroker(make_bars(BUY_CLOSES)))

    main.run_cycle()

    assert broker.submitted == []            # no naked market order placed
    led = Ledger(cfg["memory"]["ledger_path"])
    assert led.open_buys() == {}             # nothing opened
    decisions = [r for r in led.all_records()
                 if r.get("type") == "decision" and r.get("symbol") == "SPY"]
    assert decisions and decisions[-1]["executed"] is False
    assert "bracket" in decisions[-1]["detail"].lower()


def test_kill_switch_engages_halt_even_when_flatten_fails(cycle_env):
    """A broker error during the kill-switch flatten must NOT leave the daily-
    loss breach un-halted. HALT is engaged and the kill_switch event recorded
    BEFORE the flatten is attempted, so the next scheduled cycle is blocked even
    if close_all_positions timed out; the flatten failure is itself ledgered."""
    cfg, install = cycle_env

    class BreachedFlattenFailsBroker(FakeCycleBroker):
        def account(self):  # -5% day, below the -3% daily_loss_limit_pct
            return {"equity": 95_000.0, "cash": 95_000.0,
                    "last_equity": 100_000.0, "buying_power": 95_000.0}

        def flatten_all(self):
            raise RuntimeError("broker timeout closing positions")

    # The position PERSISTS across flatten_all, which is what makes this a real
    # failure. Updated 2026-08-02: success is now decided by RE-READING THE BOOK
    # rather than by the absence of an exception, so a broker that raises while
    # reporting an empty book has correctly succeeded (companion test below).
    # Without a surviving position this would assert a failure that no longer
    # exists — the test tracked forward, not relaxed.
    broker = install(BreachedFlattenFailsBroker(
        make_bars(BUY_CLOSES),
        positions={"SPY": {"qty": 50, "market_value": 1000.0}}))

    main.run_cycle()  # must NOT raise, despite flatten_all throwing

    assert risk.check_halt(), "HALT must be engaged even when flatten fails"
    led = Ledger(cfg["memory"]["ledger_path"])
    events = [r["event"] for r in led.all_records() if r["type"] == "event"]
    assert "kill_switch" in events
    assert "kill_switch_flatten_failed" in events
    assert risk.flatten_pending(), (
        "a flatten that left positions open must be marked pending, or nothing "
        "will ever retry it")
    assert broker.submitted == []  # no entries after a kill switch


def test_a_raising_flatten_on_an_empty_book_is_NOT_a_failure(cycle_env):
    """The other half, and the reason the test above needed a real position.

    `flatten_all()` is cancel_orders() + close_all_positions(); either can raise
    after the book is already flat — a timeout reading the response, say. What
    the kill switch cares about is EXPOSURE, and the broker reporting no
    positions is the only evidence of that this repo accepts (invariant #4).
    Recording a failure here would arm a pending liquidation against an empty
    book and page an operator about an incident that is already over.
    """
    cfg, install = cycle_env

    class BreachedEmptyBook(FakeCycleBroker):
        def account(self):
            return {"equity": 95_000.0, "cash": 95_000.0,
                    "last_equity": 100_000.0, "buying_power": 95_000.0}

        def flatten_all(self):
            raise RuntimeError("timeout reading the close-all response")

    install(BreachedEmptyBook(make_bars(BUY_CLOSES)))     # no positions

    main.run_cycle()

    led = Ledger(cfg["memory"]["ledger_path"])
    events = [r["event"] for r in led.all_records() if r["type"] == "event"]
    assert "kill_switch" in events
    assert "kill_switch_flatten_failed" not in events
    assert not risk.flatten_pending()


def test_executed_order_carries_idempotency_key(cycle_env):
    """Crash-rerun protection: orders carry a deterministic per-symbol/side/
    day client_order_id so the broker rejects a duplicate submission."""
    from datetime import datetime, timezone
    cfg, install = cycle_env
    broker = install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    coid = broker.submitted[0]["client_order_id"]
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert coid == f"ta-SPY-buy-{today}"


def test_degradation_slo_breach_logged_once(cycle_env, monkeypatch):
    cfg, install = cycle_env
    cfg["ops"] = {"max_degradations_per_day": 2}
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("degradation", "drift_guard: test 1")
    led.log_event("degradation", "news_catchup: test 2")
    notes = []
    import watchdog
    monkeypatch.setattr(watchdog, "notify", lambda t, m: notes.append(t))
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()   # crosses threshold -> one slo_breach + one alert
    main.run_cycle()   # already breached today -> no second event
    breaches = [r for r in led.all_records() if r.get("event") == "slo_breach"]
    assert len(breaches) == 1 and len(notes) == 1


def test_llm_outage_is_ledgered_as_degradation(cycle_env, monkeypatch):
    """An unreachable judge must be distinguishable from a judge that approved.
    Before this, an outage returned the same fallback as 'intentionally
    disabled' and was recorded as a genuine approve — crediting the judge for a
    decision it never made and hiding the outage from the degradation SLO."""
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))

    def _boom(*a, **k):
        raise RuntimeError("anthropic 503")
    monkeypatch.setattr(main.llm, "_client", _boom, raising=False)
    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "approve", "scale": 1.0,
                                         "cited_lessons": [], "bull_case": "",
                                         "bear_case": "", "confidence": None,
                                         "reasoning": "unavailable",
                                         "degraded": "anthropic 503",
                                         "degraded_reason": "api"})
    main.run_cycle()

    led = Ledger(cfg["memory"]["ledger_path"])
    degs = [r for r in led.all_records()
            if r.get("type") == "event" and r.get("event") == "degradation"
            and (r.get("detail") or "").startswith("llm_judge")]
    assert degs, "LLM outage must be ledgered as a degradation"
    assert "anthropic 503" in degs[0]["detail"]
    # 2026-07-27: the record must also name WHICH failure. An outage and a
    # model replying with prose used to be byte-identical here.
    assert "llm_judge[api]" in degs[0]["detail"], degs[0]["detail"]


def test_llm_disabled_is_not_a_degradation(cycle_env):
    """The intentionally-disabled path (llm.enabled false, as in tests) must
    NOT emit a degradation — only genuine outages do."""
    cfg, install = cycle_env
    install(FakeCycleBroker(make_bars(BUY_CLOSES)))
    main.run_cycle()
    led = Ledger(cfg["memory"]["ledger_path"])
    assert not [r for r in led.all_records()
                if r.get("type") == "event" and r.get("event") == "degradation"
                and (r.get("detail") or "").startswith("llm_judge:")]


def test_zero_qty_rejection_names_the_real_cause(cycle_env, monkeypatch):
    """A whole-share truncation must not be reported as "account too small for
    caps" — that was false on a $100k account and hid an accelerating leak.
    Two distinct causes must be distinguishable in the ledger."""
    cfg, install = cycle_env
    # Price high enough that the 1% notional buys exactly 1 share, so an LLM
    # downsize truncates it to 0.
    closes = [10] * 6 + [9, 9, 9, 700.0]
    broker = install(FakeCycleBroker(make_bars(closes)))
    monkeypatch.setattr(main.llm, "review_signal",
                        lambda *a, **k: {"verdict": "downsize", "scale": 0.5,
                                         "cited_lessons": [], "bull_case": "",
                                         "bear_case": "", "confidence": None,
                                         "reasoning": "half size"})
    main.run_cycle()

    led = Ledger(cfg["memory"]["ledger_path"])
    blocked = [r for r in led.all_records()
               if r.get("type") == "decision" and r.get("executed") is False
               and "0 shares" in (r.get("detail") or "")
               or "truncated" in (r.get("detail") or "")]
    assert blocked, "expected an honest zero-qty rejection"
    detail = blocked[-1]["detail"]
    assert "account too small" not in detail          # the false message is gone
    assert "truncated" in detail or "below one share" in detail
    assert broker.submitted == []                     # nothing was rounded UP


def test_slow_strategy_cannot_starve_the_fast_one(cycle_env, cfg):
    """§13 end-to-end: with tsmom holding its full allocation, a meanrev entry
    must still be admitted. Before per-strategy slots, tsmom holding all 5
    global slots blocked every other strategy — the live cause of ~0 evidence
    velocity (146 buy signals -> 6 executions, 68 blocked on max_open_positions)."""
    cfg2, install = cycle_env
    cfg2["risk"]["max_open_positions"] = 8
    strats = cfg2.setdefault("strategies", {})
    strats.setdefault("tsmom", {})["max_open_positions"] = 5
    strats.setdefault("meanrev", {})["max_open_positions"] = 8
    import yaml as _y
    with open("config.yaml", "w") as f:
        _y.safe_dump(cfg2, f)

    acct = {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}
    # tsmom is at its allocation (5); the book still has global headroom (8).
    tsmom_open = {f"t{i}": {"symbol": f"S{i}", "strategy": "tsmom", "qty": 1,
                            "entry_price": 100.0, "order": {}} for i in range(5)}
    positions = {f"S{i}": {"market_value": 100.0, "qty": 1} for i in range(5)}

    # tsmom is blocked by its OWN allocation...
    with pytest.raises(risk.RiskRejection, match="max open positions for tsmom"):
        risk.pre_trade_checks("buy", "NEW", 1, 100.0, acct, positions, cfg2,
                              open_trades=tsmom_open, strategy="tsmom")
    # ...while meanrev, which holds none, still gets in.
    risk.pre_trade_checks("buy", "NEW", 1, 100.0, acct, positions, cfg2,
                          open_trades=tsmom_open, strategy="meanrev")


def test_quiet_cycle_still_rebuilds_the_journal_page(cycle_env, cfg):
    """A cycle that trades nothing must still rewrite journal.html from the
    store.

    Until 2026-07-28 `journal.render()` was reached ONLY through
    `journal_and_link()`, i.e. only when a trade executed or closed. A page
    that had gone stale therefore had no way to repair itself, and one did not:
    the published journal showed a single entry, for a trade_id present in no
    current store, while memory/journal.jsonl held 17 write-ups. Rendering in
    the cycle's cosmetic block makes the page self-correcting.
    """
    cfg2, install = cycle_env
    install(FakeCycleBroker(make_bars([10] * 10)))  # flat: no crossover, no trade

    with open(cfg2["x_posting"]["journal_path"], "w") as f:
        f.write(json.dumps({
            "trade_id": "seeded1", "ts": "2026-07-27T22:17:00+00:00",
            "symbol": "XLF", "kind": "buy",
            "title": "BUY XLF — tsmom",
            "text": "Written on a previous day; the page must still show it."})
            + "\n")

    main.run_cycle()

    # Resolved through publish.out_dir, not the CWD. Before 2026-07-28 this
    # read the repo-root journal.html — so the very test that proves the page
    # self-repairs was itself overwriting the real published artifact.
    html = open(sitepaths.resolve(cfg2, journal.OUT_PATH)).read()
    assert "seeded1" in html
    assert "must still show it" in html
