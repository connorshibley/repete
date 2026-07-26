"""The swing illustration: valid asset, honest mood, and no new fetches.

Why this file exists
--------------------
Adding the first binary asset to a page that had never had one moves three
things that were previously guaranteed by there being nothing to get wrong:

  1. The page was self-contained. An <img> is the obvious way to break that,
     and `scripts/publish_dashboard.sh` copies four hardcoded named files --
     no glob, no directory copy -- so a linked asset could ship a commit
     behind its HTML and render as a broken image on the live site.
  2. The hero robot's expression tracked P/L. The illustration only smiles,
     so on a losing day the page must fall back to the SVG robot rather than
     grin above a red number.
  3. Page weight had no ceiling because nothing large could be inlined.

Each of those is pinned below.
"""
import os
import re

import pytest

import dashboard
from ledger import Ledger
from test_dashboard import _cfg_paths

# 240 CSS px at 2x, WebP with a real alpha channel. Generous headroom over the
# ~40 KB the current build produces, but low enough that inlining an
# unoptimised master (the 660 KB PNG this was derived from) fails here.
ASSET_BUDGET = 120_000
PAGE_BUDGET = 220_000


def _render(cfg, tmp_path, total_pl):
    """Render a page whose total P/L has the sign we want to exercise."""
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    tid = led.log_decision("AAA", "buy", "s", {}, None, executed=True,
                           entry_price=100.0, qty=5, strategy="meanrev")
    led.close_trade(tid, exit_price=100.0 + total_pl / 5,
                    pnl=total_pl, pnl_pct=total_pl / 500 * 100,
                    exit_reason="take_profit" if total_pl >= 0 else "stop_loss")
    return open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()


# ---- the asset itself ----

def test_asset_exists_and_is_really_a_webp_with_alpha():
    """Magic bytes, not the file extension.

    VP8X is WebP's extended chunk -- it is what carries the alpha flag. A
    plain VP8 chunk would mean the alpha silently did not survive encoding,
    which on the hero's gradient card shows up as a white rectangle.
    """
    assert os.path.exists(dashboard.SWING_ASSET), dashboard.SWING_ASSET
    head = open(dashboard.SWING_ASSET, "rb").read(16)
    assert head[:4] == b"RIFF" and head[8:12] == b"WEBP", head
    assert head[12:16] == b"VP8X", (
        "no VP8X chunk -- this WebP has no alpha channel")


def test_asset_stays_small_enough_to_inline():
    n = os.path.getsize(dashboard.SWING_ASSET)
    assert n <= ASSET_BUDGET, f"{n:,} bytes is too large to base64 into a page"


# ---- what actually reaches the page ----

def test_profit_page_shows_the_swing_as_an_inline_data_uri(tmp_path, cfg):
    html = _render(cfg, tmp_path, 250.0)
    assert "class=swing" in html
    assert "src=\"data:image/webp;base64," in html


def test_losing_page_falls_back_to_the_determined_robot(tmp_path, cfg):
    """The mood decision, pinned.

    `mouth-flat` is _robot()'s losing face. If someone later simplifies
    _hero() to always use the illustration, this is what says no: the page
    would be showing a cheerful bot on a swing directly above a red total.
    """
    html = _render(cfg, tmp_path, -250.0)
    assert "mouth-flat" in html
    assert "class=swing" not in html, (
        "the swing illustration only smiles -- it must not appear on a "
        "losing book")


def test_the_two_branches_really_do_differ(tmp_path, cfg):
    """Guards against both branches collapsing to the same output.

    Asserting each side in isolation would still pass if _hero() ignored the
    sign entirely and some other part of the page happened to supply the
    strings. Comparing the renders is what makes the branch load-bearing.
    """
    win = _render(cfg, tmp_path / "w", 250.0)
    loss = _render(cfg, tmp_path / "l", -250.0)
    assert ("class=swing" in win) != ("class=swing" in loss)


def test_the_image_carries_alt_text(tmp_path, cfg):
    """The hero's accessible name.

    Note this is NOT covered by test_tape_repeats_and_robot_present's
    aria-label assertion: that string still appears via the boot splash's
    _robot(), so it would keep passing with the hero image unlabelled.
    """
    html = _render(cfg, tmp_path, 250.0)
    m = re.search(r'<img class=swing[^>]*\balt="([^"]*)"', html)
    assert m, "swing image has no alt attribute"
    assert len(m.group(1).strip()) > 10, f"alt text too thin: {m.group(1)!r}"


