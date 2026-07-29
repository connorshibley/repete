"""§24 — scan-order rotation (risk.rotate_scan_order / risk.scan_order).

Contention for slots is first-come by scan position, so the scan list decided
allocation — and it was the order symbols happened to be typed into config.yaml.
SPY/QQQ/DIA/IWM (positions 0-3 of 38) won nearly every contested slot; the live
book confirmed it exactly.

The properties that make the fix safe are pinned here: it is DETERMINISTIC (gate
re-runs must reproduce), it gives EVEN coverage, it preserves relative order, and
it is the SAME helper live and both simulators call — a per-call-site
re-implementation would be the fifth sim/live divergence.
"""
from datetime import date, datetime, timezone


import risk

SYMS = ["SPY", "QQQ", "DIA", "IWM", "XLK"]


def cfg_on(**extra):
    return {"risk": {"scan_rotation": True, **extra}}


def cfg_off():
    return {"risk": {}}


# ---------------- the rotation itself ----------------

def test_rotation_offsets_by_one_per_calendar_day():
    """One symbol per day: the mechanism that makes coverage even."""
    d1 = date(2026, 7, 24)
    d2 = date(2026, 7, 25)
    a = risk.rotate_scan_order(SYMS, d1)
    b = risk.rotate_scan_order(SYMS, d2)
    assert b == a[1:] + a[:1]


def test_relative_order_is_preserved():
    """Only the starting point moves — this is a rotation, not a shuffle."""
    out = risk.rotate_scan_order(SYMS, date(2026, 7, 24))
    doubled = SYMS + SYMS
    assert any(doubled[i:i + len(SYMS)] == out for i in range(len(SYMS)))


def test_is_a_permutation_no_symbol_lost_or_duplicated():
    out = risk.rotate_scan_order(SYMS, date(2026, 3, 3))
    assert sorted(out) == sorted(SYMS)
    assert len(out) == len(SYMS)


def test_deterministic_same_day_same_answer():
    """Gate re-runs must reproduce. Randomness here would be a defect."""
    d = date(2026, 7, 24)
    assert risk.rotate_scan_order(SYMS, d) == risk.rotate_scan_order(SYMS, d)


def test_every_symbol_leads_equally_often_over_a_full_cycle():
    """Even coverage is the whole point — no symbol may be structurally
    favoured the way config position previously favoured SPY."""
    leaders = [risk.rotate_scan_order(SYMS, date.fromordinal(o))[0]
               for o in range(date(2026, 1, 1).toordinal(),
                              date(2026, 1, 1).toordinal() + len(SYMS) * 4)]
    counts = {s: leaders.count(s) for s in SYMS}
    assert set(counts) == set(SYMS), "a symbol never led"
    assert max(counts.values()) == min(counts.values()) == 4


def test_no_year_boundary_discontinuity():
    """toordinal, not day-of-year: Dec 31 -> Jan 1 must advance by exactly one,
    not jump from 365 back to 1."""
    a = risk.rotate_scan_order(SYMS, date(2026, 12, 31))
    b = risk.rotate_scan_order(SYMS, date(2027, 1, 1))
    assert b == a[1:] + a[:1]


# ---------------- input handling ----------------

def test_accepts_iso_string_and_datetime():
    d = date(2026, 7, 24)
    expected = risk.rotate_scan_order(SYMS, d)
    assert risk.rotate_scan_order(SYMS, "2026-07-24T21:00:00+00:00") == expected
    assert risk.rotate_scan_order(SYMS, "2026-07-24") == expected
    assert risk.rotate_scan_order(
        SYMS, datetime(2026, 7, 24, 21, 0, tzinfo=timezone.utc)) == expected


def test_unparseable_date_fails_open_unchanged():
    """A bad timestamp must not silently reshuffle the universe."""
    assert risk.rotate_scan_order(SYMS, "not-a-date") == SYMS


def test_degenerate_lists_are_safe():
    assert risk.rotate_scan_order([], date(2026, 7, 24)) == []
    assert risk.rotate_scan_order(["SPY"], date(2026, 7, 24)) == ["SPY"]


def test_does_not_mutate_the_caller_list():
    original = list(SYMS)
    risk.rotate_scan_order(SYMS, date(2026, 7, 24))
    assert SYMS == original


# ---------------- the config gate ----------------

def test_disabled_returns_the_list_untouched():
    """Any config that has not opted in must behave exactly as before."""
    assert risk.scan_order(SYMS, cfg_off(), date(2026, 7, 24)) == SYMS


def test_enabled_rotates():
    out = risk.scan_order(SYMS, cfg_on(), date(2026, 7, 24))
    assert out != SYMS
    assert sorted(out) == sorted(SYMS)


def test_shipped_config_has_rotation_on():
    """§24 is adopted — if this flips off, the SPY/QQQ bias silently returns."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    assert cfg["risk"]["scan_rotation"] is True


def test_shipped_config_symbols_still_all_get_a_turn():
    """Sanity: with the real 38-name universe, every symbol leads within one
    full rotation period."""
    import yaml
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    syms = cfg["symbols"]
    start = date(2026, 1, 1).toordinal()
    leaders = {risk.scan_order(syms, cfg, date.fromordinal(start + i))[0]
               for i in range(len(syms))}
    assert leaders == set(syms), "not every symbol leads within one cycle"
