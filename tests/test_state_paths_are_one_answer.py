"""The writer and every reader of the agent's state files must agree.

F-14 (docs/qa_findings.md). `src/health.py` held `HEARTBEAT_FILE` and
`HALT_FILE` as module constants no config could redirect, so `/healthz`
reported on whatever sat next to the process rather than on the data it was
handed. The same fixture and the same command gave 58/58 with a
`./memory/heartbeat` present and 57/58 without — PUB-04 had never measured the
fixture, it measured the host.

The deeper problem was duplication: THREE modules held their own copy of
`"memory/heartbeat"` — the writer in `main.py`, readers in `health.py` and
`watchdog.py` — and nothing made them agree. Three constants can drift where
one cannot, and `test_drawdown_latch_visibility.py::test_watchdog_and_health_agree_on_engagement`
only compares drawdown engagement, so no test would have caught it.

HOW THESE TESTS DETECT DISAGREEMENT rather than merely failing to observe it:
configure a path the default cannot reach, then assert the default location was
never created. Any module still falling back to `memory/heartbeat` reads
nothing and reports "the cycle has never run". A test that simply pointed
everything at one place and checked it worked would pass just as happily on
three modules that agreed by coincidence.
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

import health
import main
import preflight
import pytest
import risk
import statepaths
import watchdog
import yaml

NOW = datetime(2026, 8, 11, 20, tzinfo=timezone.utc)   # a Tuesday


def _cfg_at(cfg, tmp_path, beat=None, halt=None):
    cfg["memory"]["ledger_path"] = str(tmp_path / "ledger.jsonl")
    if beat is not None:
        cfg["memory"]["heartbeat_path"] = str(beat)
    if halt is not None:
        cfg["memory"]["halt_path"] = str(halt)
    return cfg


# ---- the guard ------------------------------------------------------------

def test_the_writer_and_both_readers_use_the_same_file(tmp_path, monkeypatch, cfg):
    """The whole point. main.write_heartbeat() is the production writer;
    health.status() feeds /healthz and the container healthcheck; watchdog is
    the thing that actually pages a human."""
    monkeypatch.chdir(tmp_path)
    beat = tmp_path / "state" / "hb"          # a directory that does not exist
    cfg = _cfg_at(cfg, tmp_path, beat=beat)
    with open("config.yaml", "w") as f:       # for the cfg=None reader below
        yaml.safe_dump(cfg, f)

    main.write_heartbeat()                    # exactly as run_cycle calls it

    assert beat.exists(), "the writer ignored the configured path"
    assert not (tmp_path / "memory").exists(), (
        "the writer created the cwd-relative default too — a reader that "
        "agreed with it by accident would make this test vacuous")

    # reader 1a — /healthz, cfg injected by publisher/app.py
    assert health.status(cfg, now=NOW)["heartbeat_age_hours"] is not None
    # reader 1b — `python src/health.py`, which loads config.yaml itself. This
    # is the Dockerfile HEALTHCHECK and the fly command check, and it is why no
    # CLI flag was needed (test_deploy_artifacts.py pins that exact string).
    assert health.status(now=NOW)["heartbeat_age_hours"] is not None
    # reader 2 — the watchdog, which passed no path at all until F-14
    problems = watchdog.check(today=NOW.date(),
                              heartbeat_path=statepaths.heartbeat_path(cfg),
                              halt_path=str(tmp_path / "nope"))
    assert not [p for p in problems if "heartbeat" in p.lower()], problems


def test_the_watchdogs_ENTRYPOINT_resolves_the_paths(tmp_path, monkeypatch, cfg):
    """watchdog.main(), not watchdog.check().

    The first version of this file only ever called check() with explicit
    paths, so a mutation reverting main() to a bare check() — reader 2 going
    back to module defaults, which is half of F-14 — SURVIVED. Testing the
    injectable seam proves the seam works; it says nothing about whether the
    production entrypoint uses it. Reported rather than quietly patched.

    Keyed on HALT because check() reports it on any day, so this does not
    silently become a no-op at the weekend the way a heartbeat assertion would.
    """
    monkeypatch.chdir(tmp_path)
    halt = tmp_path / "elsewhere" / "HALT"
    halt.parent.mkdir()
    halt.write_text("mode: freeze\n")
    assert not os.path.exists("HALT"), "the cwd default must NOT exist here"

    cfg = _cfg_at(cfg, tmp_path, beat=tmp_path / "hb", halt=halt)
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    seen: list = []
    monkeypatch.setattr(watchdog, "notify", lambda t, m: seen.append(m))
    watchdog.main()
    assert any("HALT" in m for m in seen), (
        f"watchdog.main() did not see the configured HALT — it is reading "
        f"module defaults again. alerts={seen}")


def test_the_watchdogs_entrypoint_is_quiet_when_the_configured_halt_is_absent(
        tmp_path, monkeypatch, cfg):
    """Paired control for the test above. If main() alerted unconditionally,
    or read some other file that happened to exist, that test would pass for
    the wrong reason."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "HALT").write_text("mode: freeze\n")     # a decoy in the cwd
    cfg = _cfg_at(cfg, tmp_path, beat=tmp_path / "hb",
                  halt=tmp_path / "elsewhere" / "HALT")  # configured, absent
    (tmp_path / "hb").write_text(datetime.now(timezone.utc).isoformat() + "\n")
    with open("config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    seen: list = []
    monkeypatch.setattr(watchdog, "notify", lambda t, m: seen.append(m))
    watchdog.main()
    assert not any("HALT" in m for m in seen), (
        f"watchdog.main() reported the cwd decoy instead of the configured "
        f"path. alerts={seen}")


def test_the_monitors_follow_a_configured_halt_path(tmp_path, monkeypatch, cfg):
    monkeypatch.chdir(tmp_path)
    halt = tmp_path / "elsewhere" / "HALT"
    halt.parent.mkdir()
    halt.write_text("2026-08-11 — qa\nmode: exits\n")
    cfg = _cfg_at(cfg, tmp_path, halt=halt)

    assert health.status(cfg, now=NOW)["halted"] is True
    assert not os.path.exists("HALT"), (
        "nothing should have created a cwd HALT — this test would pass for "
        "the wrong reason if something had")
    problems = watchdog.check(today=NOW.date(),
                              heartbeat_path=str(tmp_path / "nope"),
                              halt_path=statepaths.halt_path(cfg))
    assert any("HALT" in p for p in problems), problems


def test_the_trading_rail_ignores_a_configured_halt_path(tmp_path, monkeypatch, cfg):
    """DELIBERATE ASYMMETRY, not an oversight.

    The monitors resolve the halt path from config so a health check can be
    pointed at a fixture. `risk.check_halt()` — which is on the trading rail at
    risk.py's pre_trade_checks — does not, because a config key able to move
    the kill switch is a way to disable it. scripts/halt.py:80 settled this:
    "a HALT engaged into some other directory is a stop button wired to
    nothing." The rail reads where halt.py writes, always.

    test_preflight_refuses_a_non_default_state_path is what stops that
    asymmetry from ever existing in a process that trades.
    """
    monkeypatch.chdir(tmp_path)
    halt = tmp_path / "elsewhere" / "HALT"
    halt.parent.mkdir()
    halt.write_text("mode: freeze\n")
    cfg = _cfg_at(cfg, tmp_path, halt=halt)

    assert health.status(cfg, now=NOW)["halted"] is True    # monitor follows
    assert risk.check_halt() is False                       # rail does not
    risk.engage_halt("real", mode=risk.HALT_MODE_FREEZE)
    assert risk.check_halt() is True
    assert os.path.exists("HALT"), "the rail must write where halt.py writes"


def test_preflight_refuses_a_non_default_state_path(cfg):
    """The interlock that makes the asymmetry above safe: a cycle can never run
    with the monitors watching a different file than the kill switch."""
    for key in ("heartbeat_path", "halt_path"):
        c = {**cfg, "memory": {**cfg["memory"], key: "/tmp/somewhere-else"}}
        fails = preflight.run(c)
        assert any(key in f for f in fails), (
            f"preflight allowed a redirected {key}: {fails}")


def test_preflight_allows_the_defaults(cfg):
    """Negative control. If preflight rejected the keys unconditionally the
    test above would pass for the wrong reason and no cycle could ever run."""
    c = {**cfg, "memory": {**cfg["memory"],
                           "heartbeat_path": statepaths.DEFAULT_HEARTBEAT_PATH,
                           "halt_path": statepaths.DEFAULT_HALT_PATH}}
    fails = [f for f in preflight.run(c)
             if "heartbeat_path" in f or "halt_path" in f]
    assert not fails, fails


# ---- totality -------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    None, {}, {"memory": None}, {"memory": {}},
    {"memory": {"heartbeat_path": None, "halt_path": None}},
    {"memory": {"heartbeat_path": "", "halt_path": ""}},
    "not a dict", 42, [],
])
def test_resolution_is_total(bad):
    """These run inside run_cycle's `finally:` — where an exception may already
    be in flight — and inside /healthz. Anything raised there would replace a
    real crash with a path error, or take down the endpoint whose job is to
    report the crash."""
    assert statepaths.heartbeat_path(bad) == statepaths.DEFAULT_HEARTBEAT_PATH
    assert statepaths.halt_path(bad) == statepaths.DEFAULT_HALT_PATH