def test_image_declares_its_size_to_avoid_reflow(tmp_path, cfg):
    """width/height on the tag reserve the box before decode, so the hero
    does not jump once a 40 KB data URI finishes decoding."""
    html = _render(cfg, tmp_path, 250.0)
    m = re.search(r'<img class=swing[^>]*\bwidth="(\d+)"[^>]*\bheight="(\d+)"',
                  html)
    assert m, "swing image is missing intrinsic width/height"
    w, h = int(m.group(1)), int(m.group(2))
    assert 0.6 < (w / h) < 0.9, f"declared aspect {w}x{h} is not the artwork's"


# ---- the properties that existed before the asset did ----

def test_page_still_fetches_nothing_but_its_own_sidecar(tmp_path, cfg):
    """The dashboard renders offline. A data URI keeps that true; an
    src="repete_swing.webp" would not, and neither would a CDN font someone
    adds later while thinking about something else."""
    html = _render(cfg, tmp_path, 250.0)
    for m in re.finditer(r'\b(?:src|href)="([^"]+)"', html):
        u = m.group(1)
        if u.startswith(("data:", "#", "journal.html", "blog.html")):
            continue
        assert u.startswith("https://x.com/"), f"unexpected external ref: {u}"
    assert "dashboard_data.json" in html          # the one intended fetch


def test_whole_page_stays_under_budget(tmp_path, cfg):
    html = _render(cfg, tmp_path, 250.0)
    assert len(html) <= PAGE_BUDGET, (
        f"page is {len(html):,} bytes -- something large got inlined")


def test_the_bubble_does_not_vanish_into_the_hero_gradient():
    """The speech bubble used to be --surf2, which was fine beside a 62px
    robot at the light end of the hero gradient. The illustration pushes it
    to the far right, where the gradient IS --surf2 -- the bubble lost its
    outline entirely. Verified by screenshot before this was changed."""
    block = re.search(r"\.bubble\{(.*?)\}", dashboard.CSS, re.S).group(1)
    m = re.search(r"background:(#[0-9a-fA-F]{6}|var\(--\w+\))", block)
    assert m, "bubble has no explicit background"
    assert m.group(1) != "var(--surf2)", (
        "bubble fill matches the hero gradient's end stop -- it disappears")


def test_narrow_viewports_stack_the_bubble_under_the_image():
    """240px of illustration plus a 210px bubble needs ~430px side by side,
    which ran off the right edge of a 390px phone. Pinned because the failure
    is invisible on a desktop browser."""
    m = re.search(r"@media \(max-width:(\d+)px\)\{(.*?)\}\}", dashboard.CSS,
                  re.S)
    assert m, "no narrow-viewport rule for the hero"
    assert int(m.group(1)) >= 480, "breakpoint is too narrow to catch phones"
    rules = m.group(2)
    assert "flex-wrap:wrap" in rules, "robotbox must wrap on narrow screens"
    assert ".swing" in rules, "illustration must shrink on narrow screens"


def test_swing_animation_respects_reduced_motion():
    """Every other animated element is already in this block. A pendulum that
    ignores it is the one most likely to bother someone."""
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\{(.*?)\}\}",
                      dashboard.CSS, re.S)
    assert block, "reduced-motion block not found"
    assert ".swing" in block.group(1)


# ---- it cannot break a render ----

def test_a_missing_asset_falls_back_instead_of_raising(tmp_path, cfg,
                                                       monkeypatch):
    """Decorative art must never be able to take down the page.

    The cache is cleared first, otherwise an earlier test in the same session
    has already loaded the real bytes and this proves nothing.
    """
    monkeypatch.setattr(dashboard, "_SWING_CACHE", None)
    monkeypatch.setattr(dashboard, "SWING_ASSET",
                        str(tmp_path / "does-not-exist.webp"))
    html = _render(cfg, tmp_path, 250.0)
    assert "class=swing" not in html
    assert 'class=robot' in html          # the SVG stood in
    assert "[PAPER]" in html              # ...and the rest of the page rendered


def test_the_asset_is_read_once_not_per_render(tmp_path, cfg, monkeypatch):
    """render() runs every cycle; re-reading and re-base64ing 40 KB each time
    would be pure waste."""
    monkeypatch.setattr(dashboard, "_SWING_CACHE", None)
    calls = []
    real_open = open

    def counting_open(path, *a, **k):
        if str(path) == dashboard.SWING_ASSET:
            calls.append(path)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    dashboard._swing_data_uri()
    dashboard._swing_data_uri()
    dashboard._swing_data_uri()
    assert len(calls) == 1, f"asset re-read {len(calls)} times"


# ---- the build script's contract ----

def test_build_script_never_upscales_or_forgets_the_corner_check():
    """Both of these were live bugs during the first build, caught by the
    script's own assertions. Keeping them pinned means a future edit to the
    keying parameters cannot quietly reintroduce either."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "scripts", "build_swing_asset.py")).read()
    assert "if cw > WIDTH:" in src, "resize guard gone -- crops can be upscaled"
    assert "is not transparent" in src, "corner transparency assertion gone"
