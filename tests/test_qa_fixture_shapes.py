"""The QA fixture's hand-written records must still be readable by the REAL
production readers.

`scripts/qa_fixture.py` writes lessons/judgments/journal/posts as raw JSONL
with explicit timestamps instead of going through LessonStore, JudgmentStore
and journal.add_entry, because all three stamp `datetime.now()` on append and
that destroys byte-identical reruns. The cost of that choice is a second copy
of every record shape, free to drift from the code that reads it.

This file is the payment. It builds a fixture and replays it through the
actual readers, asserting the results are NON-DEGENERATE — not merely that
parsing succeeded. A fixture that parses and yields nothing is the exact
failure this is here to catch: before 2026-08-11 the generator touched these
four files empty, every read "worked", and every dashboard region that depends
on them rendered blank in every QA run anyone had ever done.
"""
import json
import os
import subprocess
import sys

import artifactcheck
import blog
import journal
import lessons as lessons_mod
import pytest
from judgments import (MIN_RESOLVED_FOR_SIGNAL, JudgmentStore, calibration_line,
                       calibration_metrics, confidence_calibration)
from lessons import LessonStore

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN = os.path.join(REPO, "scripts", "qa_fixture.py")
# Long enough for calibration_metrics' min_n=5 per bucket and for a second
# calendar month to exist; short enough to build in a test.
DAYS = 80
ANCHOR = "2026-08-11"


