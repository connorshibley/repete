"""Phase D: structured JSON logging with secret redaction."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

import log as structlog


@pytest.fixture(autouse=True)
def _clean_root_handlers():
    """Other modules (main.py) may have attached the json handler at import
    time — detach so each test here gets a fresh one at its own path."""
    def _strip():
        root = logging.getLogger()
        for h in [h for h in root.handlers
                  if getattr(h, "_repete_json", False)]:
            root.removeHandler(h)
            h.close()
    _strip()
    yield
    _strip()


def test_secret_values_picks_secretish_names(monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "supersecretvalue123")
    monkeypatch.setenv("SOME_TOKEN", "another-secret-value")
    monkeypatch.setenv("PLAIN_SETTING", "not-a-secret-hooray")
    monkeypatch.setenv("SHORT_KEY", "tiny")            # < 8 chars: skipped
    vals = structlog.secret_values()
    assert "supersecretvalue123" in vals
    assert "another-secret-value" in vals
    assert "not-a-secret-hooray" not in vals
    assert "tiny" not in vals


def test_redact_replaces_secrets(monkeypatch):
    monkeypatch.setenv("X_SECRET", "hunter2hunter2")
    out = structlog.redact("calling api with hunter2hunter2 now")
    assert "hunter2hunter2" not in out
    assert "[REDACTED]" in out


def test_json_handler_emits_valid_redacted_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAKY_API_KEY", "leak-me-please-123")
    path = str(tmp_path / "agent.jsonl")
    h = structlog.attach_json_handler(path)
    try:
        logger = logging.getLogger("test.redaction")
        logger.setLevel(logging.INFO)
        logger.info("connecting with key leak-me-please-123 to broker")
        h.flush()
        lines = [ln for ln in open(path).read().splitlines() if ln.strip()]
        assert lines
        rec = json.loads(lines[-1])          # valid JSON line
        assert rec["level"] == "INFO" and rec["name"] == "test.redaction"
        assert "leak-me-please-123" not in rec["msg"]
        assert "[REDACTED]" in rec["msg"]
    finally:
        logging.getLogger().removeHandler(h)
        h.close()


def test_attach_is_idempotent(tmp_path):
    path = str(tmp_path / "a.jsonl")
    h1 = structlog.attach_json_handler(path)
    h2 = structlog.attach_json_handler(str(tmp_path / "b.jsonl"))
    try:
        assert h1 is h2                      # second call returns the first
    finally:
        logging.getLogger().removeHandler(h1)
        h1.close()


# ---- the two gaps found by the 2026-07-27 audit ----

def test_a_capability_url_is_treated_as_a_secret(monkeypatch):
    """HEARTBEAT_PING_URL and ALERT_WEBHOOK_URL ARE credentials.

    Anyone holding https://hc-ping.com/<uuid> can forge this bot's
    proof-of-life; anyone holding the alert webhook can forge its alerts.
    Neither name contains KEY/SECRET/TOKEN/PASSWORD, so both sat outside the
    redaction set — while src/alerting.py carefully refused to log one of them
    by hand. A rule enforced at one call site is a habit, not a control.
    """
    import log as structlog
    monkeypatch.setenv("HEARTBEAT_PING_URL", "https://hc-ping.com/deadbeef-uuid")
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.example.com/T0/B0/xyz")
    out = structlog.redact(
        "ping failed for https://hc-ping.com/deadbeef-uuid via "
        "https://hooks.example.com/T0/B0/xyz")
    assert "deadbeef-uuid" not in out
    assert "T0/B0/xyz" not in out
    assert out.count("[REDACTED]") == 2


def test_redaction_reaches_the_human_readable_handlers(tmp_path, monkeypatch):
    """The real gap: only the JSON mirror redacted.

    `logs/agent.log` and stdout — captured by launchd into
    `logs/launchd.err.log`, and the two logs a human actually opens during an
    incident — were written raw. A key echoed back inside a broker or model
    SDK traceback would have landed in both in the clear.
    """
    import logging
    import log as structlog

    monkeypatch.setenv("ALPACA_SECRET_KEY", "SUPERSECRETVALUE123")
    path = tmp_path / "human.log"
    root = logging.getLogger("redaction_probe")
    root.handlers.clear()
    h = logging.FileHandler(path)
    h.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(h)
    root.propagate = False

    wrapped = structlog.redact_existing_handlers(root)
    assert wrapped == 1

    root.error("alpaca rejected the key SUPERSECRETVALUE123")
    h.flush()
    body = path.read_text()
    assert "SUPERSECRETVALUE123" not in body, "secret reached a plain log file"
    assert "[REDACTED]" in body
    root.handlers.clear()


def test_wrapping_is_idempotent_and_skips_the_json_handler(tmp_path):
    """Re-running configure_logging must not nest formatters, and the JSON
    handler already redacts — double-wrapping it would be wasted work."""
    import logging
    import log as structlog

    root = logging.getLogger("redaction_probe2")
    root.handlers.clear()
    h = logging.FileHandler(tmp_path / "a.log")
    root.addHandler(h)
    j = logging.FileHandler(tmp_path / "b.jsonl")
    j.setFormatter(structlog.JsonFormatter())
    j._repete_json = True
    root.addHandler(j)

    assert structlog.redact_existing_handlers(root) == 1   # json one skipped
    assert structlog.redact_existing_handlers(root) == 0   # already wrapped
    assert isinstance(h.formatter, structlog.RedactingFormatter)
    assert isinstance(j.formatter, structlog.JsonFormatter)
    root.handlers.clear()
