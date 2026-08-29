"""The disposition meter, and the three ways it could flatter the judge.

WHY THIS EXISTS. `scripts/judge_disposition.py` answers one question: will the
current judge let anything through? That question was live because
`on_unavailable: block` had already produced a bot that could not enter a
trade, and a judge that vetoes everything produces the same bot for a different
reason — with a config that looks correct either way.

An instrument that reports a comfortable number when it has measured nothing is
worse than no instrument. These are the three ways this one could:

  1. counting a DEGRADED call as a non-veto, which understates the veto rate —
     the one number the caller is reading
  2. reporting 0.0 when nothing was judged, so "nothing got through" and
     "nothing vetoed" share a value
  3. comparing against constants pasted into the script, which keep printing
     as current after a refit moves them
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_disposition as jd  # noqa: E402


def _r(verdict, scale, degraded=None, seconds=1.0):
    return {"symbol": "AAPL", "strategy": "tsmom", "seconds": seconds,
            "degraded": degraded, "verdict": verdict, "scale": scale}


# --- 1. a degradation is not a verdict -------------------------------------

def test_degraded_calls_are_excluded_from_every_rate():
    """The failure this catches: nine degradations and one veto reported as a
    10% veto rate, which reads as a permissive judge when in fact no judge saw
    nine of the signals."""
    rows = [_r(None, None, degraded="api") for _ in range(9)] + [_r("veto", 0.0)]
    s = jd.summarize(rows)
    assert s["n_judged"] == 1
    assert s["n_degraded"] == 9
    assert s["veto_rate"] == 1.0


def test_degraded_calls_are_still_counted_somewhere():
    """Excluded from the rates, never dropped. A run that degraded half its
    calls must say so — the rates are then computed on a sample half the size
    the caller asked for."""
    rows = [_r("downsize", 0.5), _r(None, None, degraded="parse")]
    assert jd.summarize(rows)["n_degraded"] == 1


# --- 2. absent must never collapse to zero ---------------------------------

def test_no_successful_calls_reports_none_not_zero():
    """`veto_rate: 0.0` on an all-degraded run says the judge vetoed nothing.
    It saw nothing. Opposite findings must not share a value — the repo's
    standing rule, and the reason this returns None."""
    s = jd.summarize([_r(None, None, degraded="api") for _ in range(5)])
    assert s["n_judged"] == 0
    assert s["veto_rate"] is None
    assert s["downsize_rate"] is None
    assert s["mean_scale_non_veto"] is None
    assert s["expected_exposure"] is None


def test_an_absent_rate_never_renders_as_a_number():
    """The formatter is the last place the collapse can happen: None printed
    through a %.3f becomes 0.000 and the guard above buys nothing."""
    assert "0" not in jd._fmt(None)
    assert jd._fmt(None).strip() == "—"


def test_all_vetoes_reports_zero_exposure_not_none():
    """The mirror case, and it must NOT be None. Every signal judged and every
    one vetoed is a measurement: exposure really is zero."""
    s = jd.summarize([_r("veto", 0.0) for _ in range(4)])
    assert s["veto_rate"] == 1.0
    assert s["mean_scale_non_veto"] is None   # no non-veto trades to average
    assert s["expected_exposure"] is None


# --- 3. the reference must come from the fit -------------------------------

def test_the_reference_is_read_from_the_calibration_file():
    """Not pasted in. A constant keeps printing after a refit moves it, and a
    stale comparison reads as a current one."""
    ref = jd.load_reference(str(ROOT))
    with open(ROOT / "knowledge" / "judge_calibration.json") as f:
        cal = json.load(f)
    assert ref["veto_rate"] == cal["veto_rate"]
    assert ref["mean_scale"] == cal["mean_scale"]
    assert ref["fitted_context_version"] == cal["fitted_context_version"]


def test_the_script_hardcodes_no_reference_numbers():
    """Guards the same property at the source level: the fitted values must
    appear in the JSON and nowhere in the script."""
    src = (ROOT / "scripts" / "judge_disposition.py").read_text()
    with open(ROOT / "knowledge" / "judge_calibration.json") as f:
        cal = json.load(f)
    for key in ("veto_rate", "downsize_rate", "mean_scale"):
        assert f"{cal[key]:.3f}" not in src, (
            f"{key}'s fitted value is baked into the script — it will keep "
            f"printing as current after a refit")


def test_a_missing_calibration_is_none_not_a_crash():
    assert jd.load_reference("/nonexistent") is None


# --- the arithmetic --------------------------------------------------------

def test_exposure_is_survival_times_size():
    """Veto rate alone misses that a judge can be permissive and still deploy
    little. 2 vetoes + 2 downsizes at 0.5 => half survive at half size."""
    s = jd.summarize([_r("veto", 0.0), _r("veto", 0.0),
                      _r("downsize", 0.5), _r("downsize", 0.5)])
    assert s["mean_scale_non_veto"] == 0.5
    assert s["expected_exposure"] == 0.25


def test_an_out_of_set_verdict_is_surfaced_not_silently_binned():
    """llm.py rejects unknown verdicts upstream, but if one ever reaches here
    it must not vanish into a denominator it was never counted in."""
    s = jd.summarize([_r("approve", 1.0), _r("maybe", 0.5)])
    assert s["unknown_verdicts"] == {"maybe": 1}
    assert s["counts"]["approve"] == 1


def test_summarize_writes_nothing_to_its_input():
    rows = [_r("downsize", 0.5)]
    before = json.dumps(rows, sort_keys=True)
    jd.summarize(rows)
    assert json.dumps(rows, sort_keys=True) == before


# --- the judge is not deterministic ----------------------------------------
#
# ADDED AFTER THE FIRST RUN WAS REPORTED AS A RATE. Two passes over the
# IDENTICAL 20 signals on 2026-08-28 returned veto_rate 0.300 and 0.150 — one
# also produced the run's only `approve`. The gap between passes was larger
# than the gap the measurement existed to size, so a single pass reported as
# "the veto rate" is a draw dressed as a distribution.

def test_spread_reports_the_range_across_passes():
    passes = [{"veto_rate": 0.30}, {"veto_rate": 0.15}, {"veto_rate": 0.20}]
    sp = jd.spread(passes, "veto_rate")
    assert (sp["min"], sp["median"], sp["max"]) == (0.15, 0.20, 0.30)
    assert sp["n_passes"] == 3


def test_spread_drops_absent_passes_rather_than_counting_them_zero():
    """A pass where everything degraded contributes None. Folding it in as 0.0
    would drag the minimum to zero and invent a pass where the judge vetoed
    nothing — the same absent-vs-zero collapse, one level up."""
    sp = jd.spread([{"veto_rate": 0.30}, {"veto_rate": None}], "veto_rate")
    assert sp["min"] == 0.30
    assert sp["n_passes"] == 1


def test_spread_with_nothing_usable_is_none_not_zero():
    sp = jd.spread([{"veto_rate": None}], "veto_rate")
    assert sp == {"min": None, "median": None, "max": None, "n_passes": 0}