@pytest.fixture(scope="module")
def fixture_dir(tmp_path_factory):
    """Build once, via the CLI — so this exercises main()'s wiring (which file
    receives which rows, and that learnings.md is generated), not just the
    builder functions."""
    out = tmp_path_factory.mktemp("qafix") / "full"
    r = subprocess.run([sys.executable, GEN, "--out", str(out),
                        "--profile", "full", "--days", str(DAYS),
                        "--anchor", ANCHOR],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return str(out)


def _cfg_for(cfg, d):
    cfg["memory"]["ledger_path"] = os.path.join(d, "ledger.jsonl")
    cfg["memory"]["learnings_path"] = os.path.join(d, "learnings.md")
    cfg["learning"]["lessons_path"] = os.path.join(d, "lessons.jsonl")
    cfg["learning"]["judgments_path"] = os.path.join(d, "judgments.jsonl")
    cfg.setdefault("x_posting", {})["posts_log_path"] = os.path.join(
        d, "posts.jsonl")
    cfg["x_posting"]["journal_path"] = os.path.join(d, "journal.jsonl")
    return cfg


def _lines(path):
    with open(path) as f:
        return [line for line in f if line.strip()]


# ---- the lesson store ----------------------------------------------------

def test_every_lesson_lifecycle_state_is_represented(fixture_dir):
    states = LessonStore(os.path.join(fixture_dir, "lessons.jsonl")).replay()
    assert states, "replay produced no lessons at all"
    seen = {s["status"] for s in states.values()}
    assert seen == {"active", "candidate", "refuted", "retired"}, (
        f"fixture only reaches {sorted(seen)} — a lifecycle state with no "
        f"fixture is a lifecycle state no QA run can see")


def test_lessons_carry_evidence_on_both_sides(fixture_dir):
    states = LessonStore(os.path.join(fixture_dir, "lessons.jsonl")).replay()
    assert any(s["supports"] for s in states.values())
    assert any(s["contradicts"] for s in states.values()), (
        "no lesson has contradicting evidence — the refuted path is unproven")


def test_learnings_md_is_the_rendered_view_of_the_store(fixture_dir):
    """learnings.md is GENERATED, never hand-written (CLAUDE.md invariant 6).
    Re-rendering it from the store must reproduce it byte for byte."""
    path = os.path.join(fixture_dir, "learnings.md")
    original = open(path).read()
    states = LessonStore(os.path.join(fixture_dir, "lessons.jsonl")).replay()
    lessons_mod.render_markdown(states, path)
    assert open(path).read() == original
    assert "GENERATED from memory/lessons.jsonl" in original
    for section in ("## Active hypotheses", "## Candidates (unproven)",
                    "## Recently refuted / retired"):
        assert section in original
    assert original.count("(none)") == 0, (
        "a section rendered empty — the fixture does not populate every group")


# ---- the judgment store --------------------------------------------------

def test_calibration_is_measurable_not_just_parseable(fixture_dir):
    judgments = JudgmentStore(
        os.path.join(fixture_dir, "judgments.jsonl")).replay()
    m = calibration_metrics(judgments)
    assert m["n_resolved"] > 0
    assert m["veto_precision"] is not None, (
        f"only {m['n_vetoes_resolved']} resolved vetoes — below min_n, so "
        f"veto precision renders as nothing and the line cannot be checked")
    assert m["approve_accuracy"] is not None
    assert m["rails_block_precision"] is not None, (
        "rails rejections are bucketed separately from llm judgments; a "
        "fixture with no resolved rails leaves that bucket untested")


def test_the_calibration_line_says_something(fixture_dir):
    judgments = JudgmentStore(
        os.path.join(fixture_dir, "judgments.jsonl")).replay()
    line = calibration_line(calibration_metrics(judgments))
    assert "no resolved judgments yet" not in line
    assert "veto precision" in line and "approve accuracy" in line


def test_stated_confidence_buckets_are_populated(fixture_dir):
    """confidence is a FLOAT in judgments.jsonl and a coarse label in the
    ledger's llm_review. Getting that wrong yields empty buckets and a
    'no resolved trades carry a stated confidence' line that looks like a
    product state rather than a fixture bug."""
    judgments = JudgmentStore(
        os.path.join(fixture_dir, "judgments.jsonl")).replay()
    cal = confidence_calibration(judgments)
    assert cal, "no resolved executed judgment carried a stated confidence"
    assert sum(b["n"] for b in cal.values()) >= 5


def test_resolved_volume_clears_the_noise_threshold(fixture_dir):
    judgments = JudgmentStore(
        os.path.join(fixture_dir, "judgments.jsonl")).replay()
    m = calibration_metrics(judgments)
    assert m["n_resolved"] >= MIN_RESOLVED_FOR_SIGNAL, (
        "below 30 resolved the calibration line appends a 'treat as noise' "
        "note; the fixture should be able to render both sides of that")


# ---- the rendered artifacts ----------------------------------------------

def test_journal_renders_one_article_per_stored_entry(fixture_dir, cfg, tmp_path):
    cfg = _cfg_for(cfg, fixture_dir)
    out = journal.render(cfg, out_path=str(tmp_path / "journal.html"))
    stored = len(_lines(os.path.join(fixture_dir, "journal.jsonl")))
    assert stored > 0
    assert open(out).read().count("<article") == stored


def test_blog_renders_one_post_per_stored_post(fixture_dir, cfg, tmp_path):
    cfg = _cfg_for(cfg, fixture_dir)
    out = blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    stored = len(_lines(os.path.join(fixture_dir, "posts.jsonl")))
    assert stored > 0
    assert open(out).read().count("<div class=post>") == stored


def test_blog_shows_the_morning_read(fixture_dir, cfg, tmp_path):
    """market_context ledger events are what blog.py:70 groups by day. Without
    them the Morning read block never renders and is never tested."""
    cfg = _cfg_for(cfg, fixture_dir)
    out = blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    assert "Morning read:" in open(out).read()


def test_the_fixture_passes_the_publish_guard(fixture_dir, cfg, tmp_path):
    """The same fail-closed check that gates a real publish. Reusing it here
    means the fixture is held to the production bar instead of a QA-only one."""
    cfg = _cfg_for(cfg, fixture_dir)
    journal.render(cfg, out_path=str(tmp_path / "journal.html"))
    blog.render(cfg, out_path=str(tmp_path / "blog.html"))
    assert artifactcheck.problems(cfg, out_dir=str(tmp_path)) == []


# ---- sanitization --------------------------------------------------------

def test_no_real_identifier_reaches_a_fixture(fixture_dir):
    """config.yaml's journal_url_base carries the owner's GitHub handle. A
    fixture that inherited it would bake a personal identifier into every QA
    artifact and into any evidence attached to a bug report."""
    for name in os.listdir(fixture_dir):
        path = os.path.join(fixture_dir, name)
        if not os.path.isfile(path):
            continue
        body = open(path, errors="ignore").read()
        assert "connorshibley" not in body, f"{name} leaks the owner's handle"
        assert "github.io" not in body, f"{name} leaks the published host"


def test_every_generated_address_is_undeliverable(fixture_dir):
    import re
    for name in ("posts.jsonl", "journal.jsonl", "ledger.jsonl"):
        body = open(os.path.join(fixture_dir, name), errors="ignore").read()
        for addr in re.findall(r"[\w.%+-]+@[\w.-]+", body):
            assert addr.endswith("@example.invalid"), (
                f"{name} contains a routable-looking address: {addr}")


# ---- the hostile profile actually produces its hostile inputs -------------

def test_the_hostile_profile_emits_every_edge_case_it_claims():
    """A criterion that passes because the fixture never generated the input
    is worse than no criterion — it reads as coverage.

    The fragment-hostile trade id was originally keyed to `d == 1`, and no
    trade executed on day 1 of the hostile window, so the id never appeared,
    the journal-anchor criterion had nothing to test, and it passed.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "h")
        subprocess.run([sys.executable, GEN, "--out", out, "--profile",
                        "hostile", "--anchor", ANCHOR],
                       capture_output=True, text=True, check=True)
        ledger = open(os.path.join(out, "ledger.jsonl")).read()
        posts = open(os.path.join(out, "posts.jsonl")).read()
        journal = open(os.path.join(out, "journal.jsonl")).read()
        assert "t#0001 spaced" in ledger, "no fragment-hostile trade id"
        assert "t#0001 spaced" in journal, "hostile id never reached the journal"
        assert "javascript:" in posts, "no dangerous-scheme post link"
        assert "<img src=x onerror=" in ledger, "no markup-shaped field"
        assert "A" * 4000 in ledger, "no oversized field"


# ---- the guard -----------------------------------------------------------

def _roots():
    """Import the generator's own root resolution, so this test guards every
    checkout it claims to — including the main one when we run in a worktree."""
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import qa_fixture
    return qa_fixture._repo_roots()


def test_the_main_checkout_is_discovered_from_a_worktree():
    """The whole point of _repo_roots(). If this returns only the worktree
    while we ARE in a worktree, every guard below tests a directory nobody
    publishes from and the live mirror is unprotected — which is exactly how
    nine fixture files reached the live .site/ on 2026-08-11."""
    roots = _roots()
    assert REPO in [os.path.realpath(r) for r in roots]
    if os.path.isfile(os.path.join(REPO, ".git")):
        assert len(roots) == 2, (
            "running inside a worktree but only one root resolved — the main "
            "checkout, which holds the live memory/ and .site/, is unguarded")


@pytest.mark.parametrize("sub", ["memory", ".site", "publisher_data", ""])
def test_the_generator_refuses_to_write_into_any_live_tree(sub):
    """This checkout IS the deployment and publish.out_dir is '.', so
    'write the fixture next to the code' and 'overwrite the published
    dashboard' are the same command. Every root, not just the local one.

    This calls the guard FUNCTION rather than shelling out to the CLI, and the
    difference is not stylistic. The first version of this test ran
    `qa_fixture --out <root>` for real, so the moment a mutation disabled the
    guard the test itself wrote eight fixture files into the repo root — and
    it only spared the MAIN checkout because the assertion failed on the first
    root and never reached the second. A guard test that is destructive
    exactly when the guard is broken is a trap; assert on the exception and
    nothing can be written even in the failing case.
    """
    for root in _roots():
        dest = os.path.join(root, sub) if sub else root
        with pytest.raises(SystemExit) as e:
            _qa_fixture()._guard_not_live(dest)
        assert "REFUSING" in str(e.value)


def _qa_fixture():
    sys.path.insert(0, os.path.join(REPO, "scripts"))
    import qa_fixture
    return qa_fixture


def test_the_cli_refuses_too_not_just_the_function():
    """The guard is only worth having if it is on the path the CLI takes.

    Cleans up in a finally, because this is the one check that must really
    invoke the CLI and therefore really writes when the guard is broken — and
    memory/ is GITIGNORED, so the debris is invisible to `git status`. That is
    not a hypothetical either: it poisoned this file's own baseline for two
    mutation runs before anyone looked inside the directory.
    """
    import shutil
    dest = os.path.join(REPO, "memory", "qa-guard-probe")
    try:
        r = subprocess.run([sys.executable, GEN, "--out", dest, "--days", "1"],
                           capture_output=True, text=True)
        assert r.returncode != 0, "the CLI agreed to write inside memory/"
        assert "REFUSING" in r.stderr
        assert not os.path.exists(dest)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_the_generator_still_writes_somewhere_legitimate(tmp_path):
    """Negative control for the guard above: if the refusal were
    unconditional, every test in this file would pass for the wrong reason."""
    out = tmp_path / "ok"
    r = subprocess.run([sys.executable, GEN, "--out", str(out), "--days", "3"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(out / "ledger.jsonl")


# ---- determinism ---------------------------------------------------------

def test_same_seed_and_anchor_give_identical_records(tmp_path):
    """A bug found in a fixture is only reproducible if the fixture is."""
    outs = []
    for n in ("a", "b"):
        d = tmp_path / n
        r = subprocess.run([sys.executable, GEN, "--out", str(d), "--days", "9",
                            "--anchor", ANCHOR, "--seed", "1234"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        outs.append(d)
    for name in ("ledger.jsonl", "judgments.jsonl", "lessons.jsonl",
                 "journal.jsonl", "posts.jsonl", "learnings.md",
                 "spy_bars.json"):
        assert open(outs[0] / name).read() == open(outs[1] / name).read(), (
            f"{name} differs between two identical invocations")


def test_the_anchor_puts_the_newest_record_at_the_anchor(tmp_path):
    """The window ENDS at the anchor. Before this flag the fixture ended on a
    fixed 2026-07-10, so latest_position_mark was always >24h stale and the
    fresh branch of the mark-age note was unreachable."""
    d = tmp_path / "anchored"
    subprocess.run([sys.executable, GEN, "--out", str(d), "--days", "20",
                    "--anchor", ANCHOR], capture_output=True, text=True,
                   check=True)
    records = [json.loads(x) for x in _lines(d / "ledger.jsonl")]
    assert records[-1]["ts"][:10] <= ANCHOR
    assert records[-1]["ts"][:7] == ANCHOR[:7]
    marks = [r for r in records if r.get("event") == "positions_mark"]
    assert len(marks) > 1, "only one mark — no mark HISTORY to render"
