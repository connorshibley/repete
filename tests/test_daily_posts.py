"""Daily plan/review posts: read-only scans, compliant text, no orders.
All offline — LLM disabled by fixture, X stays dry_run."""
import json

import daily_posts
from ledger import Ledger

from conftest import make_bars
from test_main_cycle import BUY_CLOSES


class ScanBroker:
    """Read-only fake: any order attempt is a test failure."""

    def __init__(self, bars, positions=None, equity=100_000.0):
        self._bars, self._positions, self._equity = bars, positions or {}, equity

    def bars(self, symbol, timeframe, limit):
        return self._bars

    def positions(self):
        return self._positions

    def account(self):
        return {"equity": self._equity, "cash": self._equity,
                "last_equity": self._equity, "buying_power": self._equity}

    def market_order(self, *a, **k):
        raise AssertionError("daily posts must never place orders")

    bracket_market_order = market_order
    flatten_all = market_order


def test_plan_facts_scan_places_no_orders(cfg, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # HALT check reads cwd
    cfg["risk"]["max_bar_age_days"] = 0  # fixture bars carry fixed old ts
    broker = ScanBroker(make_bars(BUY_CLOSES))
    facts = daily_posts.gather_plan_facts(cfg, broker)
    assert facts["n_setups"] == 1  # SPY crossover fires as a would-be setup
    assert facts["setups"][0]["symbol"] == "SPY"
    assert facts["n_positions"] == 0


def test_plan_facts_none_when_spy_stale(cfg):
    cfg["risk"]["max_bar_age_days"] = 4  # fixture ts are months old => stale
    assert daily_posts.gather_plan_facts(cfg, ScanBroker(make_bars(BUY_CLOSES))) is None


def test_plan_template_compliant(cfg):
    facts = {"setups": [{"symbol": "NVDA", "strategy": "meanrev",
                         "reason": "dip"}],
             "n_setups": 1, "n_positions": 6, "positions": ["SPY"],
             "regime": "up/low (SPY 754 > SMA 736, vol 12%)",
             "note": "watchlist only"}
    text = daily_posts.plan_template(facts)
    assert len(text) <= 275 and text.startswith("[PAPER]")
    assert "NVDA (meanrev)" in text and "3:45" in text


def test_plan_template_no_setups(cfg):
    facts = {"setups": [], "n_setups": 0, "n_positions": 1, "positions": [],
             "regime": "up/low", "note": ""}
    text = daily_posts.plan_template(facts)
    assert "no fresh setups" in text and "1 position." in text


def test_review_facts_from_todays_ledger(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    led = Ledger(str(tmp_path / "ledger.jsonl"))
    led.log_decision("KO", "buy", "trend", {}, {"verdict": "approve",
                                                "scale": 1.0,
                                                "reasoning": "ok"},
                     executed=True, entry_price=60.0, qty=10,
                     strategy="tsmom")
    led.log_decision("TSLA", "buy", "momo", {}, {"verdict": "veto",
                                                 "scale": 1.0,
                                                 "reasoning": "no"},
                     executed=False, strategy="tsmom")
    facts = daily_posts.gather_review_facts(cfg, ScanBroker([]),
                                            led.all_records())
    assert facts["n_executed"] == 1 and facts["n_vetoes"] == 1
    assert facts["trades"][0]["symbol"] == "KO"
    assert facts["equity"] == 100_000.0 and not facts["halted"]


def test_review_template_compliant():
    facts = {"trades": [{"symbol": "KO", "action": "buy",
                         "strategy": "tsmom"}],
             "n_executed": 1, "n_vetoes": 2, "n_holds": 20,
             "closed_today": 1, "realized_pnl_today": -12.5,
             "equity": 99_987.0, "halted": False}
    text = daily_posts.review_template(facts)
    assert len(text) <= 275 and text.startswith("[PAPER]")
    assert "BUY KO" in text and "2 judge vetoes" in text
    assert "-12.50 realized" in text and "$99,987" in text


def test_review_template_halted():
    facts = {"trades": [], "n_executed": 0, "n_vetoes": 0, "n_holds": 0,
             "closed_today": 0, "realized_pnl_today": 0.0,
             "equity": None, "halted": True}
    text = daily_posts.review_template(facts)
    assert "HALT" in text and text.startswith("[PAPER]")


def test_run_plan_posts_and_ledgers(cfg, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg["risk"]["max_bar_age_days"] = 0
    with open("config.yaml", "w") as f:
        import yaml
        yaml.safe_dump(cfg, f)
    monkeypatch.setattr(daily_posts, "Broker",
                        lambda cfg: ScanBroker(make_bars(BUY_CLOSES)))
    daily_posts.run("plan")

    out = capsys.readouterr().out
    assert "[PAPER]" in out and "dry run" in out  # dry_run printed, not posted
    led = Ledger(cfg["memory"]["ledger_path"])
    events = [r for r in led.all_records() if r["type"] == "event"]
    assert any(e["event"] == "plan_post" for e in events)
    assert not any(r["type"] == "decision" for r in led.all_records())


# ---- plan-post catch-up (missed 9:35 slot) ----

def test_should_catchup_pure_cases():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    fri_afternoon = datetime(2026, 7, 17, 11, 0, tzinfo=et)
    assert daily_posts.should_catchup(fri_afternoon, None)
    assert daily_posts.should_catchup(fri_afternoon, "2026-07-16")  # stale
    assert not daily_posts.should_catchup(fri_afternoon, "2026-07-17")  # done
    early = datetime(2026, 7, 17, 9, 34, tzinfo=et)
    assert not daily_posts.should_catchup(early, None)   # before the slot
    at_slot = datetime(2026, 7, 17, 9, 35, tzinfo=et)
    assert daily_posts.should_catchup(at_slot, None)     # slot time counts
    saturday = datetime(2026, 7, 18, 11, 0, tzinfo=et)
    assert not daily_posts.should_catchup(saturday, None)


def test_catchup_runs_plan_once_per_day(tmp_path, monkeypatch):
    marker = tmp_path / "last_plan_post"
    monkeypatch.setattr(daily_posts, "PLAN_MARKER", str(marker))
    calls = []
    monkeypatch.setattr(daily_posts, "run", lambda mode: calls.append(mode))
    from datetime import datetime
    afternoon = datetime(2026, 7, 17, 13, 0, tzinfo=daily_posts.ET)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return afternoon
    monkeypatch.setattr(daily_posts, "datetime", FakeDT)

    assert daily_posts.catchup() is True          # missed slot -> runs plan
    assert calls == ["plan"]
    assert marker.read_text() == "2026-07-17"     # marker written pre-post
    assert daily_posts.catchup() is False         # same day -> no-op
    assert calls == ["plan"]


def test_marker_written_even_if_plan_run_fails(tmp_path, monkeypatch):
    marker = tmp_path / "last_plan_post"
    monkeypatch.setattr(daily_posts, "PLAN_MARKER", str(marker))

    def boom(mode):
        raise RuntimeError("X exploded")
    monkeypatch.setattr(daily_posts, "run", boom)
    from datetime import datetime
    afternoon = datetime(2026, 7, 17, 13, 0, tzinfo=daily_posts.ET)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return afternoon
    monkeypatch.setattr(daily_posts, "datetime", FakeDT)

    import pytest as _pytest
    with _pytest.raises(RuntimeError):
        daily_posts.catchup()
    assert marker.read_text() == "2026-07-17"     # no hourly retry storm
