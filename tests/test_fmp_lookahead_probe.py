"""Can the FMP lookahead probe actually REFUSE? (2026-07-30)

The probe decides whether a fundamentals claim may be pre-registered at all. Its
whole value is the failure path — a probe that only ever passes is not a probe,
it is a formality that launders bad data into a gate. §28 is the precedent: a
fully-drafted gate was never registered because a probe said the mechanism could
not move its metric.

So every check here is driven from BOTH sides. The pass cases are cheap; the
fail and undetermined cases are the point.

All offline: `probe.get` is monkeypatched, no network, no API key.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "probe_fmp_lookahead", os.path.join(ROOT, "scripts",
                                        "probe_fmp_lookahead.py"))
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_fmp_lookahead"] = probe
_spec.loader.exec_module(probe)


def _router(monkeypatch, table):
    """Serve canned JSON by exact path; assert nothing else is requested.

    /stable/ paths are fixed strings — the symbol travels as a `symbol=`
    kwarg, not embedded in the path — so this matches path exactly rather
    than by prefix (the old /api/v3/{path}/{symbol} convention needed
    prefix matching; this one doesn't).
    """
    def fake_get(path, **params):
        return table.get(path)
    monkeypatch.setattr(probe, "get", fake_get)


def _stmt(date_, revenue, filed=None):
    r = {"date": date_, "revenue": revenue}
    if filed:
        r["fillingDate"] = filed
    return r


# ---------------- 1. restatement ----------------

def test_restatement_passes_when_both_series_agree(monkeypatch):
    rows = [_stmt("2016-09-30", 6_000_000_000)]
    _router(monkeypatch, {
        "income-statement-as-reported": [{"date": "2016-09-30",
                                          "revenues": 6_000_000_000}],
        "income-statement": rows,
    })
    out = []
    assert probe.check_restatement(out) is True
    assert "PASS" in "\n".join(out)


def test_restatement_FAILS_when_the_series_disagree(monkeypatch):
    """The leak, measured. A >1% gap between standard and as-reported means the
    standard series carries corrections made after the fact."""
    _router(monkeypatch, {
        "income-statement-as-reported": [{"date": "2016-09-30",
                                          "revenues": 6_000_000_000}],
        "income-statement": [_stmt("2016-09-30", 5_400_000_000)],
    })
    out = []
    assert probe.check_restatement(out) is False
    joined = "\n".join(out)
    assert "FAIL" in joined
    assert "AFTER the fact" in joined


def test_a_missing_as_reported_endpoint_is_UNDETERMINED_not_a_pass(monkeypatch):
    """An unanswerable question must not be scored as a passed one — the exact
    trap that lets plan-gated data look clean."""
    _router(monkeypatch, {"income-statement": [_stmt("2016-09-30", 6e9)]})
    out = []
    assert probe.check_restatement(out) is None
    assert "UNDETERMINED" in "\n".join(out)


# ---------------- 2. filing lag ----------------

def test_filing_lag_passes_on_a_realistic_lag(monkeypatch):
    _router(monkeypatch, {"income-statement": [
        _stmt("2026-03-31", 1e9, filed="2026-05-08"),
        _stmt("2025-12-31", 1e9, filed="2026-02-06"),
    ]})
    out = []
    assert probe.check_filing_lag(out) is True


def test_filing_lag_FAILS_when_no_filing_date_exists(monkeypatch):
    """The quietest lookahead: without a filing date a backtest keys off period
    end and trades on figures nobody had for another six weeks."""
    _router(monkeypatch, {"income-statement": [_stmt("2026-03-31", 1e9)]})
    out = []
    assert probe.check_filing_lag(out) is False
    assert "cannot be corrected" in "\n".join(out)


def test_filing_lag_FAILS_when_the_date_is_really_the_period_end(monkeypatch):
    """A zero-day 'filing date' is the period end wearing another name."""
    _router(monkeypatch, {"income-statement": [
        _stmt("2026-03-31", 1e9, filed="2026-03-31"),
        _stmt("2025-12-31", 1e9, filed="2025-12-31"),
    ]})
    out = []
    assert probe.check_filing_lag(out) is False


def test_filing_lag_FAILS_when_only_some_rows_carry_a_date(monkeypatch):
    _router(monkeypatch, {"income-statement": [
        _stmt("2026-03-31", 1e9, filed="2026-05-08"),
        _stmt("2025-12-31", 1e9),
    ]})
    out = []
    assert probe.check_filing_lag(out) is False


# ---------------- 3. survivorship ----------------

def test_survivorship_passes_when_dead_names_keep_history(monkeypatch):
    _router(monkeypatch, {"historical-price-eod/full":
                          [{"date": "2023-03-01"}]})
    out = []
    assert probe.check_survivorship(out) is True


def test_survivorship_FAILS_when_dead_names_are_absent(monkeypatch):
    _router(monkeypatch, {"historical-price-eod/full": []})
    out = []
    assert probe.check_survivorship(out) is False
    assert "went bankrupt" in "\n".join(out)


def test_survivorship_FAILS_on_partial_coverage(monkeypatch):
    seen = {"n": 0}

    def fake_get(path, **params):
        seen["n"] += 1
        return [{"date": "x"}] if seen["n"] == 1 else []
    monkeypatch.setattr(probe, "get", fake_get)
    out = []
    assert probe.check_survivorship(out) is False


# ---------------- the verdict ----------------

def test_main_exits_nonzero_and_REFUSES_when_a_check_fails(monkeypatch, tmp_path,
                                                           capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "check_restatement", lambda out: True)
    monkeypatch.setattr(probe, "check_filing_lag", lambda out: False)
    monkeypatch.setattr(probe, "check_survivorship", lambda out: True)
    assert probe.main() == 1
    # Whitespace-normalised: the refusal is hard-wrapped for the terminal, so a
    # raw substring would break on the line boundary rather than on the meaning.
    text = " ".join(capsys.readouterr().out.split())
    assert "REFUSING" in text
    assert "Do NOT register a fundamentals gate on this source" in text
    assert "LIVE JUDGE CONTEXT ONLY" in text


def test_main_exits_nonzero_on_UNDETERMINED_too(monkeypatch, tmp_path, capsys):
    """Undetermined is not a pass. Without this, plan-gated endpoints would
    quietly bless the source."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "check_restatement", lambda out: None)
    monkeypatch.setattr(probe, "check_filing_lag", lambda out: True)
    monkeypatch.setattr(probe, "check_survivorship", lambda out: True)
    assert probe.main() == 1
    assert "REFUSING" in capsys.readouterr().out


def test_main_exits_zero_and_blesses_only_when_all_three_pass(monkeypatch,
                                                              tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    for name in ("check_restatement", "check_filing_lag", "check_survivorship"):
        monkeypatch.setattr(probe, name, lambda out: True)
    assert probe.main() == 0
    text = capsys.readouterr().out
    assert "All three checks PASS" in text
    # Even the blessing must not overclaim.
    assert "does NOT mean fundamentals help" in text
    assert "0 for 12" in text


def test_the_report_is_written_whichever_way_the_verdict_goes(monkeypatch,
                                                             tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "check_restatement", lambda out: False)
    monkeypatch.setattr(probe, "check_filing_lag", lambda out: False)
    monkeypatch.setattr(probe, "check_survivorship", lambda out: False)
    probe.main()
    written = list((tmp_path / "research").glob("fmp_lookahead_*.txt"))
    assert written, "a negative result is a finding and must be recorded"
    assert "REFUSING" in written[0].read_text()


# ---------------- the key is never printed ----------------

def test_the_api_key_never_reaches_stdout_or_stderr(monkeypatch, capsys):
    """`get()` builds a URL containing the key. A traceback or an error line
    that quoted the URL would put it in the terminal and any log scraping it."""
    monkeypatch.setenv("FMP_API_KEY", "sk-fmp-supersecret-value-12345")
    probe._CALLS = 0

    class Boom:
        def __call__(self, *a, **k):
            raise OSError("network down")
    monkeypatch.setattr(probe.urllib.request, "urlopen", Boom())
    assert probe.get("income-statement", symbol="AAPL") is None
    cap = capsys.readouterr()
    assert "supersecret" not in cap.out + cap.err


def test_the_call_budget_is_enforced(monkeypatch, capsys):
    """250/day on the free tier. A probe that silently burned the budget would
    leave the owner unable to run it again the same day."""
    monkeypatch.setenv("FMP_API_KEY", "k" * 30)
    probe._CALLS = probe.CALL_BUDGET
    assert probe.get("income-statement", symbol="AAPL") is None
    assert "budget" in capsys.readouterr().err
    probe._CALLS = 0
