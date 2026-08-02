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


# ---------------- plan-gating is not a measurement (2026-08-02) ----------------
#
# Measured on the free tier: historical-price-eod/full served AAPL and MSFT 251
# rows each for 2022 and answered SIVB and FRC with http 402. The probe reported
# "0 historical bars ... no history for failed companies" — a decisive claim
# about the DATA, resting on a question the subscription never let it ask.
#
# The refusal was still the right call, so the bug is invisible in the verdict
# and only shows in the REASON. That is the dangerous kind: the reason is what
# gets carried into the next decision (here, whether upgrading the plan is worth
# it, and what to expect if it happens).

def test_survivorship_is_UNDETERMINED_when_delisted_names_are_plan_gated(
        monkeypatch):
    """402 is not 'no history'. It is 'not allowed to look'."""
    _router(monkeypatch, {"historical-price-eod/full": probe.PLAN_GATED})
    out = []
    assert probe.check_survivorship(out) is None
    joined = "\n".join(out)
    assert "SUBSCRIPTION" in joined
    # It must NOT keep asserting the thing it cannot know.
    assert "went bankrupt" not in joined
    assert "0 historical bars" not in joined


def test_survivorship_still_FAILS_on_a_genuine_empty_answer(monkeypatch):
    """The fix must not launder a real absence into 'undetermined' — an empty
    200 is still a measurement, and still a failure."""
    _router(monkeypatch, {"historical-price-eod/full": []})
    out = []
    assert probe.check_survivorship(out) is False


def test_filing_lag_is_UNDETERMINED_when_every_symbol_is_plan_gated(monkeypatch):
    """No row was read, so nothing was learned about whether dates exist."""
    _router(monkeypatch, {"income-statement": probe.PLAN_GATED})
    out = []
    assert probe.check_filing_lag(out) is None
    joined = "\n".join(out)
    assert "UNDETERMINED" in joined
    # The FAIL verdict must not be RENDERED. (Asserting on the sentence itself
    # would be a trap: the undetermined text quotes it to explain what it is
    # declining to say.)
    assert "VERDICT: FAIL" not in joined


def test_filing_lag_still_FAILS_when_rows_were_read_but_carry_no_date(
        monkeypatch):
    """Rows READ and dateless is a genuine FAIL; only 'read nothing' is
    undetermined. Without this the gating branch could swallow the real bug."""
    _router(monkeypatch, {"income-statement": [_stmt("2026-03-31", 1e9)]})
    out = []
    assert probe.check_filing_lag(out) is False
    assert "cannot be corrected" in "\n".join(out)


def test_get_returns_PLAN_GATED_on_402_and_None_on_other_http_errors(
        monkeypatch):
    """The sentinel must come from the transport layer, not be inferred later —
    by the time a check sees `[]` the status code is gone."""
    import urllib.error

    def raise_http(code):
        def _open(*a, **kw):
            raise urllib.error.HTTPError("u", code, "m", {}, None)
        return _open

    monkeypatch.setattr(probe, "_key", lambda: "k")
    monkeypatch.setattr(probe, "_CALLS", 0, raising=False)
    monkeypatch.setattr(probe.urllib.request, "urlopen", raise_http(402))
    assert probe.get("anything") is probe.PLAN_GATED

    monkeypatch.setattr(probe, "_CALLS", 0, raising=False)
    monkeypatch.setattr(probe.urllib.request, "urlopen", raise_http(403))
    assert probe.get("anything") is None


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


def test_an_all_undetermined_run_does_not_claim_the_data_carries_lookahead(
        monkeypatch, tmp_path, capsys):
    """The refusal stands, but its REASON must match its evidence.

    This is the shape of the 2026-08-02 bug: every check was refused with http
    402 and the report still announced 'FMP data carries lookahead' — a verdict
    about the vendor drawn from a fact about the subscription. Same exit code,
    wrong conclusion, and the conclusion is what gets carried forward.
    """
    monkeypatch.chdir(tmp_path)
    for name in ("check_restatement", "check_filing_lag", "check_survivorship"):
        monkeypatch.setattr(probe, name, lambda out: None)
    assert probe.main() == 1
    text = " ".join(capsys.readouterr().out.split())
    assert "REFUSING" in text
    assert "UNPROVEN, not convicted" in text
    assert "FMP data carries lookahead" not in text


def test_one_real_FAIL_still_convicts_even_alongside_undetermined_checks(
        monkeypatch, tmp_path, capsys):
    """The softer wording must not swallow a measured failure."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(probe, "check_restatement", lambda out: None)
    monkeypatch.setattr(probe, "check_filing_lag", lambda out: False)
    monkeypatch.setattr(probe, "check_survivorship", lambda out: None)
    assert probe.main() == 1
    text = " ".join(capsys.readouterr().out.split())
    assert "FMP data carries lookahead" in text
    assert "UNPROVEN, not convicted" not in text


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
    # It must also not restate the EDGE tally. This assertion used to read
    # `"0 for 12" in text`, which pinned a number the probe has no way to know
    # and which was already stale (§44 took it to 13 on 2026-08-02). A count
    # duplicated into a banner drifts from the ledger that owns it and then
    # gets quoted as if it were checked.
    assert "knowledge/backtest_candidates.md" in text
    import re
    assert not re.search(r"0 for \d+", text)


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
