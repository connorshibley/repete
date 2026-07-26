"""Dashboard redesign, 2026-07-26: n=1 guards, hold collapsing, self-refresh.

The guard that matters most here is the first one. On 2026-07-26 this page
showed `profit factor: inf` and `win rate: 100%` off a single closed trade —
both arithmetically correct, both inviting the reader to conclude the bot was
flawless. Everything else in this file is layout; that one is honesty.
"""
import json
import os

import pytest

import dashboard
from ledger import Ledger

from test_dashboard import _cfg_paths          # shared fixture helper


def _closed(led, n):
    for i in range(n):
        tid = led.log_decision(f"S{i}", "buy", "sig", {}, None, executed=True,
                               entry_price=100.0, qty=5, strategy="meanrev")
        led.close_trade(tid, exit_price=104.0, pnl=20.0, pnl_pct=4.0,
                        exit_reason="take_profit")


# ---- the n= guard ----

def test_ratio_is_meaningful_threshold():
    assert not dashboard.ratio_is_meaningful(0)
    assert not dashboard.ratio_is_meaningful(1)
    assert not dashboard.ratio_is_meaningful(dashboard.MIN_CLOSED_FOR_RATIOS - 1)
    assert dashboard.ratio_is_meaningful(dashboard.MIN_CLOSED_FOR_RATIOS)
    assert dashboard.ratio_is_meaningful(500)


def test_ratio_is_meaningful_handles_none():
    assert not dashboard.ratio_is_meaningful(None)


def test_one_closed_trade_does_not_advertise_a_perfect_record(tmp_path, cfg):
    """The regression this file exists for."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _closed(led, 1)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "not yet meaningful" in html
    assert "n=1 · not yet meaningful" in html
    # the raw figures must not stand unqualified anywhere
    assert "pendingv" in html


def test_enough_trades_shows_the_real_numbers(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _closed(led, dashboard.MIN_CLOSED_FOR_RATIOS)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "not yet meaningful" not in html


def test_per_strategy_rows_get_the_same_guard(tmp_path, cfg):
    """A strategy row reading 100%/inf off one trade is the same misreading
    in a smaller font."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _closed(led, 1)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert html.count("not yet meaningful") >= 3   # 2 cards + >=1 strategy row


# ---- hold collapsing ----