def test_a_bare_filename_still_writes(tmp_path, monkeypatch, cfg):
    """os.path.dirname("hb") is "" and os.makedirs("") raises
    FileNotFoundError — an OSError, which write_heartbeat catches and logs. A
    configured bare filename would have silently cost proof-of-life."""
    monkeypatch.chdir(tmp_path)
    main.write_heartbeat(_cfg_at(cfg, tmp_path, beat="hb"))
    assert (tmp_path / "hb").exists()


def test_a_failing_heartbeat_write_never_replaces_the_real_error(tmp_path,
                                                                 monkeypatch,
                                                                 cfg):
    """write_heartbeat runs in run_cycle's `finally:`. If it raises, it masks
    whatever the cycle was already failing with — turning a diagnosable crash
    into a path error."""
    monkeypatch.chdir(tmp_path)
    blocker = tmp_path / "afile"
    blocker.write_text("not a directory")
    cfg = _cfg_at(cfg, tmp_path, beat=blocker / "hb")

    try:
        raise RuntimeError("the original failure")
    except RuntimeError:
        main.write_heartbeat(cfg)             # must swallow, not re-raise
    assert not (blocker / "hb").exists()


def test_a_broken_config_still_reports_a_real_halt(tmp_path, monkeypatch):
    """health.status falls back to `cfg = {}` when config.yaml is unreadable,
    because health must never crash. The halt read has to keep working under
    that fallback — otherwise a broken config makes a genuinely halted bot
    report halted: false, which is scripts/halt.py's "stop button wired to
    nothing", inverted."""
    monkeypatch.chdir(tmp_path)
    with open("config.yaml", "w") as f:
        f.write("{{{ not yaml")
    (tmp_path / "HALT").write_text("mode: freeze\n")
    assert health.status(now=NOW)["halted"] is True


