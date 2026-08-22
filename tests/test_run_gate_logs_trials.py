"""Every arm of every gate run lands in the trial log — refused or not.

scripts/run_gate.py never called append_trial until 2026-08-22. It is the
runner behind 95 of 102 registered specs, so the trial log stopped on
2026-07-27 while the project kept running gates, and the audit could not
compute a Deflated Sharpe because nobody could say how many things had been
tried. Declared K was 16; verdicts.jsonl alone held 225 arm runs.

These drive main() end to end — real spec file, real registration row, real
sha-verified bars — with only run_arm stubbed, because the capture gap lived
in main() and a test of run_arms() could never have seen it.

The property that matters most is the second test: a run the judge-consistency
check REFUSES still logs its trials. Those arms were computed; the trial was
spent. A log that records only runs surviving every refusal is the dishonest
accounting this closes.
"""
import gzip
import hashlib
import json
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import run_gate  # noqa: E402
import backtest as bt  # noqa: E402
from gatespec import canonical_sha256  # noqa: E402


# ---- a complete, registered, sha-verified world in tmp_path ---------------

def _bars(n=400):
    """Two symbols, daily, long enough that fold/split logic is exercised."""
    out = {}
    for sym, base in (("SPY", 100.0), ("QQQ", 200.0)):
        rows = []
        for i in range(n):
            ts = f"2024-{1 + (i // 28) % 12:02d}-{1 + i % 28:02d}T21:00:00+00:00"
            px = base * (1 + 0.0003 * i)
            rows.append({"ts": ts, "open": px, "high": px * 1.01,
                         "low": px * 0.99, "close": px, "volume": 1e6})
        out[sym] = rows
    return out


def _spec(spec_id, snapshot_path, sha, **over):
    spec = {
        "id": spec_id, "claim": "DIAGNOSTIC",
        "title": "trial-log fixture",
        "judge_model": False,
        "snapshot": {"path": snapshot_path, "sha256": sha},
        "cash": 100000.0,
        "bonferroni_k": 16,
        "arms": [{"name": "baseline"}, {"name": "cand"}],
        "clauses": [{"id": "a", "rule": "min_trades", "n": 1}],
        "prior": ("This fixture exists to prove the trial log is written; the "
                  "verdict is irrelevant and expected to fail."),
        "failure_modes": ["none — this is a plumbing fixture"],
    }
    spec.update(over)
    return spec


def _judge_stats(acted):
    return ({"sized": 10, "cut": 3, "vetoed": 1, "zeroed": 0} if acted
            else {"sized": 10, "cut": 0, "vetoed": 0, "zeroed": 0})


# Module level, NOT a closure: run_arms() fans jobs across a multiprocessing
# pool, and a local function cannot be pickled across that boundary. The
# existing test_run_gate_reproduces.py learned this the same way. Whether the
# stub judge "acts" is carried on the job's cfg rather than captured, for the
# same reason.
def _fake_run_arm(job):
    _, arm, cfg, *_ = job
    acted = bool((cfg.get("_fixture") or {}).get("judge_acts"))
    summary = {"total_return_pct": 1.0, "profit_factor": 1.1,
               "max_drawdown_pct": 2.0, "n_trades": 5,
               "n_symbols_traded": 2, "avg_deployment_pct": 50.0,
               "buy_hold_return_pct": 0.5}
    return (arm["name"], summary, [1.0] * 5, 0.0, _judge_stats(acted),
            [(f"2024-01-{1 + i:02d}T21:00:00Z", f"K{i}") for i in range(5)])


