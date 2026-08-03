"""The drawdown circuit breaker must be able to say that it has engaged.

Why this file exists (2026-08-03)
---------------------------------
§40 established that the drawdown rail is a ONE-WAY LATCH, and that this is a
LIVE defect rather than a backtest artifact:

    equity peaks at P -> a >=10% drawdown blocks every entry -> open positions
    exit normally and the book goes to cash -> in cash equity is FLAT at ~0.9P
    -> the peak stays P -> the drawdown stays >=10% -> the bot never buys
    again, for the rest of the run.

In 2022-2026 it blocked 99.43% of every buy signal. §41 (decay at unchanged
sizing) and §44 (decay plus reduced sizing) both tested candidate FIXES and
both were REJECTED, so the latch stays exactly as it is.

**Nothing here changes the rail.** What was missing is that the latch would
engage in COMPLETE SILENCE: `health.py` never reported it and `watchdog.py`
never checked it, so the first symptom available to a human would have been a
bot that quietly stopped buying, indefinitely, with every other check green.

Two properties are load-bearing and each is pinned in both directions:

  * the state is reported HONESTLY — an unknown drawdown must never read as a
    healthy one, and an unreadable high-water file must read as engaged, the
    same fail-closed polarity `read_high_water` already uses;
  * the alert TELLS YOU HOW TO CLEAR IT. A permanent condition reported as if
    it were transient is the same silent failure wearing a message.

The watchdog check is deliberately grouped with HALT rather than the heartbeat
checks: both are states that cannot clear themselves, so both re-alert every
day. A once-only alert would be wrong, and `test_the_alert_repeats` pins that.
"""
import json
import os

import pytest

import health
import risk
import watchdog

LIMIT = 10.0


def _seed_peak(tmp_path, peak):
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / ".equity_highwater.json").write_text(
        json.dumps({"peak_equity": peak, "updated": "2026-08-03T00:00:00+00:00"}))


def _cycle(ts, equity):
    return {"type": "event", "event": "cycle_complete", "ts": ts,
            "detail": json.dumps({"equity": equity, "n_positions": 3})}


# ------------------------------------------------------- risk.drawdown_state

def test_a_quiet_rail_reports_headroom_and_is_not_engaged(tmp_path, monkeypatch):
    """The normal case, and the live one: 0.12% down against a 10% rail."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    st = risk.drawdown_state(99_000.0, LIMIT)
    assert st["engaged"] is False
    assert st["drawdown_pct"] == pytest.approx(1.0)
    assert st["headroom_pp"] == pytest.approx(9.0)


def test_a_breached_rail_IS_engaged(tmp_path, monkeypatch):
    """Its paired half. Without this the fix could report headroom forever and
    never once say the word ENGAGED."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    st = risk.drawdown_state(89_000.0, LIMIT)
    assert st["engaged"] is True
    assert st["drawdown_pct"] == pytest.approx(11.0)
    assert st["headroom_pp"] == pytest.approx(-1.0)


