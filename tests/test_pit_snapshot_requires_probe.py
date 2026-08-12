"""§56. `data/pit/` is a CERTIFIED location, and this file is the certificate.

THE PROBLEM THIS SOLVES. `register_gate.freeze_violation` blocks `claim: EDGE`
by matching a path prefix — `data/snapshots/`. That is a proxy for
"survivor-selected", and it is a good proxy only because everything under that
directory is built by `index_constituents()` from CURRENT index membership.

Which means a snapshot written ANYWHERE ELSE clears the freeze with no override
and no argument. On its own that is a loophole: move a file, dodge a control.

So `data/pit/` is allowed to exist only while it is certified. Every file under
it must be covered by a committed probe record that says PASS, and every symbol
inside it must be one the probe actually examined. Delete the probe record, or
slip in a symbol nobody certified, and the suite goes red.

**Without this file the whole §56 path escape is dishonest.** It is the one
thing standing between `data/pit/` and "we moved the data so the rule stopped
applying", so it is mutation-proven along with everything else.
"""
from __future__ import annotations

import glob
import gzip
import json
import os

import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
PIT = os.path.join(ROOT, "data", "pit")
PROBES = os.path.join(ROOT, "research", "etf_probe_*.json")


def _records() -> list:
    out = []
    for path in sorted(glob.glob(PROBES)):
        with open(path) as f:
            out.append((os.path.basename(path), json.load(f)))
    return out


def _snapshots() -> list:
    if not os.path.isdir(PIT):
        return []
    return sorted(p for p in glob.glob(os.path.join(PIT, "*.json.gz")))


def certified_from(records: list) -> set:
    """Symbols examined by a probe that PASSED.

    Takes the records rather than reading disk, so the accumulation rule can be
    tested against a FAILED record — which is not otherwise possible while the
    only committed record is a passing one, and an untestable rule here is what
    the whole file exists to prevent.

    Only `universe` counts. The negative controls were examined precisely
    because they are NOT fit to trade, and folding them in would certify SBNY.
    """
    out: set = set()
    for _, rec in records:
        if rec.get("verdict") == "PASS":
            out |= {t["ticker"] for t in rec.get("universe", [])}
    return out


def _certified_symbols() -> set:
    return certified_from(_records())


# ---- the certification itself ----------------------------------------------

def test_a_pit_snapshot_requires_a_passing_probe_record():
    """The load-bearing assertion. If `data/pit/` holds data and no committed
    probe says PASS, the EDGE freeze has been dodged rather than satisfied."""
    snaps = _snapshots()
    if not snaps:
        pytest.skip("data/pit/ is empty — nothing has claimed certification")
    passing = [name for name, r in _records() if r.get("verdict") == "PASS"]
    assert passing, (
        f"{len(snaps)} snapshot(s) live in data/pit/, which is outside the §52 "
        f"EDGE freeze, and NO committed probe record reports PASS. That is a "
        f"path escape, not a certification. Run "
        f"scripts/probe_etf_universe_survivorship.py and commit its --json "
        f"output to research/, or move the data back under data/snapshots/.")


@pytest.mark.parametrize("snap", _snapshots(), ids=os.path.basename)
def test_every_symbol_in_a_pit_snapshot_was_certified(snap):
    """The teeth. A PASS verdict certifies a NAMED universe, not the directory.
    Adding an uncertified symbol to a certified file would launder it."""
    certified = _certified_symbols()
    with gzip.open(snap) as f:
        symbols = set(json.load(f))
    uncertified = sorted(symbols - certified)
    assert not uncertified, (
        f"{os.path.basename(snap)} contains {uncertified}, which no passing "
        f"probe examined. A certificate covers the universe it measured; it "
        f"does not cover whatever is later dropped into the same folder.")


@pytest.mark.parametrize("snap", _snapshots(), ids=os.path.basename)
def test_every_pit_snapshot_is_in_its_manifest_with_a_matching_hash(snap):
    """Same guarantee `data/snapshots/` gets: a gate that names this file gets
    the bytes the registration named."""
    import hashlib
    with open(os.path.join(PIT, "MANIFEST.json")) as f:
        man = json.load(f)["snapshots"]
    name = os.path.basename(snap)
    assert name in man, f"{name} is not recorded in data/pit/MANIFEST.json"
    h = hashlib.sha256()
    with open(snap, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == man[name]["sha256"], f"{name} has drifted"
    assert "adjust" in man[name]["source"].lower(), (
        f"{name}'s manifest entry does not declare its price adjustment")


# ---- the certificate must itself be readable and honest ---------------------

def test_a_passing_probe_record_carries_both_checks_and_a_universe():
    for name, rec in _records():
        if rec.get("verdict") != "PASS":
            continue
        assert rec["check1_continuous_from_inception"] is True, name
        assert rec["check2_detects_the_dead"] is True, name
        assert rec["universe"], f"{name} passed with an empty universe"
        assert rec["closed"], (
            f"{name} passed with no negative control — a probe that never "
            f"tried to detect a dead symbol has not shown it can")
        assert rec["control"]["rows"] > 0, name


def test_the_negative_controls_are_not_treated_as_certified():
    """SBNY is in every probe record. It must never end up in `certified`, or
    the guard would happily wave through a snapshot containing a seized bank."""
    certified = _certified_symbols()
    for _, rec in _records():
        for t in rec.get("closed", []):
            assert t["ticker"] not in certified, (
                f"{t['ticker']} is a negative control and must never be "
                f"certified for trading")


@pytest.mark.parametrize("verdict", ["REFUSED", "UNDETERMINED", None])
def test_a_probe_that_did_not_pass_certifies_nothing(verdict):
    """Only PASS confers certification.

    Driven with a synthetic record because the committed one passes — and the
    earlier version of this test, which looped over the real records, asserted
    NOTHING for exactly that reason. A check that cannot fail in the very file
    whose subject is checks that cannot fail.
    """
    rec = {"verdict": verdict,
           "universe": [{"ticker": "ZZZZ"}, {"ticker": "YYYY"}],
           "closed": []}
    assert certified_from([("synthetic.json", rec)]) == set()


def test_a_passing_probe_certifies_exactly_its_universe():
    """The positive half — otherwise the test above would pass on a function
    that certified nothing, ever."""
    rec = {"verdict": "PASS",
           "universe": [{"ticker": "ZZZZ"}, {"ticker": "YYYY"}],
           "closed": [{"ticker": "DEAD"}]}
    assert certified_from([("synthetic.json", rec)]) == {"ZZZZ", "YYYY"}