def _make_run_arm(acted):
    """Threads `acted` through config.yaml in the CWD — which is tmp_path —
    so it reaches the worker inside the job's cfg, picklable, no globals."""
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["_fixture"] = {"judge_acts": acted}
    open("config.yaml", "w").write(yaml.safe_dump(cfg))
    return _fake_run_arm


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Registered spec + frozen data + config.yaml, all under tmp_path, with
    CWD moved there so every relative path run_gate uses resolves inside it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "research" / "specs").mkdir(parents=True)
    (tmp_path / "data").mkdir()

    raw = json.dumps(_bars()).encode()
    snap = tmp_path / "data" / "bars.json.gz"
    with gzip.open(snap, "wb") as f:
        f.write(raw)
    sha = hashlib.sha256(snap.read_bytes()).hexdigest()

    # A minimal config the runner can load. The stubbed run_arm never reads
    # strategy settings, so only the keys run_gate touches need to exist.
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "backtest": {"judge_model": {"enabled": False}},
        "risk": {}, "strategies": {}, "symbols": ["SPY", "QQQ"],
    }))

    def register(spec):
        (tmp_path / "research" / "specs" / f"{spec['id']}.yaml").write_text(
            yaml.safe_dump(spec))
        row = {"id": spec["id"], "spec_sha256": canonical_sha256(spec),
               "registered_at": "2026-08-22T00:00:00+00:00", "spec": spec}
        with open(tmp_path / "research" / "registrations.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
        return spec

    return {"tmp": tmp_path, "snapshot": "data/bars.json.gz", "sha": sha,
            "register": register}


def _run(world, spec_id, workers=1, argv_extra=()):
    trials = world["tmp"] / "research" / "trials.jsonl"
    verdicts = world["tmp"] / "research" / "verdicts.jsonl"
    argv = ["run_gate.py", spec_id, "--workers", str(workers),
            "--registrations", str(world["tmp"] / "research" / "registrations.jsonl"),
            "--verdicts", str(verdicts), "--trials", str(trials),
            "--spec-dir", str(world["tmp"] / "research" / "specs"),
            "--resamples", "20", *argv_extra]
    return argv, trials, verdicts


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---- the tests -------------------------------------------------------------

def test_every_arm_of_a_scored_run_is_logged(world, monkeypatch):
    spec = world["register"](_spec("t1", world["snapshot"], world["sha"]))
    monkeypatch.setattr(run_gate, "run_arm", _make_run_arm(acted=False))
    argv, trials, verdicts = _run(world, "t1")
    monkeypatch.setattr(sys, "argv", argv)

    run_gate.main()

    rows = _rows(trials)
    assert len(rows) == len(spec["arms"]) == 2
    assert {r["arm"] for r in rows} == {"baseline", "cand"}
    for r in rows:
        assert r["section"] == "t1"
        assert r["claim"] == "DIAGNOSTIC"
        assert r["bonferroni_k"] == 16
        assert r["judge_model"] is False
        assert r["snapshot_sha256"] == world["sha"]
        assert r["spec_sha256"]
        assert "logged_at" in r
        assert r["n_trades"] == 5           # the summary is carried whole
    # and the verdict was also written — this was a normal, recorded run
    assert len(_rows(verdicts)) == 1


def test_a_run_the_judge_check_refuses_STILL_logs_its_trials(world, monkeypatch):
    """THE HONESTY PROPERTY.

    judge_model: true, but the (stubbed) judge never acts -> run_gate refuses
    to record the verdict and SystemExits. The arms were computed all the
    same. The trial was spent. The log must say so, or it is a record of the
    runs that looked good enough to keep.
    """
    spec = world["register"](_spec("t2", world["snapshot"], world["sha"],
                                   judge_model=True))
    monkeypatch.setattr(run_gate, "run_arm", _make_run_arm(acted=False))
    argv, trials, verdicts = _run(world, "t2")
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit):
        run_gate.main()

    assert not _rows(verdicts), "the refusal must write no verdict"
    rows = _rows(trials)
    assert len(rows) == len(spec["arms"]) == 2, (
        "a refused run spent its trials; they must be in the log")
    assert all(r["judge_model"] is True for r in rows)


def test_a_dry_run_logs_nothing(world, monkeypatch):
    """Nothing was computed, so there is no trial to record."""
    world["register"](_spec("t3", world["snapshot"], world["sha"]))
    monkeypatch.setattr(run_gate, "run_arm", _make_run_arm(acted=False))
    argv, trials, _ = _run(world, "t3", argv_extra=("--dry-run",))
    monkeypatch.setattr(sys, "argv", argv)
    run_gate.main()
    assert not trials.exists() or not _rows(trials)


def test_worker_count_does_not_change_the_log(world, monkeypatch):
    """The hook sits in the parent, once. If it ever moves into run_arm the
    workers would each append and this would break — which is the point."""
    world["register"](_spec("t4", world["snapshot"], world["sha"]))
    monkeypatch.setattr(run_gate, "run_arm", _make_run_arm(acted=False))

    argv1, trials1, _ = _run(world, "t4", workers=1)
    monkeypatch.setattr(sys, "argv", argv1)
    run_gate.main()
    one = [{k: v for k, v in r.items() if k not in ("logged_at", "workers")}
           for r in _rows(trials1)]
    trials1.unlink()

    argv2, trials2, _ = _run(world, "t4", workers=2)
    monkeypatch.setattr(sys, "argv", argv2)
    run_gate.main()
    two = [{k: v for k, v in r.items() if k not in ("logged_at", "workers")}
           for r in _rows(trials2)]

    assert sorted(one, key=lambda r: r["arm"]) == sorted(two, key=lambda r: r["arm"])
    assert len(two) == 2


def test_the_default_path_is_the_one_constant():
    """Fifteen copies of this literal existed on 2026-08-22. There is one."""
    assert bt.DEFAULT_TRIALS_PATH == "research/trials.jsonl"
    assert not bt.DEFAULT_TRIALS_PATH.startswith("memory/"), (
        "memory/ is gitignored; a trial log there cannot be checked in CI")
