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
