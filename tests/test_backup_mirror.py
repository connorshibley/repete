"""scripts/backup.sh's off-host mirror, exercised as the shell script it is.

THIS FILE EXISTS BECAUSE IT DIDN'T. On 2026-08-23 a mutation was run with
`--expect tests/test_backup_mirror.py` against this very logic and the harness
printed "CAUGHT — went red, as required". The file did not exist. pytest exits
non-zero for a missing path (4) exactly as it does for a failing test (1), and
mutate.py only checked `!= 0`, so a typo produced a proof of nothing phrased as
a proof. The harness is fixed (it now runs --expect on the UNMUTATED code
first, and requires exit 1 specifically); this is the test that should have
been there.

The mirror is shell, so it is tested as shell: real `backup.sh`, with `rclone`
and `gpg` stubbed on PATH so nothing leaves the machine and no bucket is
needed. What is under test is the DECISION LOGIC — when it refuses, when it
verifies, and when it is allowed to write a receipt.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BACKUP = REPO / "scripts" / "backup.sh"

RCLONE = """#!/bin/sh
# stub rclone. Behaviour is driven by files the test drops in $STUB_DIR.
case "$1" in
  copyto)   [ -f "$STUB_DIR/upload_fails" ] && exit 1
            echo "$2" > "$STUB_DIR/last_src"
            echo "$3" >> "$STUB_DIR/uploaded"; exit 0 ;;
  hashsum)  if   [ -f "$STUB_DIR/hash_empty" ];    then printf ''; exit 0
            elif [ -f "$STUB_DIR/hash_wrong" ];    then echo "0000000000000000000000000000000000000000  x"; exit 0
            else src=$(cat "$STUB_DIR/last_src")
                 if command -v sha1sum >/dev/null 2>&1; then
                   sha1sum "$src" | sed "s/ .*/  x/"
                 else
                   shasum -a 1 "$src" | sed "s/ .*/  x/"
                 fi
                 exit 0
            fi ;;
  lsf)      exit 0 ;;
  deletefile) exit 0 ;;
esac
exit 0
"""

# gpg stub: copies the plaintext to --output so the pipeline stays inspectable,
# and records that it was asked to encrypt at all.
GPG = """#!/bin/sh
out=""; src=""
while [ $# -gt 0 ]; do
  case "$1" in
    --output) out="$2"; shift 2 ;;
    --*) shift ;;
    *) src="$1"; shift ;;
  esac
done
cat "$src" > "$out"
echo "encrypted $src" >> "$STUB_DIR/gpg_calls"
exit 0
"""


@pytest.fixture
def agent(tmp_path):
    """A minimal AGENT_ROOT the real script will accept."""
    root = tmp_path / "agent"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "ledger.jsonl").write_text('{"type":"decision"}\n')
    (root / "config.yaml").write_text("mode: paper\n")

    stub = tmp_path / "stub"
    stub.mkdir()
    binp = tmp_path / "bin"
    binp.mkdir()
    for name, body in (("rclone", RCLONE), ("gpg", GPG)):
        f = binp / name
        f.write_text(body)
        f.chmod(0o755)

    env = dict(os.environ)
    env.update({
        "AGENT_ROOT": str(root),
        "STUB_DIR": str(stub),
        "PATH": f"{binp}:{env['PATH']}",
        "REPETE_OFFHOST_DIR": "",          # filesystem branch off; remote only
        "HOME": str(tmp_path),             # never touch real iCloud
    })
    return root, stub, env


def _run(agent, **over):
    root, stub, env = agent
    env = {**env, **over}
    return subprocess.run(["sh", str(BACKUP)], cwd=str(root), env=env,
                          capture_output=True, text=True)


def _receipt(root):
    p = root / "memory" / "offhost_mirror.json"
    return json.loads(p.read_text()) if p.exists() else None


def test_a_remote_without_a_passphrase_refuses_rather_than_uploading_plaintext(agent):
    """The ledger and every strategy parameter are in that archive. Forgetting
    one env var must not be the difference between encrypted and not."""
    root, stub, _ = agent
    r = _run(agent, REPETE_MIRROR_REMOTE="b2:bucket")
    assert r.returncode != 0
    assert "REPETE_BACKUP_PASSPHRASE" in r.stderr
    assert not (stub / "uploaded").exists(), "it uploaded anyway"
    assert _receipt(root) is None


def test_an_empty_remote_hash_is_a_failure_not_a_pass(agent):
    """THE ONE THAT MATTERS. 'I could not verify' must never read as 'verified'
    — that is the shape of every silent-success bug in this repo."""
    root, stub, _ = agent
    (stub / "hash_empty").touch()
    r = _run(agent, REPETE_MIRROR_REMOTE="b2:bucket",
             REPETE_BACKUP_PASSPHRASE="x")
    assert r.returncode != 0
    assert "no hash" in r.stderr
    assert _receipt(root) is None, "a receipt was written for an unverified upload"


def test_a_mismatched_remote_hash_fails(agent):
    root, stub, _ = agent
    (stub / "hash_wrong").touch()
    r = _run(agent, REPETE_MIRROR_REMOTE="b2:bucket",
             REPETE_BACKUP_PASSPHRASE="x")
    assert r.returncode != 0
    assert "remote hash" in r.stderr
    assert _receipt(root) is None


def test_a_failed_upload_fails_the_job(agent):
    root, stub, _ = agent
    (stub / "upload_fails").touch()
    r = _run(agent, REPETE_MIRROR_REMOTE="b2:bucket",
             REPETE_BACKUP_PASSPHRASE="x")
    assert r.returncode != 0
    assert _receipt(root) is None


def test_a_verified_upload_encrypts_first_and_writes_a_receipt(agent):
    root, stub, _ = agent
    r = _run(agent, REPETE_MIRROR_REMOTE="b2:bucket",
             REPETE_BACKUP_PASSPHRASE="x")
    assert r.returncode == 0, r.stderr
    assert (stub / "gpg_calls").exists(), "uploaded without encrypting"
    uploaded = (stub / "uploaded").read_text().strip()
    assert uploaded.endswith(".tar.gz.gpg"), f"uploaded plaintext: {uploaded}"
    rec = _receipt(root)
    assert rec and rec["verified"] is True
    assert rec["kind"] == "remote"


def test_no_remote_configured_writes_no_receipt(agent):
    """No mirror means no receipt, so health.py reports 'never' rather than
    inheriting a stale success from a previous configuration."""
    root, _, env = agent
    r = _run(agent)
    assert r.returncode == 0, r.stderr
    assert _receipt(root) is None


def test_nothing_configured_at_all_warns(agent):
    """An EXPLICIT empty REPETE_OFFHOST_DIR means 'deliberately disabled' and
    is silent by design. Leaving it unset with no iCloud and no remote is the
    accident, and that is the case that must say so out loud."""
    root, _, env = agent
    env = {k: v for k, v in env.items() if k != "REPETE_OFFHOST_DIR"}
    r = subprocess.run(["sh", str(BACKUP)], cwd=str(root), env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "no off-host mirror configured" in r.stderr
    assert _receipt(root) is None
