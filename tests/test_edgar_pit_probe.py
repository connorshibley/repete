"""Can the EDGAR point-in-time probe actually REFUSE? (2026-08-10)

Same discipline as tests/test_fmp_lookahead_probe.py and
tests/test_alfred_vintages_probe.py: the probe decides whether a filings claim
may be pre-registered at all, so its failure path is the deliverable. A probe
that only ever passes is a formality that launders bad data into a gate.

Every check is driven from BOTH sides. Two cases deserve naming because they
are the ones a careless implementation gets wrong:

  - MIDNIGHT TIMESTAMPS. `acceptanceDateTime` present on every row looks like a
    pass, but if every value is midnight it is the filing date wearing a clock
    and cannot separate a pre-open filing from one accepted after the close.
  - AMENDMENT REPLACING ITS ORIGINAL. An amendment list that contains 10-K/A
    but no surviving 10-K means history was rewritten in place.

All offline: `probe.get` is monkeypatched, no network, no User-Agent needed.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "probe_edgar_pit", os.path.join(ROOT, "scripts", "probe_edgar_pit.py"))
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_edgar_pit"] = probe
_spec.loader.exec_module(probe)


def _router(monkeypatch, by_cik):
    """Serve canned submissions by CIK; anything unstubbed returns None."""
    def fake_get(path):
        for cik, payload in by_cik.items():
            if cik in path:
                return payload
        return None
    monkeypatch.setattr(probe, "get", fake_get)


def _subs(forms, filing_dates=None, acceptance=None, report_dates=None):
    n = len(forms)
    return {"filings": {"recent": {
        "form": list(forms),
        "filingDate": list(filing_dates or ["2023-02-01"] * n),
        "acceptanceDateTime": list(
            acceptance if acceptance is not None
            else ["2023-02-01T17:31:12.000Z"] * n),
        "reportDate": list(report_dates or ["2022-12-31"] * n),
        "accessionNumber": [f"0000000000-23-{i:06d}" for i in range(n)],
    }}}


_ALL_CIKS = [c for c, _, _ in probe.RESTATED]
_DEAD_CIKS = [c for c, _, _ in probe.DELISTED]


def _both_restated(payload):
    return {cik: payload for cik in _ALL_CIKS}


# --------------------------------------------------------------- check 1
def test_acceptance_timestamp_passes(monkeypatch):
    _router(monkeypatch, _both_restated(
        _subs(["8-K", "10-Q", "10-K"])))
    out = []
    assert probe.check_acceptance_timestamp(out) is True
    assert "PASS" in "\n".join(out)


def test_acceptance_timestamp_fails_when_all_midnight(monkeypatch):
    """Present on every row, and useless — it cannot distinguish a filing
    accepted before the open from one accepted after the close."""
    _router(monkeypatch, _both_restated(_subs(
        ["8-K", "10-Q"], acceptance=["2023-02-01T00:00:00.000Z"] * 2)))
    out = []
    assert probe.check_acceptance_timestamp(out) is False
    assert "midnight" in "\n".join(out)


def test_acceptance_timestamp_fails_when_some_rows_lack_it(monkeypatch):
    _router(monkeypatch, _both_restated(_subs(
        ["8-K", "10-Q"],
        acceptance=["2023-02-01T17:31:12.000Z", None])))
    out = []
    assert probe.check_acceptance_timestamp(out) is False


def test_acceptance_timestamp_undetermined_when_nothing_retrieved(monkeypatch):
    _router(monkeypatch, {})
    out = []
    assert probe.check_acceptance_timestamp(out) is None
    assert "UNDETERMINED" in "\n".join(out)


def test_non_timed_forms_are_ignored(monkeypatch):
    """Only 8-K/10-Q/10-K are counted; a universe of SC 13G rows answers
    nothing about tradeable timing."""
    _router(monkeypatch, _both_restated(_subs(["SC 13G", "4", "3"])))
    out = []
    assert probe.check_acceptance_timestamp(out) is None


# --------------------------------------------------------------- check 2
def test_no_retroactive_edit_passes_when_original_survives(monkeypatch):
    _router(monkeypatch, _both_restated(_subs(["10-K", "10-K/A", "10-Q"])))
    out = []
    assert probe.check_no_retroactive_edit(out) is True


def test_retroactive_edit_detected_when_original_gone(monkeypatch):
    """The amendment is there and the original is not — history rewritten in
    place, which is fatal for a point-in-time read."""
    _router(monkeypatch, _both_restated(_subs(["10-K/A", "10-Q"])))
    out = []
    assert probe.check_no_retroactive_edit(out) is False
    assert "FAIL" in "\n".join(out)


def test_no_amendments_is_undetermined_not_pass(monkeypatch):
    """No pair observed means the question was not answered."""
    _router(monkeypatch, _both_restated(_subs(["10-K", "10-Q"])))
    out = []
    assert probe.check_no_retroactive_edit(out) is None


def test_one_bad_issuer_fails_the_whole_check(monkeypatch):
    good, bad = _ALL_CIKS[0], _ALL_CIKS[1]
    _router(monkeypatch, {
        good: _subs(["10-K", "10-K/A"]),
        bad: _subs(["10-K/A"]),
    })
    out = []
    assert probe.check_no_retroactive_edit(out) is False


# --------------------------------------------------------------- check 3
def test_survivorship_passes_when_failed_issuers_retained(monkeypatch):
    _router(monkeypatch, {cik: _subs(["8-K", "10-K"]) for cik in _DEAD_CIKS})
    out = []
    assert probe.check_survivorship(out) is True


def test_survivorship_fails_when_failed_issuers_absent(monkeypatch):
    _router(monkeypatch, {})
    out = []
    assert probe.check_survivorship(out) is False
    assert "bankrupt" in "\n".join(out)


def test_partial_survivorship_coverage_fails(monkeypatch):
    """Partial coverage biases a screen in a way that cannot be corrected, so
    it is a FAIL and not a partial credit."""
    _router(monkeypatch, {_DEAD_CIKS[0]: _subs(["8-K"])})
    out = []
    assert probe.check_survivorship(out) is False


# --------------------------------------------------------------- hygiene
def test_rows_transpose_handles_ragged_arrays(monkeypatch):
    """EDGAR returns parallel arrays. A short column must not raise or
    silently shift values onto the wrong filing."""
    recent = {"form": ["8-K", "10-Q"], "filingDate": ["2023-01-01"],
              "acceptanceDateTime": [], "reportDate": None,
              "accessionNumber": ["x"]}
    rows = probe._rows(recent)
    assert len(rows) == 2
    assert rows[1]["filingDate"] is None
    assert rows[0]["form"] == "8-K"


def test_user_agent_is_never_printed(monkeypatch, capsys):
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Real Person real@example.com")
    _router(monkeypatch, {})
    out = []
    probe.check_acceptance_timestamp(out)
    probe.check_no_retroactive_edit(out)
    probe.check_survivorship(out)
    captured = capsys.readouterr()
    blob = captured.out + captured.err + "\n".join(out)
    assert "real@example.com" not in blob


def test_missing_user_agent_exits_rather_than_going_anonymous(monkeypatch):
    """EDGAR throttles unidentified traffic; guessing a User-Agent would be
    both rude and unreliable."""
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    try:
        probe._user_agent()
    except SystemExit as e:
        assert e.code == 2
    else:  # pragma: no cover
        raise AssertionError("missing User-Agent must not silently continue")
