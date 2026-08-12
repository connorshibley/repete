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


# ---- content hashes (2026-08-06) ------------------------------------------

def test_manifest_carries_a_sha256_per_stream(tmp_path):
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    with tarfile.open(archive) as t:
        man = json.load(t.extractfile("backup_manifest.json"))
    assert set(man["sha256"]) == {"ledger.jsonl", "lessons.jsonl"}
    # and the recorded hash is the real one, not a placeholder
    assert man["sha256"]["ledger.jsonl"] == restore_drill.sha256_of(
        str(root / "memory" / "ledger.jsonl"))


def test_drill_catches_corruption_that_preserves_count_and_json(tmp_path):
    """The gap the hashes close.

    Counts and parseability both survive a byte flipped INSIDE a value, so
    every check the drill had before today passes on this archive. Only the
    content hash sees it. Run without live_memory so the prefix check cannot
    take the credit.
    """
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))

    def _flip(work):
        p = work / "memory" / "ledger.jsonl"
        p.write_text(p.read_text().replace('"event": "e"', '"event": "X"', 1))

    bad = _repack_with(archive, tmp_path, _flip)
    fails = restore_drill.drill(bad)                    # no live comparison
    assert any("content hash" in f for f in fails), fails
    # prove the OLD checks would have missed it
    assert not any("unparseable" in f or "truncated" in f for f in fails), fails


def test_an_archive_without_hashes_still_passes(tmp_path):
    """Archives written before 2026-08-06 have no sha256 block. Refusing them
    would discard the only backup history that exists."""
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))

    def _strip_hashes(work):
        p = work / "backup_manifest.json"
        doc = json.loads(p.read_text())
        doc.pop("sha256", None)
        p.write_text(json.dumps(doc))

    old = _repack_with(archive, tmp_path, _strip_hashes)
    assert restore_drill.drill(old, live_memory=str(root / "memory")) == []


# ---- the prefix property (2026-08-06) --------------------------------------

def test_drill_catches_a_rewritten_history(tmp_path):
    """Live must only ever have GROWN. A changed historical record means the
    append-only promise was broken — by corruption or by a hand-edit."""
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    p = root / "memory" / "ledger.jsonl"
    # same byte length, same record count, still valid JSON — only the
    # prefix test can see this
    p.write_text(p.read_text().replace('"event": "e"', '"event": "X"', 1))
    fails = restore_drill.drill(archive, live_memory=str(root / "memory"))
    assert any("not a prefix of live" in f for f in fails), fails


def test_live_growing_past_the_archive_is_not_a_failure(tmp_path):
    """The control. An archive is SUPPOSED to lag live; if this ever fails the
    prefix check has become a false alarm that would be switched off."""
    root = _fixture_root(tmp_path, n_records=5)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    with open(root / "memory" / "ledger.jsonl", "a") as f:
        for i in range(5, 9):
            f.write(json.dumps({"type": "event", "event": "e", "i": i}) + "\n")
    assert restore_drill.drill(archive, live_memory=str(root / "memory")) == []


# ---- the off-host mirror (2026-08-06) --------------------------------------

def test_backup_mirrors_off_host_byte_for_byte(tmp_path, monkeypatch):
    off = tmp_path / "off"
    monkeypatch.setenv("REPETE_OFFHOST_DIR", str(off))
    root = _fixture_root(tmp_path)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    mirror = off / os.path.basename(archive)
    assert mirror.exists(), "the archive never reached the off-host directory"
    assert (restore_drill.sha256_of(str(mirror))
            == restore_drill.sha256_of(archive))


def test_drill_fails_when_the_off_host_copy_is_missing(tmp_path, monkeypatch):
    """'The mirror silently stopped' is the failure this whole phase exists to
    make loud."""
    off = tmp_path / "off"
    monkeypatch.setenv("REPETE_OFFHOST_DIR", str(off))
    root = _fixture_root(tmp_path)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    os.remove(off / os.path.basename(archive))
    fails, _ = restore_drill.check_offhost(archive, str(off))
    assert any("off-host copy missing" in f for f in fails), fails


def test_drill_fails_when_the_off_host_copy_differs(tmp_path, monkeypatch):
    off = tmp_path / "off"
    monkeypatch.setenv("REPETE_OFFHOST_DIR", str(off))
    root = _fixture_root(tmp_path)
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    (off / os.path.basename(archive)).write_bytes(b"truncated")
    fails, _ = restore_drill.check_offhost(archive, str(off))
    assert any("differs from local" in f for f in fails), fails


def test_off_host_check_skips_loudly_when_none_resolves(tmp_path, monkeypatch):
    """On CI there is no iCloud. It must not fail — and it must not pass in
    silence either, which is the trap ci.yml already names."""
    monkeypatch.setenv("REPETE_OFFHOST_DIR", "")
    assert restore_drill.offhost_dir() is None
    fails, note = restore_drill.check_offhost("whatever.tar.gz", None)
    assert fails == []
    assert "SKIPPED" in note


def test_off_host_prunes_to_30_not_14(tmp_path, monkeypatch):
    """The durable copy keeps more history than the local one."""
    off = tmp_path / "off"
    off.mkdir()
    for i in range(40):
        (off / f"agent-backup-202601{i:02d}-000000.tar.gz").write_bytes(b"x")
    monkeypatch.setenv("REPETE_OFFHOST_DIR", str(off))
    root = _fixture_root(tmp_path)
    assert _run_backup(root).returncode == 0
    assert len([p for p in os.listdir(off) if p.endswith(".tar.gz")]) == 30


def test_archive_carries_no_appledouble_sidecars(tmp_path):
    """macOS tar writes `._ledger.jsonl` beside any file with an xattr. The
    drill's extractall(filter='data') drops them, but the runbook's manual
    restore uses system tar and would copy them into live memory/."""
    root = _fixture_root(tmp_path)
    target = str(root / "memory" / "ledger.jsonl")
    # Give the file a real extended attribute, or macOS tar has no reason to
    # emit a sidecar and the test would pass without proving anything.
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-w", "com.apple.test", "x", target],
                       check=False, capture_output=True)
    elif hasattr(os, "setxattr"):
        os.setxattr(target, "user.test", b"x")
    assert _run_backup(root).returncode == 0
    archive = restore_drill.newest_backup(str(root / "backups"))
    with tarfile.open(archive) as t:
        sidecars = [n for n in t.getnames()
                    if os.path.basename(n).startswith("._")]
    assert sidecars == [], sidecars


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
