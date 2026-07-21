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


def test_hero_total_pl_from_snapshots(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    cfg["reporting"] = {"starting_equity": 100_000}
    led = Ledger(cfg["memory"]["ledger_path"])
    for eq in (100_000.0, 100_250.0):
        led.log_event("cycle_complete", json.dumps({"equity": eq}))
    out = dashboard.render(cfg, out_path=str(tmp_path / "dash.html"))
    html = open(out).read()
    assert "+$250.00" in html                 # hero total P/L, signed
    assert 'data-count="250.00"' in html      # count-up target
    assert "Total P/L" in html
    assert 'data-tip="2026-' in html          # chart hover targets rendered


def test_hero_negative_pl_gets_loss_class(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    cfg["reporting"] = {"starting_equity": 100_000}
    led = Ledger(cfg["memory"]["ledger_path"])
    for eq in (100_000.0, 99_400.0):
        led.log_event("cycle_complete", json.dumps({"equity": eq}))
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert "-$600.00" in html
    assert 'class="hv loss"' in html


def test_trade_bars_one_rect_per_closed_trade(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    t1 = led.log_decision("NVDA", "buy", "dip", {}, None, executed=True,
                          entry_price=100.0, qty=5, strategy="meanrev")
    t2 = led.log_decision("AAPL", "buy", "trend", {}, None, executed=True,
                          entry_price=200.0, qty=3, strategy="tsmom")
    led.close_trade(t1, exit_price=104.0, pnl=20.0, pnl_pct=4.0,
                    exit_reason="take_profit")
    led.close_trade(t2, exit_price=195.0, pnl=-15.0, pnl_pct=-2.5,
                    exit_reason="stop_loss")
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert '<rect class="win"' in html and '<rect class="loss"' in html
    assert "NVDA · +$20.00 (+4.00%) · take_profit" in html
    assert "AAPL · -$15.00 (-2.50%) · stop_loss" in html


def test_filter_chips_and_row_classes(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("NVDA", "buy", "dip", {},
                     {"verdict": "veto", "scale": 0.0, "reasoning": "no"},
                     executed=False, detail="LLM veto")
    led.log_decision("AAPL", "buy", "trend", {},
                     {"verdict": "approve", "scale": 1.0, "reasoning": "ok"},
                     executed=True, entry_price=200.0, qty=3)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    for chip in ("Executed", "Vetoed", "Downsized", "Skipped"):
        assert f">{chip}</span>" in html
    assert 'class="r-skip r-veto"' in html
    assert 'class="r-exec r-approve"' in html


# ---- playful layer (2026-07-21): Repete's tape + robot + speech ----

def _render_html(tmp_path, cfg, monkeypatch):
    import dashboard
    monkeypatch.chdir(tmp_path)
    from ledger import Ledger
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("cycle_complete", '{"equity": 100150.0}')
    tid = led.log_decision("SPY", "buy", "x", {}, None, executed=True,
                           entry_price=100.0, qty=10, strategy="tsmom",
                           regime="up/low")
    out = tmp_path / "dash.html"
    dashboard.render(cfg, out_path=str(out))
    return out.read_text()


def test_tape_repeats_and_robot_present(tmp_path, cfg, monkeypatch):
    html_text = _render_html(tmp_path, cfg, monkeypatch)
    assert html_text.count("REPETE · [PAPER]") == 2      # seamless double pass
    assert 'class=tape' in html_text and "tapescroll" in html_text
    assert "prefers-reduced-motion" in html_text          # a11y escape hatch
    assert 'aria-label="Repete the trading robot"' in html_text
    assert "HOLDING <b>SPY</b>" in html_text              # real book data


def test_robot_mood_tracks_pl(tmp_path, cfg, monkeypatch):
    import dashboard
    assert "mouth-smile" in dashboard._robot(120.0)
    assert "mouth-flat" in dashboard._robot(-120.0)


def test_speech_lines_embedded_json(tmp_path, cfg, monkeypatch):
    import json as _json
    html_text = _render_html(tmp_path, cfg, monkeypatch)
    start = html_text.index('id=replines>') + len('id=replines>')
    end = html_text.index('</script>', start)
    lines = _json.loads(html_text[start:end])
    assert any("position" in ln for ln in lines)
    assert all(isinstance(ln, str) for ln in lines)
