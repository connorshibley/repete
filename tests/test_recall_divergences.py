"""Which divergences were open when a gate ran — answered, or refused.

`bot-divergence-check` exists because a verdict measures a bot that does not
exist while a divergence is open. But `docs/divergences.md` is a live document
and reading today's copy tells you nothing about what was open in July. So the
query resolves through git, and it reports what the register SAID rather than
what was true — the file documents itself having been wrong by nineteen days
about #15.

The refusal is the load-bearing part. s35, s37 and s38 ran on 2026-07-28; the
register was first committed on 2026-07-29. There is no answer for them, and
falling back to HEAD would manufacture one.
"""
import pytest

import recall


def test_it_resolves_a_revision_at_or_before_the_date():
    out = recall.divergences_as_of("2026-08-06")
    assert out["rev_committed"][:10] <= "2026-08-06", (
        "resolved a commit made AFTER the date asked about — the query would "
        "report a register the run could not have seen")


def test_a_date_before_the_register_existed_is_refused():
    with pytest.raises(recall.RecallError, match="no register existed"):
        recall.divergences_as_of("2026-07-28")


@pytest.mark.parametrize("spec_id", ["s35", "s37", "s38"])
def test_the_three_specs_that_predate_the_register_get_no_answer(spec_id):
    """Not today's table, not the earliest table. No answer."""
    assert recall.latest_verdicts()[spec_id]["ran_at"][:10] == "2026-07-28"
    with pytest.raises(recall.RecallError, match="no register existed"):
        recall.divergences_for(spec_id)


def test_it_uses_the_runs_full_timestamp_and_not_just_its_date():
    """s57a ran at 00:39 on 2026-08-10 and the PR carrying §57's own write-up
    merged later the same day. A date-only query would report a register that
    did not exist when the gate ran."""
    row = recall.latest_verdicts()["s57a"]
    precise = recall.divergences_for("s57a")
    day_only = recall.divergences_as_of(row["ran_at"][:10])
    assert precise["rev"] != day_only["rev"], (
        "the precise and day-rounded queries resolved the same commit — if "
        "this ever coincides, pick a different spec to pin the distinction")
    assert precise["rev_committed"] < row["ran_at"]


def test_the_quoted_table_is_contiguous_in_its_revision():
    """Taking every pipe line in the file would splice rows from the
    per-divergence deep-dives into a table that appears nowhere in the source —
    composition wearing a quotation's clothes."""
    out = recall.divergences_as_of("2026-08-10")
    blob = recall._git(["show", f"{out['rev']}:{recall.DIVERGENCES}"])
    assert out["table"] in blob
    assert out["open_line"] in blob


def test_the_master_table_is_the_one_quoted():
    out = recall.divergences_as_of("2026-08-10")
    rows = out["table"].splitlines()
    assert rows[0].startswith("| #"), f"not the master status table: {rows[0]}"
    assert any("Status" in rows[0] for _ in (0,))
    assert len(rows) >= 20, "the master table lost rows"


def test_the_open_line_is_quoted_not_recomputed():
    """The register's own count, whatever it says. Recomputing it here would
    make this module a second opinion about which divergences are open, and
    the file has been wrong about that before — which is the finding, not
    something to paper over."""
    out = recall.divergences_as_of("2026-08-10")
    assert "Open as of" in out["open_line"]
    blob = recall._git(["show", f"{out['rev']}:{recall.DIVERGENCES}"])
    assert out["open_line"] in blob


def test_the_refusal_names_when_the_register_actually_began():
    """TWO independent guards refuse a pre-register date — the constant, and an
    empty git result — so no single-line edit can make one answer wrongly. That
    is defence in depth and worth keeping, but it also means the constant needs
    its own test: this pins the FIRST guard, whose message tells a reader when
    the register began. The second guard only says no.
    """
    with pytest.raises(recall.RecallError, match="first committed 2026-07-29"):
        recall.divergences_as_of("2026-07-28")
    assert recall.DIVERGENCES_FIRST_COMMIT == "2026-07-29"
