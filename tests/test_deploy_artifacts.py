"""Deploy artifacts must stay consistent with the code they deploy.

Docs rot silently; these assert the few facts that would actually break a
deploy if they drifted.
"""
import os
import re
import subprocess
import tomllib

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_deploy_scripts_are_valid_shell():
    for script in ("deploy/deploy.sh", "scripts/backup.sh",
                   "scripts/install_launchd.sh", "scripts/publish_dashboard.sh"):
        r = subprocess.run(["sh", "-n", os.path.join(ROOT, script)],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{script}: {r.stderr}"


def test_fly_config_runs_the_scheduler_and_persists_state():
    with open(os.path.join(ROOT, "deploy", "fly.toml"), "rb") as f:
        cfg = tomllib.load(f)
    # A scheduler that autostops would sleep through the 15:45 ET cycle.
    assert "scheduler.py" in cfg["processes"]["app"]
    assert cfg["mounts"]["source"]                     # state outlives deploys
    assert cfg["checks"]["health"]["command"] == "python src/health.py"


def test_secrets_doc_covers_every_env_var_the_example_declares():
    """A key documented in .env.example but missing from SECRETS.md is a key
    someone will forget to set on the host."""
    example = _read(".env.example")
    declared = {line.split("=")[0].strip()
                for line in example.splitlines()
                if "=" in line and not line.strip().startswith("#")}
    doc = _read("deploy", "SECRETS.md")
    missing = sorted(v for v in declared if v not in doc)
    assert not missing, f"undocumented in deploy/SECRETS.md: {missing}"


def test_deploy_docs_point_at_the_go_live_gate():
    """Deploying must not read as a route around the go-live gate: both docs
    have to name the interlock, and the deploy script must warn rather than
    proceed silently when config.yaml already says live."""
    readme = _read("deploy", "README.md")
    secrets = _read("deploy", "SECRETS.md")
    assert "go_live_checklist" in readme
    assert "LIVE_TRADING_CONFIRMED" in readme and "LIVE_TRADING_CONFIRMED" in secrets
    # The interlock is described as requiring BOTH halves, in both docs.
    assert "BOTH" in secrets

    script = _read("deploy", "deploy.sh")
    assert "go_live_checklist.md" in script      # the warning names the gate
    assert "LIVE_TRADING_CONFIRMED" in script    # ...and names the interlock
    # It may MENTION the interlock (it warns about it); it must never ASSIGN
    # either half. Assert on assignment/edit forms, not on mere mention.
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("printf") or stripped.startswith("#"):
            continue                              # warning text, not an action
        assert not re.match(r"(export\s+)?LIVE_TRADING_CONFIRMED\s*=", stripped), line
        assert not re.search(r"(sed|tee|>>?)\s.*config\.yaml", stripped), \
            f"deploy.sh must not rewrite config.yaml: {line}"
