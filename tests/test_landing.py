"""Landing page (2026-07-21): cream/orange front door; the dark terminal
moved to dash.html. Self-contained, real ledger facts, cosmetic only."""
import landing
from ledger import Ledger


def _render(tmp_path, cfg, monkeypatch):
    monkeypatch.chdir(tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_event("cycle_complete", '{"equity": 100150.0}')
    led.log_decision("SPY", "buy", "x", {}, None, executed=True,
                     entry_price=100.0, qty=10, strategy="tsmom")
    out = tmp_path / "landing.html"
    landing.render(cfg, out_path=str(out))
    return out.read_text()


def test_landing_palette_and_structure(tmp_path, cfg, monkeypatch):
    t = _render(tmp_path, cfg, monkeypatch)
    assert "--cream:#F5F0E8" in t and "--orange:#D97757" in t
    assert "Meet" in t and "Repete" in t
    assert 'href="dash.html"' in t                 # CTA into the terminal
    assert 'aria-label="Repete the trading robot"' in t
    assert "prefers-reduced-motion" in t
    assert "Paper trading" in t                    # honesty footer


def test_landing_tape_repeats_with_real_facts(tmp_path, cfg, monkeypatch):
    t = _render(tmp_path, cfg, monkeypatch)
    assert t.count("REPETE</b> · paper trading") == 2   # seamless loop pair
    assert "1</b> open position" in t                   # real book count
    assert "equity" in t and "$100,150.00" in t


def test_landing_self_contained(tmp_path, cfg, monkeypatch):
    t = _render(tmp_path, cfg, monkeypatch)
    assert "<link" not in t and "src=http" not in t and "@import" not in t
    # only external hrefs are the X profile links
    import re
    ext = [u for u in re.findall(r'href="(http[^"]+)"', t)
           if "x.com/Repete2026" not in u]
    assert ext == []
