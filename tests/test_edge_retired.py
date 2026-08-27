"""EDGE is retired on every venue, not only the two that were spent — §77.

WHAT CHANGED AND WHY IT MATTERS

§52 froze `data/snapshots/` and §57 froze `data/pit/`. Both were EXHAUSTION
arguments: this dataset has given what it can. Both were escapable the same
way — point a spec at a directory neither prefix matched and `freeze_violation`
returned None.

So the licence to claim an edge depended on what data had been BOUGHT rather
than on what had been SHOWN. Buying a survivorship-free dataset would have
reopened EDGE without anyone deciding to reopen it. That makes the freeze a
statement about inventory.

§77 is a decision instead: the programme stops making edge claims. 16
registrations, 2 passes, and §51 measured +200.28pp of survivorship inflation
on the first — enough to explain it outright — while §75 re-measured the second
with the judge on and one period of four survived. §34 found the selection
procedure itself scores no better than random, and §76's decay monitor returned
INDISTINGUISHABLE_FROM_RANDOM on the live record.

WHAT IS DELIBERATELY UNCHANGED

The override. `--override-freeze` still lifts this, exactly as it lifts §52 and
§57, and that is the point: this repo's stated position is that a wall gets
climbed while a speed bump with an audit trail gets recorded. Retirement should
be reversible by a deliberate, argued act — not by choosing a new folder.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import register_gate as rg  # noqa: E402


def _spec(path, claim="EDGE"):
    return {"claim": claim, "snapshot": {"path": path}}


# ---- the hole §77 closes -------------------------------------------------

@pytest.mark.parametrize("path", [
    "data/norgate/2026.json",      # the licence that was nearly bought
    "data/newvendor/x.json",
    "data/pitfall/y.json",         # near-miss on the `data/pit` prefix
    "snapshots/x.json",            # no `data/` prefix
    "",                            # no snapshot at all
])
def test_edge_is_refused_on_a_venue_neither_freeze_names(path):
    """THE POINT. Before §77 every one of these registered cleanly, so the
    freeze was a claim about inventory rather than about the programme."""
    v = rg.freeze_violation(_spec(path))
    assert v is not None, (
        f"an EDGE claim on {path!r} registered — buying data would reopen "
        f"EDGE without anyone deciding to")
    assert "RETIRED" in v


def test_the_refusal_says_what_would_lift_it():
    """A refusal that does not name its own exit is a wall, and this repo's
    position is that walls get climbed while speed bumps get recorded."""
    v = rg.freeze_violation(_spec("data/norgate/2026.json"))
    assert "--override-freeze" in v
    assert "argued before" in v, "the exit must require an argument, not a flag"


def test_the_refusal_states_the_evidence_not_just_the_verdict():
    """A reader hitting this in six months needs to know why, not merely
    that. The numbers are what make it arguable-with."""
    v = rg.freeze_violation(_spec("data/norgate/2026.json"))
    for token in ("16 EDGE registrations", "200.28pp", "§75", "§34", "§76"):
        assert token in v, f"the refusal no longer cites {token}"


# ---- what must NOT have changed ------------------------------------------

def test_the_two_venue_freezes_keep_their_own_messages():
    """`test_edge_freeze.py` asserts on this exact text, and §52's reason
    docstring says outright: NOT to be reworded — a control whose message
    drifts is a control nobody trusts. The catch-all must sit BEHIND them."""
    assert "FROZEN (§52)" in rg.freeze_violation(_spec("data/snapshots/a.json"))
    assert "FROZEN (§57)" in rg.freeze_violation(_spec("data/pit/b.json"))


@pytest.mark.parametrize("claim", ["DIAGNOSTIC", "METHOD", "CAPACITY"])
def test_the_claim_types_the_apparatus_runs_on_stay_open(claim):
    """Retiring EDGE must not retire the programme. These three describe how
    the machinery behaves, which is what is being built now."""
    assert rg.freeze_violation(_spec("data/norgate/2026.json", claim)) is None
    assert rg.freeze_violation(_spec("data/snapshots/a.json", claim)) is None


def test_only_EDGE_is_frozen():
    """If FROZEN_CLAIMS ever grows, this test is where that gets noticed."""
    assert rg.FROZEN_CLAIMS == ("EDGE",)