# ---- the documented literals ---------------------------------------------

def test_the_defaults_are_the_documented_literals():
    """One definition, and it is the one operators are told about.

    Named in HEARTBEAT.md, docs/runbooks.md, docs/slo.md, deploy/README.md,
    deploy/deploy.sh and CLAUDE.md; relied on by deploy/fly.toml (which
    symlinks memory/ onto /app/state) and docker-compose.yml (which mounts
    ./memory:ro for the publisher).

    Must stay CWD-RELATIVE. A default anchored on __file__ would make
    tests/test_publisher.py's "no heartbeat has ever been written in this
    fixture" find the developer's real heartbeat and report HEALTHY on one
    machine and DEGRADED on another.
    """
    assert statepaths.DEFAULT_HEARTBEAT_PATH == "memory/heartbeat"
    assert statepaths.DEFAULT_HALT_PATH == "HALT"
    assert not os.path.isabs(statepaths.DEFAULT_HEARTBEAT_PATH)
    assert not os.path.isabs(statepaths.DEFAULT_HALT_PATH)
    assert (health.HEARTBEAT_FILE == main.HEARTBEAT_FILE
            == watchdog.HEARTBEAT_FILE == statepaths.DEFAULT_HEARTBEAT_PATH)
    assert (health.HALT_FILE == watchdog.HALT_FILE == risk.HALT_FILE
            == statepaths.DEFAULT_HALT_PATH)


