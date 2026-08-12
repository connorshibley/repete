"""Can the ALFRED vintage probe actually REFUSE? (2026-08-10)

The probe decides whether a macro claim may be pre-registered at all. Its whole
value is the failure path — a probe that only ever passes is not a probe, it is
a formality that launders bad data into a gate. §28 and §46 are the precedent:
a fully-drafted gate was never registered because a probe said so.

So every check here is driven from BOTH sides. The pass cases are cheap; the
fail and undetermined cases are the point. Special attention goes to check 2's
NEGATIVE CONTROL, which is the reason that check means anything: if a
never-revised series also showed multiple values, a "divergence" on the revised
series would be noise rather than evidence, and the probe must say UNDETERMINED
rather than PASS.

All offline: `probe.get` is monkeypatched, no network, no API key.
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "probe_alfred_vintages", os.path.join(ROOT, "scripts",
                                          "probe_alfred_vintages.py"))
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_alfred_vintages"] = probe
_spec.loader.exec_module(probe)


def _router(monkeypatch, table, seen=None):
    """Serve canned JSON by (path, series_id); anything unstubbed returns None.

    An unstubbed path must never silently succeed — that would let a test pass
    because the probe asked a question nobody answered.

    The fake HONOURS `observation_start`, because the real API does. An earlier
    version of this router returned every canned row for every query, which
    made a correct probe look broken: it took the minimum realtime_start across
    all five months and reported negative lags. A fake loose enough to fail a
    working implementation is worse than no fake.
    """
    def fake_get(path, **params):
        if seen is not None:
            seen.append((path, params.get("series_id")))
        for (prefix, series), payload in table.items():
            if path.startswith(prefix) and (series is None
                                            or params.get("series_id") == series):
                start = params.get("observation_start")
                if start and isinstance(payload, dict) and "observations" in payload:
                    rows = [r for r in payload["observations"]
                            if r.get("date") == start]
                    return {"observations": rows}
                return payload
        return None
    monkeypatch.setattr(probe, "get", fake_get)


def _obs(rows):
    return {"observations": [
        {"realtime_start": rs, "realtime_end": re_, "date": d, "value": v}
        for rs, re_, d, v in rows]}


def _vintages(n):
    return {"vintage_dates": [f"2020-{1 + (i % 12):02d}-01" for i in range(n)]}


# --------------------------------------------------------------- check 1
def test_vintages_exist_passes_with_many(monkeypatch):
    _router(monkeypatch, {("series/vintagedates", None): _vintages(40)})
    out = []
    assert probe.check_vintages_exist(out) is True
    assert "PASS" in "\n".join(out)


def test_vintages_exist_fails_with_single_vintage(monkeypatch):
    """One vintage is FRED-current wearing ALFRED's name — the §31 trap."""
    _router(monkeypatch, {("series/vintagedates", None): _vintages(1)})
    out = []
    assert probe.check_vintages_exist(out) is False
    assert "FAIL" in "\n".join(out)


def test_vintages_exist_fails_without_vintage_field(monkeypatch):
    _router(monkeypatch, {("series/vintagedates", None): {"observations": []}})
    out = []
    assert probe.check_vintages_exist(out) is False


def test_vintages_exist_undetermined_when_endpoint_silent(monkeypatch):
    _router(monkeypatch, {})
    out = []
    assert probe.check_vintages_exist(out) is None
    assert "UNDETERMINED" in "\n".join(out)


# --------------------------------------------------------------- check 2
_REVISED_MANY = _obs([
    ("2020-07-30", "2020-08-26", "2020-04-01", "17302.5"),
    ("2020-08-27", "2020-09-29", "2020-04-01", "17258.2"),
    ("2020-09-30", "9999-12-31", "2020-04-01", "17302.0"),
])
_REVISED_ONE = _obs([("2020-07-30", "9999-12-31", "2020-04-01", "17302.5")])
_CONTROL_ONE = _obs([("2023-01-04", "9999-12-31", "2023-01-01", "3.79")])
_CONTROL_MANY = _obs([
    ("2023-01-04", "2023-02-01", "2023-01-01", "3.79"),
    ("2023-02-02", "9999-12-31", "2023-01-01", "3.55"),
])


def test_revision_divergence_passes(monkeypatch):
    _router(monkeypatch, {
        ("series/observations", "GDPC1"): _REVISED_MANY,
        ("series/observations", "DGS10"): _CONTROL_ONE,
    })
    out = []
    assert probe.check_revision_divergence(out) is True