def test_exactly_at_the_limit_is_engaged(tmp_path, monkeypatch):
    """`pre_trade_checks` blocks on `dd >= dd_cap`. The reporter must use the
    same comparison or it will disagree with the rail at the boundary."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    assert risk.drawdown_state(90_000.0, LIMIT)["engaged"] is True


def test_an_unreadable_high_water_file_reads_as_ENGAGED(tmp_path, monkeypatch):
    """Fail CLOSED, matching read_high_water's own polarity: if we cannot tell
    how far below the peak we are, we do not get to call it healthy."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    (tmp_path / "memory" / ".equity_highwater.json").write_text("{ not json")
    st = risk.drawdown_state(99_000.0, LIMIT)
    assert st["engaged"] is True
    assert "unreadable" in st["note"]


def test_an_unseeded_high_water_is_not_a_drawdown(tmp_path, monkeypatch):
    """First run. No peak to be below is not the same as being 100% below one,
    and reporting it as engaged would alert on every fresh install."""
    monkeypatch.chdir(tmp_path)
    st = risk.drawdown_state(99_000.0, LIMIT)
    assert st["engaged"] is False
    assert st["drawdown_pct"] == 0.0


def test_unknown_equity_is_reported_as_unknown_not_as_healthy(tmp_path,
                                                              monkeypatch):
    """The whole point of the `known` flag. A missing equity reading must not
    silently produce a comfortable-looking zero drawdown."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    st = risk.drawdown_state(None, LIMIT)
    assert st["known"] is False
    assert st["engaged"] is False
    assert st["drawdown_pct"] is None


def test_a_disabled_rail_never_engages(tmp_path, monkeypatch):
    """config.yaml: "0 disables". Verified against risk.py, where a 0 cap skips
    the check AND skips update_high_water entirely."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    st = risk.drawdown_state(50_000.0, 0)
    assert st["engaged"] is False
    assert "disabled" in st["note"]


def test_reading_the_state_never_writes_the_high_water_file(tmp_path,
                                                            monkeypatch):
    """health.py is documented read-only, and the publisher calls it. Reporting
    the rail must not be able to RATCHET it — a reporter that wrote the peak
    would move the very threshold it claims to observe."""
    monkeypatch.chdir(tmp_path)
    _seed_peak(tmp_path, 100_000.0)
    before = (tmp_path / "memory" / ".equity_highwater.json").read_text()
    risk.drawdown_state(150_000.0, LIMIT)      # a NEW high — would ratchet
    assert (tmp_path / "memory" / ".equity_highwater.json").read_text() == before


# ------------------------------------------------- health.last_known_equity

def test_last_known_equity_takes_the_most_recent_cycle():
    recs = [_cycle("2026-08-01T20:00:00+00:00", 98_000.0),
            _cycle("2026-08-03T20:00:00+00:00", 99_811.0)]
    assert health.last_known_equity(recs) == pytest.approx(99_811.0)


def test_last_known_equity_is_None_when_nothing_is_readable():
    """Paired with the above: unreadable must yield None, which
    `drawdown_state` then reports as unknown rather than as zero drawdown."""
    assert health.last_known_equity([]) is None
    assert health.last_known_equity(
        [{"type": "event", "event": "cycle_complete", "detail": "{oops"}]) is None


# --------------------------------------------------------- health.status()

def _env(tmp_path, monkeypatch, equity, peak, limit=LIMIT):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    _seed_peak(tmp_path, peak)
    with open(tmp_path / "memory" / "ledger.jsonl", "w") as f:
        f.write(json.dumps(_cycle("2026-08-03T20:00:00+00:00", equity)) + "\n")
    return {"memory": {"ledger_path": "memory/ledger.jsonl"}, "mode": "paper",
            "risk": {"max_drawdown_pct": limit}}


def _latch_problems(st):
    return [p for p in st["problems"] if "circuit breaker ENGAGED" in p]


def test_status_reports_headroom_while_quiet(tmp_path, monkeypatch):
    cfg = _env(tmp_path, monkeypatch, equity=99_000.0, peak=100_000.0)
    st = health.status(cfg=cfg, read_only=True)
    assert st["drawdown"]["headroom_pp"] == pytest.approx(9.0)
    assert _latch_problems(st) == []


def test_status_raises_a_problem_once_the_latch_engages(tmp_path, monkeypatch):
    cfg = _env(tmp_path, monkeypatch, equity=85_000.0, peak=100_000.0)
    st = health.status(cfg=cfg, read_only=True)
    assert _latch_problems(st), st["problems"]
    assert st["healthy"] is False


def test_the_problem_says_how_to_clear_it(tmp_path, monkeypatch):
    """THE LOAD-BEARING ASSERTION. §40's finding is not "entries are blocked",
    it is "entries are blocked FOREVER and nothing will undo it". A message
    that omits the reset path describes a transient state and would send an
    operator away to wait for a recovery that cannot happen."""
    cfg = _env(tmp_path, monkeypatch, equity=85_000.0, peak=100_000.0)
    problem = _latch_problems(health.status(cfg=cfg, read_only=True))[0]
    assert "cannot clear itself" in problem
    assert ".equity_highwater.json" in problem
    assert "exits still run" in problem


def test_an_unreadable_ledger_does_not_produce_a_confident_drawdown(
        tmp_path, monkeypatch):
    """No equity reading -> unknown, not engaged, not healthy-looking. The
    ledger problem is the one that gets reported; the rail stays silent rather
    than inventing a number."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    _seed_peak(tmp_path, 100_000.0)
    cfg = {"memory": {"ledger_path": "memory/does_not_exist.jsonl"},
           "risk": {"max_drawdown_pct": LIMIT}}
    st = health.status(cfg=cfg, read_only=True)
    assert st["drawdown"]["drawdown_pct"] is None
    assert _latch_problems(st) == []


# ------------------------------------------------------------ watchdog check

def _wd_env(tmp_path, monkeypatch, equity, peak, limit=LIMIT):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "memory").mkdir(exist_ok=True)
    _seed_peak(tmp_path, peak)
    (tmp_path / "config.yaml").write_text(
        f"risk:\n  max_drawdown_pct: {limit}\n"
        f"memory:\n  ledger_path: memory/ledger.jsonl\n")
    return [_cycle("2026-08-03T20:00:00+00:00", equity)]


def test_watchdog_is_silent_while_the_rail_is_quiet(tmp_path, monkeypatch):
    recs = _wd_env(tmp_path, monkeypatch, equity=99_000.0, peak=100_000.0)
    assert watchdog.drawdown_latch(recs)["engaged"] is False
    assert [p for p in watchdog.check(records=recs)
            if "circuit breaker" in p] == []


def test_watchdog_alerts_when_the_latch_engages(tmp_path, monkeypatch):
    """The check that did not exist. Without it the latch engages and every
    watchdog check still reports all clear."""
    recs = _wd_env(tmp_path, monkeypatch, equity=85_000.0, peak=100_000.0)
    hits = [p for p in watchdog.check(records=recs) if "circuit breaker" in p]
    assert hits, watchdog.check(records=recs)
    assert "cannot clear itself" in hits[0]


def test_the_alert_repeats(tmp_path, monkeypatch):
    """Grouped with HALT, not with the heartbeat checks: the condition is
    PERMANENT, so a once-only alert would fire into a quiet evening and never
    be seen again. Repetition is the design."""
    recs = _wd_env(tmp_path, monkeypatch, equity=85_000.0, peak=100_000.0)
    for _ in range(3):
        assert [p for p in watchdog.check(records=recs) if "circuit breaker" in p]


def test_watchdog_never_raises_when_config_is_missing(tmp_path, monkeypatch):
    """`Alerts degrade gracefully; the watchdog itself never raises` — the
    module docstring's promise, extended to the new check."""
    monkeypatch.chdir(tmp_path)
    assert watchdog.drawdown_latch([])["engaged"] is False
    assert isinstance(watchdog.check(records=[]), list)


def test_watchdog_and_health_agree_on_engagement(tmp_path, monkeypatch):
    """One implementation, two callers. If these ever disagree the operator
    gets a green health check and a red watchdog on the same state, which is
    the class of divergence this repo numbers."""
    for equity, expected in ((99_000.0, False), (85_000.0, True)):
        recs = _wd_env(tmp_path, monkeypatch, equity=equity, peak=100_000.0)
        cfg = {"memory": {"ledger_path": "memory/ledger.jsonl"},
               "risk": {"max_drawdown_pct": LIMIT}}
        with open(tmp_path / "memory" / "ledger.jsonl", "w") as f:
            f.write(json.dumps(recs[0]) + "\n")
        h = health.status(cfg=cfg, read_only=True)["drawdown"]["engaged"]
        w = watchdog.drawdown_latch(recs)["engaged"]
        assert h == w == expected, (equity, h, w)


def test_the_runbook_documents_the_reset(tmp_path):
    """§40 described the manual reset in prose in the research record. An
    operator paged at 4pm reads the runbook, not backtest_candidates.md."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(health.__file__)))
    docs = os.path.join(root, "docs")
    hits = []
    for name in os.listdir(docs):
        if name.endswith(".md"):
            with open(os.path.join(docs, name)) as f:
                if ".equity_highwater.json" in f.read():
                    hits.append(name)
    assert hits, "no doc explains how to clear the drawdown latch"
