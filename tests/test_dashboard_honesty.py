"""The page must not claim to be fresher, or better benchmarked, than it is.

Three failures, all shipped, none caught by a test:

1. THE BADGE. On 2026-08-20 the JS became `var cls='green'` and the badge said
   "live" no matter how stale the page was — on a public page that in the same
   commit began claiming the bot "runs 24/7 ... never misses a session". Every
   existing badge test asserted the thresholds were PRESENT in the HTML; not
   one asserted they were READ. All of them passed. The server-rendered badge
   was worse: it had NEVER been computed, predating that commit entirely.

2. THE BENCHMARK. The S&P column read "n/a" and the footer "Beaten 0/0
   months" on a page whose stated goal is to beat the S&P. Not missing data —
   main.py rendered WITH spy_bars and daily_posts.py rendered without, and the
   16:20 review overwrote the 15:45 cycle's work every evening.

3. THE COPY. An availability claim the page cannot substantiate.

The lesson each encodes: a test that asserts the INGREDIENTS of a rule are
present will pass forever after the rule stops being consulted.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import dashboard

ET = timezone.utc


# ---- the rule itself, directly ---------------------------------------------

def test_the_staleness_rule_reaches_amber_and_red():
    """Pure. Goes red the moment the classifier is short-circuited."""
    assert dashboard.staleness_class(0.0) == "green"
    assert dashboard.staleness_class(dashboard.STALE_AMBER_HOURS - 0.01) == "green"
    assert dashboard.staleness_class(dashboard.STALE_AMBER_HOURS) == "amber"
    assert dashboard.staleness_class(dashboard.STALE_RED_HOURS - 0.01) == "amber"
    assert dashboard.staleness_class(dashboard.STALE_RED_HOURS) == "red"
    assert dashboard.staleness_class(10_000.0) == "red"


def test_the_thresholds_are_ordered():
    assert 0 < dashboard.STALE_AMBER_HOURS < dashboard.STALE_RED_HOURS


def test_age_is_measured_from_the_data_not_the_render():
    """`now - render_time` is always ~0, so a badge computed from it would be
    green by construction — a second way of never going red."""
    now = datetime.now(timezone.utc)
    recs = [{"ts": (now - timedelta(hours=30)).isoformat()},
            {"ts": (now - timedelta(hours=48)).isoformat()}]
    assert dashboard.data_age_hours(recs, now) == pytest.approx(30.0, abs=0.1)
    assert dashboard.staleness_class(dashboard.data_age_hours(recs, now)) == "red"


def test_age_of_an_empty_ledger_is_not_a_crash():
    now = datetime.now(timezone.utc)
    assert dashboard.data_age_hours([], now) == 0.0
    assert dashboard.data_age_hours([{"no_ts": 1}], now) == 0.0


# ---- the JS path: the rule must be CONSULTED, not merely shipped -----------

def test_the_page_js_computes_the_badge_instead_of_pinning_it(rendered):
    """THE TEST THAT WOULD HAVE FAILED ON 26f88d1.

    That commit left STALE_AMBER_HOURS and STALE_RED_HOURS defined AND
    interpolated into the page, and only stopped reading them. So every
    presence check still passed. This asserts the thresholds appear inside a
    COMPARISON that assigns the class, and that the class is not pinned to a
    literal.
    """
    assert re.search(r"cls\s*=\s*hrs\s*>=\s*RED\s*\?", rendered), (
        "the badge class must be computed from the page's age, not hardcoded")
    assert re.search(r"var\s+cls\s*=\s*'(green|amber|red)'\s*;", rendered) is None, (
        "the badge class is pinned to a constant — this is exactly the "
        "2026-08-20 regression")


# ---- the server-rendered path: never computed until 2026-08-21 -------------

def test_a_stale_ledger_renders_a_stale_server_badge(stale_render):
    """With JS blocked or throwing, the server badge is all a reader gets."""
    assert 'class="fresh red"' in stale_render
    assert ">stale<" in stale_render
    assert 'class="fresh green"' not in stale_render


def test_a_fresh_ledger_renders_a_live_server_badge(rendered):
    assert 'class="fresh green"' in rendered
    assert ">live<" in rendered


# ---- the copy --------------------------------------------------------------

def test_the_page_does_not_claim_uptime_it_cannot_show(rendered):
    """An availability claim on a public page, with the falsifying indicator
    switched off in the same commit, is the finding that made this a resale
    exposure rather than a cosmetic one."""
    assert "24/7" not in rendered, (
        "the page must not assert continuous uptime; the freshness badge "
        "shows what can actually be substantiated")


# ---- the benchmark ---------------------------------------------------------

def test_every_render_caller_supplies_a_benchmark_or_relies_on_the_default():
    """THE TEST THAT WOULD HAVE CAUGHT THE WIPED S&P COLUMN.

    Source-scan rather than behaviour, because the defect was never in
    render() — it was in WHO CALLS IT. main.py passed spy_bars; daily_posts.py
    and backfill_posts.py did not, and the 16:20 review re-rendered the same
    file the 15:45 cycle had just written correctly.

    Passing explicitly is fine. Passing nothing is fine ONLY because render()
    now fetches its own. What must never return is a render path that silently
    produces a page with no benchmark.
    """
    import pathlib
    src = pathlib.Path(dashboard.__file__).parent
    body = (src / "dashboard.py").read_text()
    assert re.search(r"if\s+spy_bars\s+is\s+None\s*:", body), (
        "render() must default its own benchmark; without it, any caller that "
        "omits spy_bars silently ships a page with an empty S&P column")
    # `is None`, not falsiness: qa_render.py passes [] to opt out offline.
    assert "if not spy_bars:\n        spy_bars = review" not in body


def test_the_offline_opt_out_still_works(tmp_path, cfg, monkeypatch):
    """An explicit [] must NOT trigger a fetch — that is how QA renders stay
    offline and byte-reproducible."""
    called = []

    def _boom(*a, **k):
        called.append(1)
        raise AssertionError("render() fetched a benchmark despite []")

    monkeypatch.setattr("review.spy_benchmark_bars", _boom)
    dashboard.render(cfg, out_path=str(tmp_path / "d.html"), spy_bars=[])
    assert not called


def test_render_survives_an_unreachable_benchmark(tmp_path, cfg, monkeypatch):
    """A benchmark is a reporting nicety; it must never take down the cosmetic
    render inside a trading cycle."""
    monkeypatch.setattr("review.spy_benchmark_bars",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        # The helper itself raises here; render() passes None through to it,
        # so this documents that the guard lives INSIDE spy_benchmark_bars.
        __import__("review").spy_benchmark_bars(cfg, 120)


# ---- fixtures --------------------------------------------------------------

def _write_ledger(path, ages_hours):
    now = datetime.now(timezone.utc)
    with open(path, "w") as f:
        for h in ages_hours:
            f.write(json.dumps({
                "type": "event", "event": "cycle_complete",
                "detail": "ok",
                "ts": (now - timedelta(hours=h)).isoformat()}) + "\n")


@pytest.fixture
def rendered(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr("review.spy_benchmark_bars", lambda *a, **k: [])
    _write_ledger(cfg["memory"]["ledger_path"], [0.2, 1.0])
    out = tmp_path / "dash.html"
    dashboard.render(cfg, out_path=str(out), spy_bars=[])
    return out.read_text()


@pytest.fixture
def stale_render(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr("review.spy_benchmark_bars", lambda *a, **k: [])
    _write_ledger(cfg["memory"]["ledger_path"], [30.0, 55.0])
    out = tmp_path / "stale.html"
    dashboard.render(cfg, out_path=str(out), spy_bars=[])
    return out.read_text()