def test_revision_divergence_fails_when_all_vintages_identical(monkeypatch):
    """A series revised three times a quarter showing one value means the
    realtime metadata is decoration over a current-values feed."""
    _router(monkeypatch, {
        ("series/observations", "GDPC1"): _REVISED_ONE,
        ("series/observations", "DGS10"): _CONTROL_ONE,
    })
    out = []
    assert probe.check_revision_divergence(out) is False
    assert "FAIL" in "\n".join(out)


def test_negative_control_firing_forces_undetermined(monkeypatch):
    """THE POINT OF THE CONTROL. The revised series diverges — which alone
    looks like a pass — but a never-revised market rate diverges too, so the
    check cannot tell revision from noise and must refuse to pass."""
    _router(monkeypatch, {
        ("series/observations", "GDPC1"): _REVISED_MANY,
        ("series/observations", "DGS10"): _CONTROL_MANY,
    })
    out = []
    assert probe.check_revision_divergence(out) is None
    assert "UNDETERMINED" in "\n".join(out)


def test_revision_divergence_undetermined_without_control(monkeypatch):
    _router(monkeypatch, {("series/observations", "GDPC1"): _REVISED_MANY})
    out = []
    assert probe.check_revision_divergence(out) is None


def test_row_without_realtime_metadata_is_undetermined(monkeypatch):
    """A row that cannot be placed in time is not a usable observation."""
    _router(monkeypatch, {("series/observations", "GDPC1"): {
        "observations": [{"date": "2020-04-01", "value": "17302.5"}]}})
    out = []
    assert probe.check_revision_divergence(out) is None


# --------------------------------------------------------------- check 3
def _lag_payload(lag_days):
    from datetime import date, timedelta
    rows = []
    for d in probe.LAG_DATES:
        first = date.fromisoformat(d) + timedelta(days=lag_days)
        rows.append((first.isoformat(), "9999-12-31", d, "155000"))
    return rows


def test_release_lag_passes_with_real_publication_delay(monkeypatch):
    payloads = _lag_payload(34)
    _router(monkeypatch, {("series/observations", "PAYEMS"): _obs(payloads)})
    out = []
    assert probe.check_release_lag(out) is True


def test_release_lag_fails_when_vintage_equals_period(monkeypatch):
    """Zero lag is the period date wearing another name — the quietest
    lookahead in macro backtesting."""
    _router(monkeypatch, {
        ("series/observations", "PAYEMS"): _obs(_lag_payload(0))})
    out = []
    assert probe.check_release_lag(out) is False
    assert "FAIL" in "\n".join(out)


def test_release_lag_fails_when_no_vintage_dates(monkeypatch):
    _router(monkeypatch, {("series/observations", "PAYEMS"): {
        "observations": [{"date": d, "value": "1"} for d in probe.LAG_DATES]}})
    out = []
    assert probe.check_release_lag(out) is False


def test_release_lag_fails_when_endpoint_silent(monkeypatch):
    _router(monkeypatch, {})
    out = []
    assert probe.check_release_lag(out) is False


# --------------------------------------------------------------- hygiene
def test_no_network_is_reached_for_unstubbed_paths(monkeypatch):
    seen = []
    _router(monkeypatch, {("series/vintagedates", None): _vintages(3)}, seen)
    probe.check_vintages_exist([])
    assert seen and all(p.startswith("series/") for p, _ in seen)


def test_key_is_never_printed(monkeypatch, capsys):
    """The key must not reach stdout or stderr on ANY path, including the
    failure paths — a diagnosis that has to be redacted is the wrong
    diagnosis."""
    monkeypatch.setenv("FRED_API_KEY", "SUPERSECRETKEYVALUE")
    _router(monkeypatch, {})
    out = []
    probe.check_vintages_exist(out)
    probe.check_revision_divergence(out)
    probe.check_release_lag(out)
    captured = capsys.readouterr()
    blob = captured.out + captured.err + "\n".join(out)
    assert "SUPERSECRETKEYVALUE" not in blob


def test_missing_key_exits_rather_than_guessing(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    try:
        probe._key()
    except SystemExit as e:
        assert e.code == 2
    else:  # pragma: no cover
        raise AssertionError("missing key must not silently continue")
