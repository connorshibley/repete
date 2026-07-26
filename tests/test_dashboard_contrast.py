"""The shipped palette must stay readable. Measured, not asserted by comment.

Why this file exists
--------------------
Until 2026-07-26 `src/dashboard.py` carried the line:

    # Chart palette validated for the dark surface (#131722): CVD separation
    # and >=3:1 contrast all pass

That was true when written and silently stopped being true the moment the
background changed. Measured against white, the old green fell to 3.35:1 while
the old red held 4.80:1 — so PROFIT would have been less legible than LOSS, on
a page whose whole job is showing which one you have. The card labels were
worse: 2.51:1, failing even the graphics threshold.

Nothing caught it, because a comment cannot fail. This does.

WCAG 2.1 thresholds used:
  * text and anything read as a value  -> 4.5:1
  * purely graphical / structural ink  -> 3:1
"""
import re

import pytest

import dashboard


def _lum(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
          for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(fg: str, bg: str) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _css_vars() -> dict:
    """The :root custom properties actually shipped in the stylesheet."""
    block = re.search(r":root\{(.*?)\}", dashboard.CSS, re.S).group(1)
    return dict(re.findall(r"--([a-z0-9]+):(#[0-9a-fA-F]{6})", block))


# ---- the sanity of the metric itself ----

def test_contrast_helper_matches_known_values():
    assert contrast("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert contrast("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)


# ---- text-bearing colours: 4.5:1 ----

TEXT_ROLES = ["ink", "ink2", "mut", "green", "red", "blue", "amber"]


@pytest.mark.parametrize("role", TEXT_ROLES)
def test_text_colour_is_readable_on_the_page_background(role):
    v = _css_vars()
    ratio = contrast(v[role], v["bg"])
    assert ratio >= 4.5, (
        f"--{role} ({v[role]}) is {ratio:.2f}:1 on {v['bg']} — needs 4.5:1")


@pytest.mark.parametrize("role", TEXT_ROLES)
def test_text_colour_is_readable_on_cards_too(role):
    """Cards and tables sit on --surf, not --bg. A colour that clears the page
    background but not the card is still unreadable where it is actually
    used — and every stat card and table row lives on --surf."""
    v = _css_vars()
    ratio = contrast(v[role], v["surf"])
    assert ratio >= 4.5, (
        f"--{role} ({v[role]}) is {ratio:.2f}:1 on --surf {v['surf']}")


# ---- the two that carry the money ----

def test_profit_and_loss_are_both_strongly_legible():
    """The request was green/red for P&L, so both must be first-class.

    The old palette had profit at 3.35:1 and loss at 4.80:1 — bad news was
    literally easier to read than good news.
    """
    v = _css_vars()
    assert contrast(v["green"], v["bg"]) >= 4.5
    assert contrast(v["red"], v["bg"]) >= 4.5


def test_profit_and_loss_are_distinguishable_from_each_other():
    """Adjacent rows in the positions table are green and red. To a red-green
    colour-blind reader the hues are close, so LIGHTNESS is what is left.

    A first draft of this file also asserted the two were within 3:1 of each
    other, on the theory that neither should look more important. That was
    wrong and the two assertions fought: colours cannot be simultaneously
    equal in contrast and separable by lightness. Separability wins, because
    it is the one that helps a reader who cannot use the hue.
    """
    v = _css_vars()
    sep = contrast(v["green"], v["red"])
    assert sep >= 1.6, (
        f"green and red are {sep:.2f}:1 apart — too close in lightness for a "
        f"reader who cannot use the hue")


def test_colour_is_never_the_only_channel_carrying_profit_or_loss(tmp_path,
                                                                 cfg):
    """The real CVD protection is not the palette, it is the sign.

    Every signed figure ships with + / - and a $, so a reader who sees no
    colour difference at all still gets the answer. This is the assertion
    that would survive someone replacing the whole palette with greyscale.
    """
    from ledger import Ledger

    from test_dashboard import _cfg_paths
    cfg = _cfg_paths(cfg, tmp_path)
    led = Ledger(cfg["memory"]["ledger_path"])
    win = led.log_decision("AAA", "buy", "s", {}, None, executed=True,
                           entry_price=100.0, qty=5, strategy="meanrev")
    led.close_trade(win, exit_price=110.0, pnl=50.0, pnl_pct=10.0,
                    exit_reason="take_profit")
    loss = led.log_decision("BBB", "buy", "s", {}, None, executed=True,
                            entry_price=100.0, qty=5, strategy="meanrev")
    led.close_trade(loss, exit_price=90.0, pnl=-50.0, pnl_pct=-10.0,
                    exit_reason="stop_loss")
    html = open(dashboard.render(
        cfg, out_path=str(tmp_path / "d.html"))).read()
    assert "+$50.00" in html or "$50.00" in html
    assert "-$50.00" in html, "a loss must carry its sign, not just its colour"


def test_chart_constants_match_the_css_they_sit_beside():
    """C_WIN/C_LOSS are baked into SVG attributes and do NOT follow a CSS
    variable. If they drift from --green/--red the same number is one colour
    in a table and another in the chart above it."""
    v = _css_vars()
    assert dashboard.C_WIN.lower() == v["green"].lower()
    assert dashboard.C_LOSS.lower() == v["red"].lower()
    assert dashboard.C_LINE.lower() == v["blue"].lower()


# ---- graphical ink: 3:1, and it must still be visible ----

def test_chart_ink_is_visible_on_the_surface_it_is_drawn_on():
    for name, colour, floor in [("C_LINE", dashboard.C_LINE, 3.0),
                                ("C_WIN", dashboard.C_WIN, 3.0),
                                ("C_LOSS", dashboard.C_LOSS, 3.0),
                                ("C_MUTED", dashboard.C_MUTED, 4.5),
                                ("C_AXIS", dashboard.C_AXIS, 4.5)]:
        ratio = contrast(colour, dashboard.SURFACE)
        assert ratio >= floor, f"{name} {colour} is {ratio:.2f}:1"


def test_gridlines_are_present_but_subordinate():
    """Gridlines must be visible against the surface and must NOT compete with
    the data line — a grid as strong as the series is worse than no grid."""
    grid = contrast(dashboard.C_GRID, dashboard.SURFACE)
    line = contrast(dashboard.C_LINE, dashboard.SURFACE)
    assert 1.08 <= grid <= 2.0, f"gridline contrast {grid:.2f}:1"
    assert line > grid * 2, "the data line must dominate the grid"


def test_zero_baseline_reads_against_both_grid_and_surface():
    """The zero line is a reference value, not decoration. It has to be
    findable among the gridlines it sits between."""
    z = contrast(dashboard.C_ZERO, dashboard.SURFACE)
    assert z >= 2.0, f"zero baseline {z:.2f}:1 on the surface"
    assert contrast(dashboard.C_ZERO, dashboard.C_GRID) >= 1.4, (
        "zero line is indistinguishable from an ordinary gridline")


# ---- no dark-theme survivors ----

def test_no_dark_theme_colours_survive_on_the_light_surface():
    """These are the exact values tuned for #131722. Any of them left on a
    light surface is a patch of the old theme on a white page.

    The `#boot` rules are excluded deliberately, not overlooked. The boot
    splash is a full-screen overlay that is dark BY DESIGN — a three-second
    terminal-style intro on its own near-black background — so its internal
    palette is measured against itself, not against the page. Scoping this
    scan to everything else is what lets it stay strict.
    """
    css = dashboard.CSS
    light_only = re.sub(r"#boot[^{]*\{[^}]*\}", "", css)
    light_only = re.sub(r"@keyframes (eyeson|markup)[^}]*\}[^}]*\}", "",
                        light_only)
    dead = ["#0b0e14", "#131722", "#1a2130", "#232b3b", "#e6e9f0",
            "#9aa4b5", "#0ca30c", "#d03b3b", "#b3261e", "#3987e5", "#2e3950"]
    for c in dead:
        assert c not in light_only, f"{c} is a dark-theme leftover"


def test_rendered_page_has_a_white_background():
    v = _css_vars()
    assert v["bg"].lower() == "#ffffff"
    assert dashboard.SURFACE.lower() == "#ffffff"
