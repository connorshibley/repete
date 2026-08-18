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
    assert "No closed trades yet." in html
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


def _close_n(led, n, start=0):
    """n closed trades, alternating win/loss, for chart-density tests."""
    for i in range(start, start + n):
        tid = led.log_decision(f"S{i}", "buy", "sig", {}, None, executed=True,
                               entry_price=100.0, qty=5, strategy="meanrev")
        win = i % 2 == 0
        led.close_trade(tid, exit_price=104.0 if win else 96.0,
                        pnl=20.0 if win else -20.0,
                        pnl_pct=4.0 if win else -4.0,
                        exit_reason="take_profit" if win else "stop_loss")


def test_trade_bars_one_rect_per_closed_trade(tmp_path, cfg):
    """The bar chart renders once there is a distribution worth looking at.

    Threshold added 2026-07-26: below 5 closed trades a lone bar in a 940px
    field read as a broken render rather than as a small sample, so the chart
    is replaced by the trades written out. This test now supplies enough to
    clear that and still asserts one bar per trade.
    """
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
    _close_n(led, 4)                      # 6 total — clears the threshold
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert '<rect class="win"' in html and '<rect class="loss"' in html
    assert "NVDA · +$20.00 (+4.00%) · take_profit" in html
    assert "AAPL · -$15.00 (-2.50%) · stop_loss" in html


