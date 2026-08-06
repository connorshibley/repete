"""Log rotation must bound the directory WITHOUT losing the writers.

The design under test is `copytruncate`, and the reason it was chosen over
`RotatingFileHandler` is the whole point of this file:

`logs/agent.log` is written by FOUR handlers in FOUR separate processes
(`main.py:79`, `watchdog.py:64`, `daily_posts.py:48`, `backfill_posts.py:170`),
and `flatten_retry` runs every 15 minutes around the clock, so overlap is
routine. Rename-based rotation moves the file out from under the other
processes' open descriptors and they go on writing into an orphaned inode —
rotation would silently start LOSING logs.

`test_a_process_holding_the_file_open_keeps_writing_to_it` is the test that
distinguishes the two designs. It fails under a rename and passes under
copytruncate, and nothing else here does that.
"""
import os
import subprocess

ROOT = os.path.join(os.path.dirname(__file__), "..")
SCRIPT = os.path.join(ROOT, "scripts", "rotate_logs.sh")


def _logs(tmp_path, **files):
    d = tmp_path / "logs"
    d.mkdir(exist_ok=True)
    for name, size in files.items():
        (d / name).write_text("x" * size)
    return d


def _rotate(tmp_path, max_bytes=1000, keep=3):
    return subprocess.run(
        ["sh", SCRIPT],
        env={**os.environ,
             "AGENT_ROOT": str(tmp_path),
             "REPETE_LOG_MAX_BYTES": str(max_bytes),
             "REPETE_LOG_KEEP": str(keep)},
        capture_output=True, text=True)


def test_a_file_over_the_threshold_is_copied_aside_and_truncated(tmp_path):
    d = _logs(tmp_path, **{"agent.log": 3000})
    r = _rotate(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (d / "agent.log.1").read_text() == "x" * 3000
    assert (d / "agent.log").read_text() == ""


def test_the_inode_survives_rotation(tmp_path):
    """The property the entire design rests on.

    A rename gives the live path a NEW inode; copytruncate keeps the old one.
    Every process holding an append descriptor is bound to the inode, not the
    name, so this is what decides whether their writes still land.
    """
    d = _logs(tmp_path, **{"agent.log": 3000})
    before = os.stat(d / "agent.log").st_ino
    assert _rotate(tmp_path).returncode == 0
    assert os.stat(d / "agent.log").st_ino == before


def test_a_process_holding_the_file_open_keeps_writing_to_it(tmp_path):
    """The test that rejects RotatingFileHandler.

    Simulates the watchdog holding `logs/agent.log` open across a rotation the
    cycle triggered. Under a rename the line below lands in a file nobody will
    ever read; under copytruncate it lands in the live log.
    """
    d = _logs(tmp_path, **{"agent.log": 3000})
    holder = open(d / "agent.log", "a")
    try:
        assert _rotate(tmp_path).returncode == 0
        holder.write("written-after-rotation\n")
        holder.flush()
    finally:
        holder.close()
    assert "written-after-rotation" in (d / "agent.log").read_text()
    assert "written-after-rotation" not in (d / "agent.log.1").read_text()


def test_it_covers_the_files_python_cannot_reach(tmp_path):
    """`cron.log` is written by 13 shell `>>` redirects and `launchd.err.log`
    by `StandardErrorPath` in 11 plists. No Python handler touches either, so a
    handler-side fix would have left the largest file unbounded."""
    d = _logs(tmp_path, **{"cron.log": 3000, "launchd.err.log": 3000})
    assert _rotate(tmp_path).returncode == 0
    assert (d / "cron.log.1").exists()
    assert (d / "launchd.err.log.1").exists()
    assert (d / "cron.log").read_text() == ""


def test_a_file_under_the_threshold_is_untouched(tmp_path):
    d = _logs(tmp_path, **{"agent.log": 100})
    assert _rotate(tmp_path).returncode == 0
    assert (d / "agent.log").read_text() == "x" * 100
    assert not (d / "agent.log.1").exists()


def test_generations_age_off_and_prune_to_keep(tmp_path):
    d = _logs(tmp_path, **{"agent.log": 3000})
    for i in (1, 2, 3):
        (d / f"agent.log.{i}").write_text(f"gen{i}")
    assert _rotate(tmp_path, keep=3).returncode == 0
    # the fresh copy becomes .1; the old .1 and .2 shift up; .3 falls off
    assert (d / "agent.log.1").read_text() == "x" * 3000
    assert (d / "agent.log.2").read_text() == "gen1"
    assert (d / "agent.log.3").read_text() == "gen2"
    assert not (d / "agent.log.4").exists()


def test_a_rotated_file_is_never_itself_rotated(tmp_path):
    """Otherwise `agent.log.1` grows an `agent.log.1.1` every day and the
    directory expands instead of being bounded.

    The property comes from the GLOBS (`*.log`, `*.jsonl`), which `agent.log.1`
    does not match — not from a separate guard. An explicit `case` guard was
    written first and a mutation showed it was dead code, so it was deleted.
    This test is what would fail if the globs were ever widened.
    """
    d = _logs(tmp_path, **{"agent.log": 100})
    (d / "agent.log.1").write_text("y" * 9000)
    assert _rotate(tmp_path).returncode == 0
    assert not (d / "agent.log.1.1").exists()
    assert (d / "agent.log.1").read_text() == "y" * 9000


def test_nothing_to_do_still_exits_zero(tmp_path):
    """`run_rotate_logs.sh` alerts on a non-zero exit. A quiet day must not
    page anyone — and under `set -eu` that is easy to get wrong."""
    _logs(tmp_path, **{"agent.log": 10})
    r = _rotate(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "nothing over" in r.stdout


def test_a_missing_log_dir_is_not_an_error(tmp_path):
    r = _rotate(tmp_path)
    assert r.returncode == 0, r.stderr
