"""The sidecar-mutation lever, and the one way it could lie.

Its whole job is to recompute the hash the way dashboard.render does; if the
two ever drift, the mutated sidecar's hash won't move (or moves wrongly), the
page never swaps, and a browser pass reports listener-survival on a swap that
never happened — a false PASS on the exact criterion the lever exists to
test. So the shape test below compares against a REAL render's stamp, never
this module's own arithmetic (Method note 1, docs/qa_findings.md).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import qa_mutate_sidecar as qms  # noqa: E402


def _render(tmp_path):
    import yaml

    import dashboard
    import store
    store.configure({"storage": {"backend": "jsonl"}})
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["memory"] = dict(cfg["memory"])
    for k, fn in (("ledger_path", "l.jsonl"), ("learnings_path", "l.md"),
                  ("lessons_path", "le.jsonl"), ("judgments_path", "j.jsonl")):
        cfg["memory"][k] = str(tmp_path / fn)
    (tmp_path / "l.jsonl").write_text(
        json.dumps({"type": "decision", "trade_id": "t1", "symbol": "SPY",
                    "action": "buy", "executed": False,
                    "detail": "quiet hold", "ts": "2026-08-29T15:45:00Z"}) + "\n")
    cfg["publish"] = dict(cfg.get("publish") or {})
    cfg["publish"]["out_dir"] = str(tmp_path)
    dashboard.render(cfg, spy_bars=[])
    return tmp_path / "dashboard_data.json"


def test_recompute_matches_a_real_renders_stamp(tmp_path):
    """Unmutated regions must hash to exactly what render() stamped."""
    sidecar = _render(tmp_path)
    d = json.loads(sidecar.read_text())
    assert qms.recompute_hash(d["regions"]) == d["hash"], (
        "qa_mutate_sidecar's hash arithmetic drifted from dashboard.render — "
        "every mutation it makes will fail to trigger a swap")


def test_mutation_changes_the_hash_and_keeps_it_consistent(tmp_path):
    sidecar = _render(tmp_path)
    before = json.loads(sidecar.read_text())["hash"]
    qms.mutate(str(sidecar))
    after = json.loads(sidecar.read_text())
    assert after["hash"] != before
    assert qms.recompute_hash(after["regions"]) == after["hash"]
    assert "QAINJ" in after["regions"]["decisions"]


def test_refuses_live_paths(tmp_path):
    for bad in (str(ROOT / "dashboard_data.json"),
                str(ROOT / ".site" / "dashboard_data.json"),
                str(ROOT / "memory" / "dashboard_data.json")):
        with pytest.raises(SystemExit):
            qms.mutate(bad)