def test_resolvers_never_touch_the_filesystem(tmp_path, monkeypatch):
    """health.status() is read-only (invariant #9) and the publisher calls it
    that way. sitepaths.resolve() mkdirs on purpose; this must not — reading
    state must never create it."""
    monkeypatch.chdir(tmp_path)
    before = set(os.listdir(tmp_path))
    statepaths.heartbeat_path({"memory": {"heartbeat_path": "a/b/c"}})
    statepaths.halt_path({"memory": {"halt_path": "d/e/f"}})
    assert set(os.listdir(tmp_path)) == before


# ---- the CLI reader, end to end -------------------------------------------

def test_the_health_cli_reads_the_configured_path(tmp_path):
    """`python src/health.py` is the Docker HEALTHCHECK and the fly check. It
    takes no flags (test_deploy_artifacts.py pins the exact command string), so
    it must pick the path up from config.yaml on its own."""
    beat = tmp_path / "state" / "hb"
    beat.parent.mkdir()
    beat.write_text(datetime.now(timezone.utc).isoformat() + "\n")
    (tmp_path / "memory").mkdir()
    cfg = {"mode": "paper",
           "memory": {"ledger_path": str(tmp_path / "ledger.jsonl"),
                      "heartbeat_path": str(beat)},
           "risk": {"max_drawdown_pct": 10.0}}
    with open(tmp_path / "config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "health.py")
    r = subprocess.run([sys.executable, src, "--json"], cwd=tmp_path,
                       capture_output=True, text=True)
    import json
    assert json.loads(r.stdout)["heartbeat_age_hours"] is not None, r.stdout


def test_the_health_cli_reports_never_when_the_configured_file_is_absent(tmp_path):
    """Paired control for the test above: if the CLI ignored the config and
    read a cwd default that happened to exist, that test would pass for the
    wrong reason."""
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "heartbeat").write_text(
        datetime.now(timezone.utc).isoformat() + "\n")     # a decoy
    cfg = {"mode": "paper",
           "memory": {"ledger_path": str(tmp_path / "ledger.jsonl"),
                      "heartbeat_path": str(tmp_path / "state" / "absent")},
           "risk": {"max_drawdown_pct": 10.0}}
    with open(tmp_path / "config.yaml", "w") as f:
        yaml.safe_dump(cfg, f)

    src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "src", "health.py")
    r = subprocess.run([sys.executable, src, "--json"], cwd=tmp_path,
                       capture_output=True, text=True)
    import json
    assert json.loads(r.stdout)["heartbeat_age_hours"] is None, (
        "the CLI read the cwd decoy instead of the configured path")


def test_the_fixture_heartbeat_matches_its_own_last_cycle(tmp_path):
    """scripts/qa_fixture.py stamps the heartbeat with the fixture's last
    cycle_complete, not the wall clock: a heartbeat that disagreed with the
    ledger describes a state the bot cannot be in, and now() would break the
    byte-identical rerun guarantee."""
    import json
    gen = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts", "qa_fixture.py")
    out = tmp_path / "fx"
    subprocess.run([sys.executable, gen, "--out", str(out), "--days", "9",
                    "--anchor", "2026-08-11"], capture_output=True, text=True,
                   check=True)
    beat = (out / "heartbeat").read_text().strip()
    cycles = [json.loads(x)["ts"] for x in open(out / "ledger.jsonl")
              if x.strip() and json.loads(x).get("event") == "cycle_complete"]
    assert beat == cycles[-1]


def test_the_empty_fixture_has_no_heartbeat(tmp_path):
    """Day one: the cycle has never run, and that is the honest thing for
    health.status() to say. A fabricated heartbeat would make the never-run
    state untestable."""
    gen = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "scripts", "qa_fixture.py")
    out = tmp_path / "fx"
    subprocess.run([sys.executable, gen, "--out", str(out), "--profile",
                    "empty", "--anchor", "2026-08-11"], capture_output=True,
                   text=True, check=True)
    assert not (out / "heartbeat").exists()