def test_trade_bars_below_threshold_list_trades_instead_of_a_lone_bar(
        tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _close_n(led, 2)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert "too few for the distribution to have a shape" in html
    assert '<rect class="win"' not in html


def test_closed_positions_table_shows_prices_and_signed_pnl(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    tid = led.log_decision("NVDA", "buy", "dip", {}, None, executed=True,
                           entry_price=100.0, qty=5, strategy="meanrev")
    led.close_trade(tid, exit_price=104.0, pnl=20.0, pnl_pct=4.0,
                    exit_reason="take_profit", benchmark_pnl_pct=1.5)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert "Closed positions" in html
    assert "NVDA" in html
    assert "$100.00" in html and "$104.00" in html   # entry -> exit
    assert "+$20.00 (+4.00%)" in html
    assert "+2.50%" in html                          # alpha: 4.0 - 1.5
    assert "take_profit" in html


def test_closed_positions_losing_trade_gets_loss_class(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    tid = led.log_decision("XOM", "buy", "sig", {}, None, executed=True,
                           entry_price=100.0, qty=10, strategy="tsmom")
    led.close_trade(tid, exit_price=96.0, pnl=-40.0, pnl_pct=-4.0,
                    exit_reason="stop_loss")
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert "-$40.00 (-4.00%)" in html
    assert "class=loss>-$40.00" in html


def test_closed_positions_absent_alpha_renders_an_em_dash_not_zero(
        tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    tid = led.log_decision("AAPL", "buy", "sig", {}, None, executed=True,
                           entry_price=200.0, qty=3, strategy="tsmom")
    led.close_trade(tid, exit_price=210.0, pnl=30.0, pnl_pct=5.0,
                    exit_reason="strategy_sell")   # no benchmark_pnl_pct
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    assert "<td>—</td><td>strategy_sell</td>" in html
    assert "0.00%</td><td>strategy_sell" not in html


def test_closed_positions_table_caps_at_n_closed_newest_first(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _close_n(led, dashboard.N_CLOSED + 5)
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "dash.html"))).read()
    start = html.index("id=rgn-closed>") + len("id=rgn-closed>")
    end = html.index("<h2>⚖️ Recent decisions", start)
    closed_section = html[start:end]
    assert closed_section.count("<tr><td>") == dashboard.N_CLOSED
    assert f">S{dashboard.N_CLOSED + 4}<" in closed_section   # newest kept
    assert ">S0<" not in closed_section                        # oldest dropped


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
    led.log_decision("SPY", "buy", "x", {}, None, executed=True,
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


def test_boot_splash_present_and_safe(tmp_path, cfg, monkeypatch):
    """Boot overlay ships hidden (display:none default — no-JS visitors go
    straight to data), plays once per session, and reduced-motion skips it."""
    html_text = _render_html(tmp_path, cfg, monkeypatch)
    assert 'id=boot' in html_text and "market brain is waking" in html_text
    assert "market brain online" in html_text
    assert 'id=candles' in html_text        # candlestick loading tape
    assert html_text.count("<b s") >= 6      # REPETE ignites letter by letter
    assert "repete_boot" in html_text            # once-per-session guard
    assert "#boot{position:fixed" in html_text and "display:none" in html_text
    assert "click anywhere to skip" in html_text


# ---- position marks: current value and +/- % (2026-07-25) ----
#
# The dashboard is a STATIC file on GitHub Pages and this suite pins that it
# renders offline. So "current value" is the last broker mark the agent
# recorded, never a live quote fetched here. These tests cover the three ways
# that can go wrong: absent data, bad data, and data that does not line up
# with the ledger.

from datetime import datetime, timedelta, timezone


def _open_position(led, symbol="NVDA", qty=4, entry=198.0):
    return led.log_decision(
        symbol, "buy", "trend intact", {"rsi2": 7},
        {"verdict": "approve", "scale": 1.0}, executed=True,
        entry_price=entry, qty=qty,
        order={"id": "o1", "stop_price": entry * 0.94})


def _mark(led, payload):
    led.log_event("positions_mark", json.dumps(payload))


# ---- degradation: the load-bearing behaviour ----

def test_positions_table_is_unchanged_without_a_mark(tmp_path, cfg):
    """An old ledger, or a host that never ran a cycle, must render exactly the
    table that shipped before this feature — not empty columns."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led)
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()

    assert "NVDA" in html
    for col in ("<th>Now</th>", "<th>Value</th>", "<th>Unrealized</th>"):
        assert col not in html
    assert "open position value" not in html
    assert "valued" not in html


def test_a_malformed_mark_degrades_instead_of_crashing(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led)
    led.log_event("positions_mark", "{not json")
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "NVDA" in html and "<th>Now</th>" not in html


# ---- the numbers ----

def test_mark_renders_price_value_and_percent(tmp_path, cfg):
    """qty 10 @ 100 now worth 1100 with +100 unrealized -> $110.00 / $1,100.00
    / +$100.00 (+10.00%)."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "NVDA", qty=10, entry=100.0)
    _mark(led, {"NVDA": {"qty": 10, "avg_entry": 100.0,
                         "market_value": 1100.0, "unrealized_pl": 100.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()

    assert "<th>Now</th>" in html
    assert "$110.00" in html          # 1100 / 10
    assert "$1,100.00" in html        # market value
    assert "+$100.00 (+10.00%)" in html
    assert "open position value" in html


def test_a_losing_position_renders_as_a_loss(tmp_path, cfg):
    """Red/green must follow unrealized P&L, not some other field's sign."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "XOM", qty=10, entry=100.0)
    _mark(led, {"XOM": {"qty": 10, "avg_entry": 100.0,
                        "market_value": 900.0, "unrealized_pl": -100.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "-$100.00 (-10.00%)" in html
    assert "class=loss" in html


def test_the_book_total_row_sums_the_positions(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "AAA", qty=10, entry=100.0)
    _open_position(led, "BBB", qty=5, entry=200.0)
    _mark(led, {"AAA": {"qty": 10, "avg_entry": 100.0,
                        "market_value": 1100.0, "unrealized_pl": 100.0},
                "BBB": {"qty": 5, "avg_entry": 200.0,
                        "market_value": 900.0, "unrealized_pl": -100.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "totrow" in html
    assert "$2,000.00" in html         # 1100 + 900 book value
    assert "+$0.00 (+0.00%)" in html   # +100 - 100 on 2000 cost


# ---- guards: the size_order ZeroDivisionError class of bug ----

def test_zero_qty_and_zero_entry_render_dashes_not_a_crash(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "AAA", qty=0, entry=0.0)
    _mark(led, {"AAA": {"qty": 0, "avg_entry": 0.0,
                        "market_value": 0.0, "unrealized_pl": 0.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "AAA" in html               # rendered, no exception


def test_a_symbol_missing_from_the_mark_shows_dashes(tmp_path, cfg):
    """A ledger position the broker no longer reports must not invent numbers."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "AAA", qty=10, entry=100.0)
    _open_position(led, "GONE", qty=1, entry=50.0)
    _mark(led, {"AAA": {"qty": 10, "avg_entry": 100.0,
                        "market_value": 1100.0, "unrealized_pl": 100.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "GONE" in html and "$110.00" in html


def test_a_broker_symbol_with_no_ledger_record_adds_no_row(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "AAA", qty=10, entry=100.0)
    _mark(led, {"AAA": {"qty": 10, "avg_entry": 100.0,
                        "market_value": 1100.0, "unrealized_pl": 100.0},
                "PHANTOM": {"qty": 3, "avg_entry": 10.0,
                            "market_value": 30.0, "unrealized_pl": 0.0}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "PHANTOM" not in html


# ---- staleness ----

def test_a_fresh_mark_is_timestamped():
    note = dashboard._mark_age_note(
        datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc))
    assert "valued" in note and "ET" in note and "old" not in note


def test_a_stale_mark_says_so():
    """Regenerated 3x a weekday: on a Monday a Friday mark is 3 days old, and
    an unlabelled number will eventually be read as live."""
    now = datetime.now(timezone.utc)
    note = dashboard._mark_age_note((now - timedelta(days=3)).isoformat(), now)
    assert "3d old" in note and "warn" in note


def test_a_missing_or_unparseable_timestamp_produces_no_note():
    now = datetime.now(timezone.utc)
    assert dashboard._mark_age_note(None, now) == ""
    assert dashboard._mark_age_note("not-a-date", now) == ""


# ---- the reader ----

def test_latest_mark_wins_over_earlier_ones(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _mark(led, {"AAA": {"qty": 1, "avg_entry": 1, "market_value": 1,
                        "unrealized_pl": 0}})
    _mark(led, {"BBB": {"qty": 2, "avg_entry": 2, "market_value": 2,
                        "unrealized_pl": 0}})
    mark, ts = dashboard.latest_position_mark(led.all_records())
    assert set(mark) == {"BBB"} and ts


def test_no_mark_returns_none_pair():
    assert dashboard.latest_position_mark([]) == (None, None)


# ---- the writer ----

def test_log_positions_mark_stores_the_raw_broker_fields(tmp_path, cfg):
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_positions_mark({"NVDA": {"qty": 4.0, "avg_entry": 198.0,
                                     "market_value": 846.0,
                                     "unrealized_pl": 54.0,
                                     "ignored_extra": "x"}})
    mark, _ = dashboard.latest_position_mark(led.all_records())
    assert mark == {"NVDA": {"qty": 4.0, "avg_entry": 198.0,
                             "market_value": 846.0, "unrealized_pl": 54.0}}


def test_an_empty_book_writes_no_mark(tmp_path, cfg):
    """Flat is not the same as unknown — an empty mark would blank the columns
    on a page that should simply say 'No open positions'."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    led.log_positions_mark({})
    assert dashboard.latest_position_mark(led.all_records()) == (None, None)


def test_a_marked_row_shows_the_BROKER_cost_basis_not_the_signal_price(tmp_path, cfg):
    """THE bug found by rendering the real page and reading it.

    The ledger's entry_price is the SIGNAL price, not the fill — live SPY
    signalled at 689.30 and filled at 753.14 (926 bps). Printing the signal
    next to a live "Now" invites (Now - Entry) and shows a gain where the
    account holds a loss. A marked row must reconcile: Cost -> Now -> Value ->
    Unrealized."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "SPY", qty=1, entry=689.30)      # signal price
    _mark(led, {"SPY": {"qty": 1, "avg_entry": 753.14,   # what it actually cost
                        "market_value": 738.93, "unrealized_pl": -14.21}})
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()

    assert "<th>Cost</th>" in html and "<th>Entry</th>" not in html
    assert "$753.14" in html, "cost basis must be the broker's avg_entry"
    assert "$689.30" not in html, "the signal price must not sit beside 'Now'"
    # and the arithmetic a reader would do now agrees with the stated P&L
    assert "-$14.21 (-1.89%)" in html


def test_an_unmarked_row_keeps_the_old_entry_column(tmp_path, cfg):
    """No mark: the table is exactly what shipped before, signal price and all."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    _open_position(led, "SPY", qty=1, entry=689.30)
    html = open(dashboard.render(cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "<th>Entry</th>" in html and "<th>Cost</th>" not in html
    assert "$689.30" in html
