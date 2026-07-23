"""Phase D: backup + restore drill — full round-trip on a tmp fixture."""
import json
import os
import subprocess
import sys
import tarfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import restore_drill


def _fixture_root(tmp_path, n_records=5):
    mem = tmp_path / "memory"
    mem.mkdir()
    with open(mem / "ledger.jsonl", "w") as f:
        for i in range(n_records):
            f.write(json.dumps({"type": "event", "event": "e", "i": i}) + "\n")
    with open(mem / "lessons.jsonl", "w") as f:
        f.write(json.dumps({"kind": "lesson"}) + "\n")
    (tmp_path / "config.yaml").write_text("mode: paper\n")
    return tmp_path


def _run_backup(root, dest="backups"):
    return subprocess.run(
        ["sh", os.path.join(ROOT, "scripts", "backup.sh"), dest],
        env={**os.environ, "AGENT_ROOT": str(root)},
        capture_output=True, text=True)


def test_backup_then_drill_round_trip(tmp_path):
    root = _fixture_root(tmp_path)
    r = _run_backup(root)
    assert r.returncode == 0, r.stderr
    archive = restore_drill.newest_backup(str(root / "backups"))
    assert archive is not None
    fails = restore_drill.drill(archive, live_memory=str(root / "memory"))
    assert fails == []


def test_drill_fails_on_corrupted_stream(tmp_path):
    root = _fixture_root(tmp_path)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    # Corrupt the ledger INSIDE the archive: rebuild it with a mangled copy.
    extract = tmp_path / "mangle"
    with tarfile.open(archive) as tf:
        tf.extractall(extract, filter="data")
    with open(extract / "memory" / "ledger.jsonl", "a") as f:
        f.write('{"type": "event", "trunc\n')
    with tarfile.open(archive, "w:gz") as tf:
        for name in ("memory", "config.yaml"):
            tf.add(extract / name, arcname=name)
    fails = restore_drill.drill(archive)
    assert any("unparseable" in f or "tail" in f for f in fails)


def test_drill_fails_when_archive_larger_than_live(tmp_path):
    """An archive with MORE records than live = restoring the wrong data."""
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    with open(root / "memory" / "ledger.jsonl", "w") as f:   # live shrinks
        f.write(json.dumps({"type": "event"}) + "\n")
    fails = restore_drill.drill(archive, live_memory=str(root / "memory"))
    assert any("wrong source" in f for f in fails)


def test_drill_fails_with_no_backups(tmp_path, capsys):
    sys.argv = ["restore_drill.py", str(tmp_path / "empty")]
    assert restore_drill.main() == 1
    assert "no backups" in capsys.readouterr().out


def test_backup_prunes_to_14(tmp_path):
    root = _fixture_root(tmp_path)
    dest = root / "backups"
    dest.mkdir()
    for i in range(20):                     # fake 20 older archives
        (dest / f"agent-backup-202601{i:02d}-000000.tar.gz").write_bytes(b"x")
    r = _run_backup(root)
    assert r.returncode == 0, r.stderr
    left = [p for p in os.listdir(dest) if p.endswith(".tar.gz")]
    assert len(left) == 14


def _repack_with(archive, tmp_path, mutate):
    """Extract an archive, let `mutate(extract_dir)` change it, repack."""
    import shutil
    work = tmp_path / "work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    with tarfile.open(archive) as t:
        t.extractall(work)
    mutate(work)
    out = str(tmp_path / "backups" / "agent-backup-99999999-999999.tar.gz")
    with tarfile.open(out, "w:gz") as t:
        for item in sorted(os.listdir(work)):
            t.add(os.path.join(work, item), arcname=item)
    return out


def test_backup_writes_a_manifest(tmp_path):
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    with tarfile.open(archive) as t:
        names = t.getnames()
        assert "backup_manifest.json" in names
        man = json.load(t.extractfile("backup_manifest.json"))
    assert man["streams"]["ledger.jsonl"] == 5
    assert man["streams"]["lessons.jsonl"] == 1


def test_drill_fails_on_cleanly_truncated_archive(tmp_path):
    """The case the old drill PASSED: every remaining line is valid JSON, the
    archive simply has fewer records than it should. Only the manifest catches
    it — counting against live cannot, since an archive is legitimately behind."""
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))

    def _truncate(work):
        p = work / "memory" / "ledger.jsonl"
        lines = p.read_text().splitlines()
        p.write_text("\n".join(lines[:2]) + "\n")      # 5 -> 2, all still valid

    bad = _repack_with(archive, tmp_path, _truncate)
    fails = restore_drill.drill(bad, live_memory=str(root / "memory"))
    assert any("truncated" in f for f in fails), fails


def test_drill_accepts_sqlite_only_archive(tmp_path):
    """A sqlite deployment has no .jsonl streams — that must not be a failure."""
    import sqlite3
    root = tmp_path / "sq"
    (root / "memory").mkdir(parents=True)
    (root / "config.yaml").write_text("mode: paper\n")
    db = root / "memory" / "agent.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE events (id INTEGER PRIMARY KEY, "
                       "stream TEXT NOT NULL, data TEXT NOT NULL);")
    conn.execute("INSERT INTO events (stream, data) VALUES ('ledger', '{}')")
    conn.commit(); conn.close()
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    fails = restore_drill.drill(archive, live_memory=str(root / "memory"))
    assert fails == [], fails