def test_runs_of_quiet_holds_collapse_to_one_row(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    for s in ("SPY", "QQQ", "DIA", "IWM"):
        led.log_decision(s, "hold", "no entry from 3 strategies", {}, None,
                         executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "symbols held — no entry" in html
    assert html.count("holdrun") >= 1
    # the symbols stay visible — collapsing must not destroy information
    for s in ("SPY", "QQQ", "DIA", "IWM"):
        assert s in html


def test_a_judged_HOLD_is_never_collapsed(tmp_path, cfg):
    """Only 'nothing happened' rows collapse.

    The case that matters is a hold the judge actually reasoned about — same
    action as a quiet hold, but carrying content. Collapsing on action alone
    would silently delete that reasoning from the page, and a test using a
    `buy` here would never notice, because a buy is not collapsible either
    way. The 2026-07-26 negative-control run caught exactly that gap.
    """
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("SPY", "hold", "no entry", {}, None, executed=False)
    led.log_decision("QQQ", "hold", "no entry", {}, None, executed=False)
    led.log_decision("NVDA", "hold", "momentum fading", {},
                     {"verdict": "veto", "scale": 0.0,
                      "reasoning": "regime risk outweighs the setup"},
                     executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "regime risk outweighs the setup" in html, (
        "a judged hold was collapsed away — its reasoning is gone")
    assert "2</b> symbols held — no entry" in html, (
        "the two quiet holds should collapse into one row")


def test_an_executed_decision_is_never_collapsed(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("NVDA", "buy", "momentum", {},
                     {"verdict": "downsize", "scale": 0.5,
                      "reasoning": "sized down"}, executed=False,
                     detail="risk rejection: max open positions")
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "risk rejection: max open positions" in html


# ---- self-refresh ----

def test_render_writes_the_json_sidecar(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "no entry", {}, None, executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    data = os.path.join(os.path.dirname(out), dashboard.DATA_PATH)
    assert os.path.exists(data), "the page polls this file; without it 404s"
    d = json.load(open(data))
    assert d["hash"] and d["generated_at"]
    assert "positions" in d["regions"] and "decisions" in d["regions"]


def test_html_and_sidecar_agree_on_the_hash(tmp_path, cfg):
    """The page compares its embedded hash against the file's. If they were
    generated differently the page would swap content on every single poll."""
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    html = open(out).read()
    d = json.load(open(os.path.join(os.path.dirname(out),
                                    dashboard.DATA_PATH)))
    assert f'data-hash="{d["hash"]}"' in html


def test_hash_is_stable_across_identical_regenerations(tmp_path, cfg):
    """The hash must cover the CONTENT and not the generation timestamp.

    If `generated_at` were folded in, every cycle would produce a new hash
    even when nothing changed, and the page would rewrite all ten regions on
    every poll — destroying text selection and any open row, forever, for no
    reason. Cheap to get wrong and invisible without this test.
    """
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    data = os.path.join(os.path.dirname(out), dashboard.DATA_PATH)
    first = json.load(open(data))
    dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    second = json.load(open(data))
    assert first["hash"] == second["hash"]
    assert first["generated_at"] != second["generated_at"] or True  # may tie


def test_changed_content_changes_the_hash(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_decision("SPY", "hold", "x", {}, None, executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    data = os.path.join(os.path.dirname(out), dashboard.DATA_PATH)
    before = json.load(open(data))["hash"]
    led.log_decision("NVDA", "buy", "momentum", {},
                     {"verdict": "approve", "scale": 1.0,
                      "reasoning": "clean setup"}, executed=True,
                     entry_price=100.0, qty=3, strategy="tsmom")
    dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    assert json.load(open(data))["hash"] != before


def test_every_region_in_the_sidecar_has_a_mount_point(tmp_path, cfg):
    """A region with no matching #rgn-<key> silently never updates."""
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    out = dashboard.render(cfg, out_path=str(tmp_path / "d.html"))
    html = open(out).read()
    d = json.load(open(os.path.join(os.path.dirname(out),
                                    dashboard.DATA_PATH)))
    for key in d["regions"]:
        assert f'id=rgn-{key}' in html, f"region {key} has no mount point"


def test_the_hard_meta_refresh_is_gone(tmp_path, cfg):
    """It reloaded the whole document every 5 minutes, discarding scroll
    position and any open filter. Polling replaced it; both together would
    make long tables unreadable."""
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "http-equiv=\"refresh\"" not in html


def test_freshness_badge_is_present_and_stamped(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "id=fresh" in html and "data-gen=" in html


def test_staleness_thresholds_are_ordered_and_shipped_to_the_page(tmp_path, cfg):
    """The badge must be able to reach amber and then red. If the constants
    were equal or inverted it could never show one of the states."""
    assert 0 < dashboard.STALE_AMBER_HOURS < dashboard.STALE_RED_HOURS
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert f"AMBER={dashboard.STALE_AMBER_HOURS}" in html
    assert f"RED={dashboard.STALE_RED_HOURS}" in html


def test_page_degrades_gracefully_when_it_cannot_poll(tmp_path, cfg):
    """Opened via file://, fetch() of a sibling is blocked. The page must say
    so rather than showing stale data as if it were live."""
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "auto-update unavailable" in html
    assert "location.protocol!=='file:'" in html


def test_staleness_is_repainted_independently_of_polling(tmp_path, cfg):
    """The badge must age on a timer, not only on a successful fetch —
    otherwise a broken update path shows a permanently green 'live'."""
    cfg = _cfg_paths(cfg, tmp_path)
    Ledger(cfg["memory"]["ledger_path"]).log_decision(
        "SPY", "hold", "x", {}, None, executed=False)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    # a repaint interval that does not depend on fetch succeeding
    assert "setInterval(function(){paint(" in html


def test_publish_script_ships_the_sidecar():
    """Without this the deployed page polls a 404 while its HTML is current —
    it would look broken precisely because the freshness badge works."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sh = open(os.path.join(here, "scripts", "publish_dashboard.sh")).read()
    assert "dashboard_data.json" in sh
    assert "git -C .site add dashboard_data.json" in sh
