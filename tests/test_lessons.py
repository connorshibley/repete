from datetime import datetime, timedelta, timezone

import pytest

import lessons
from lessons import LessonStore, apply_transitions


LCFG = {"promotion_min_supports": 3, "promotion_support_ratio": 2.0,
        "refutation_min_evidence": 3, "staleness_days": 45}

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return LessonStore(str(tmp_path / "lessons.jsonl"))


def _seed(store, supports=0, contradicts=0, hypothesis="stops fire fast on XLE"):
    lid = store.add_lesson(hypothesis, {"symbols": ["XLE"], "regime": "down/high"},
                           source="t1")
    for i in range(supports):
        store.add_evidence(lid, f"sup{i}", "supports", "matched")
    for i in range(contradicts):
        store.add_evidence(lid, f"con{i}", "contradicts", "opposed")
    return lid


# ---- store replay ----

def test_replay_round_trip(store):
    lid = _seed(store, supports=2, contradicts=1)
    s = store.replay()[lid]
    assert s["status"] == "candidate"
    assert s["supports"] == ["sup0", "sup1"] and s["contradicts"] == ["con0"]
    assert s["scope"]["symbols"] == ["XLE"]


def test_status_change_applied_on_replay(store):
    lid = _seed(store, supports=3)
    store.change_status(lid, "candidate", "active", "test")
    assert store.replay()[lid]["status"] == "active"


def test_evaluated_trade_cursor(store):
    store.mark_trade_evaluated("t9", 4)
    assert "t9" in store.evaluated_trade_ids()


# ---- lifecycle truth table ----

def test_promotion_at_three_clean_supports(store):
    lid = _seed(store, supports=3)
    trans = apply_transitions(store.replay(), NOW, LCFG)
    assert [(t[0], t[2]) for t in trans] == [(lid, "active")]


def test_no_promotion_when_ratio_fails(store):
    _seed(store, supports=3, contradicts=2)  # 3 < 2x2
    assert apply_transitions(store.replay(), NOW, LCFG) == []


def test_refutation_when_contradicts_dominate(store):
    lid = _seed(store, supports=1, contradicts=2)
    trans = apply_transitions(store.replay(), NOW, LCFG)
    assert [(t[0], t[2]) for t in trans] == [(lid, "refuted")]


def test_refutation_checked_before_promotion(store):
    # 3 supports would promote, but 3 contradicts refute — refutation wins
    lid = _seed(store, supports=3, contradicts=3)
    trans = apply_transitions(store.replay(), NOW, LCFG)
    assert [(t[0], t[2]) for t in trans] == [(lid, "refuted")]


def test_staleness_retires(store):
    lid = _seed(store)
    states = store.replay()
    # Pin the clock: store.append stamps REAL now, which made this test flip
    # whenever the wall clock crossed a UTC midnight near the 45d boundary.
    states[lid]["created_ts"] = NOW.isoformat()
    states[lid]["last_evidence_ts"] = NOW.isoformat()
    trans = apply_transitions(states, NOW + timedelta(days=46), LCFG)
    assert [(t[0], t[2]) for t in trans] == [(lid, "retired")]


def test_fresh_evidence_resets_staleness(store):
    lid = _seed(store)
    states = store.replay()
    states[lid]["last_evidence_ts"] = (NOW + timedelta(days=40)).isoformat()
    assert apply_transitions(states, NOW + timedelta(days=46), LCFG) == []


def test_terminal_states_never_transition(store):
    lid = _seed(store, supports=5)
    store.change_status(lid, "candidate", "refuted", "test")
    assert apply_transitions(store.replay(), NOW, LCFG) == []


# ---- rendered view + migration ----

def test_render_markdown_sections(store, tmp_path):
    lid = _seed(store, supports=3)
    store.change_status(lid, "candidate", "active", "test")
    _seed(store, hypothesis="a candidate idea")
    path = str(tmp_path / "learnings.md")
    lessons.render_markdown(store.replay(), path)
    text = open(path).read()
    assert "GENERATED from memory/lessons.jsonl" in text
    assert "## Active hypotheses" in text and "[ACTIVE n=3/0" in text
    assert "a candidate idea" in text


def test_parse_legacy_markdown(tmp_path):
    path = tmp_path / "learnings.md"
    path.write_text("# Learnings\n\n"
                    "- **2026-06-01** (trade ab12): possible pattern: n=1\n"
                    "- **2026-06-05**: unattributed note\n")
    parsed = lessons.parse_legacy_markdown(str(path))
    assert parsed[0] == {"date": "2026-06-01", "trade_id": "ab12",
                        "text": "possible pattern: n=1"}
    assert parsed[1]["trade_id"] == "migrated"


def test_parse_generated_file_yields_nothing(store, tmp_path):
    path = str(tmp_path / "learnings.md")
    _seed(store)
    lessons.render_markdown(store.replay(), path)
    assert lessons.parse_legacy_markdown(path) == []
