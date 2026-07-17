"""Dashboard generator: renders offline from ledger fixtures, embeds the
right numbers, degrades on empty data, and never needs the network."""
import json

import dashboard
from ledger import Ledger


def _cfg_paths(cfg, tmp_path):
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    cfg["memory"]["learnings_path"] = str(tmp_path / "learnings.md")
    cfg["learning"]["lessons_path"] = str(tmp_path / "lessons.jsonl")
    cfg["learning"]["judgments_path"] = str(tmp_path / "judgments.jsonl")
    return cfg


def test_render_empty_ledger(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    out = dashboard.render(cfg, out_path=str(tmp_path / "dash.html"))
    html = open(out).read()
    assert "[PAPER]" in html
    assert "No open positions" in html
    assert "No decisions yet" in html


def test_render_with_activity(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    tid = led.log_decision("NVDA", "buy", "dip in uptrend", {"rsi2": 7},
                           {"verdict": "downsize", "scale": 0.7,
                            "reasoning": "zero trade history"},
                           executed=True, order={"id": "o1",
                                                 "stop_price": 164.67},
                           entry_price=177.19, qty=5, regime="up/low",
                           strategy="meanrev")
    led.log_event("cycle_complete", json.dumps({"equity": 100_000.0,
                                                "n_positions": 1,
                                                "regime": "up/low"}))
    led.log_event("cycle_complete", json.dumps({"equity": 100_250.0,
                                                "n_positions": 1,
                                                "regime": "up/low"}))
    out = dashboard.render(cfg, out_path=str(tmp_path / "dash.html"))
    html = open(out).read()
    assert "NVDA" in html and "meanrev" in html
    assert "downsize" in html and "zero trade history" in html
    assert "$164.67" in html            # stop price surfaced
    assert "<polyline" in html          # equity curve drawn from 2 snapshots
    assert tid  # decision id exists (sanity)


def test_equity_series_skips_legacy_events(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("cycle_complete")            # pre-snapshot era: no detail
    led.log_event("cycle_complete", "free text detail")
    led.log_event("cycle_complete", json.dumps({"equity": 99_500.0}))
    series = dashboard.equity_series(led.all_records())
    assert len(series) == 1 and series[0][1] == 99_500.0


def test_spy_overlay_included_when_bars_given(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    for eq in (100_000.0, 100_100.0, 100_050.0):
        led.log_event("cycle_complete", json.dumps({"equity": eq}))
    from conftest import make_bars
    out = dashboard.render(cfg, out_path=str(tmp_path / "dash.html"),
                           spy_bars=make_bars([100, 101, 102]))
    html = open(out).read()
    assert "SPY (scaled)" in html
