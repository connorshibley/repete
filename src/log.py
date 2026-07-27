"""Structured logging with secret redaction (Phase D, 2026-07-22).

`attach_json_handler()` adds a JSON-lines handler (logs/agent.jsonl) to the
root logger WITHOUT touching the existing human-readable handlers — the
launchd/console logs the owner reads stay exactly as they were.

Redaction is belt-and-braces on top of the secrets-hygiene test: any env
value whose NAME looks secret (KEY/SECRET/TOKEN/PASSWORD) is replaced with
[REDACTED] in every emitted line, so a secret can't leak even through an
exception message that embeds one.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone

# WEBHOOK / PING_URL are here because a capability URL IS the credential —
# anyone holding https://hc-ping.com/<uuid> can forge this bot's proof-of-life,
# and anyone holding the alert webhook can forge its alerts. Neither name
# contains KEY, SECRET, TOKEN or PASSWORD, so both sat outside the redaction
# set while src/alerting.py:79 was carefully refusing to log one of them by
# hand. A rule enforced in one call site is a habit, not a control.
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|WEBHOOK|PING_URL",
                          re.I)
_MIN_LEN = 8   # values shorter than this are too collision-prone to scrub


def secret_values() -> list[str]:
    """Current env values that must never appear in a log line."""
    return [v for k, v in os.environ.items()
            if _SECRET_NAME.search(k) and v and len(v) >= _MIN_LEN]


def redact(text: str, secrets: list[str] | None = None) -> str:
    for s in (secrets if secrets is not None else secret_values()):
        if s in text:
            text = text.replace(s, "[REDACTED]")
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            line["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(line, default=str))


class RedactingFormatter(logging.Formatter):
    """Wraps another formatter and scrubs secrets from whatever it produced.

    Delegation rather than subclassing, so it works over whatever format the
    handler was already configured with — including the exception text, which
    is where an SDK is most likely to echo a key back at you.
    """

    def __init__(self, inner: logging.Formatter):
        super().__init__()
        self._inner = inner

    def format(self, record: logging.LogRecord) -> str:
        return redact(self._inner.format(record))


def redact_existing_handlers(root: logging.Logger | None = None) -> int:
    """Put redaction in front of every handler already on the root logger.

    Until 2026-07-27 only the JSON mirror redacted. `logs/agent.log` and
    stdout — captured by launchd into `logs/launchd.err.log`, and the two
    logs a human actually opens during an incident — were written raw. A
    secret embedded in a broker or model SDK traceback would have landed in
    both in the clear.

    Idempotent, and returns how many handlers it wrapped so a caller can
    assert on it.
    """
    root = root or logging.getLogger()
    wrapped = 0
    for h in root.handlers:
        if getattr(h, "_repete_json", False):
            continue                       # JsonFormatter already redacts
        if isinstance(h.formatter, RedactingFormatter):
            continue
        h.setFormatter(RedactingFormatter(h.formatter or logging.Formatter()))
        wrapped += 1
    return wrapped


def attach_json_handler(path: str = "logs/agent.jsonl",
                        level: int = logging.INFO) -> logging.Handler:
    """Idempotent: a second call finds the existing handler and returns it."""
    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, "_repete_json", False):
            return h
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    h = logging.FileHandler(path, mode="a")
    h.setFormatter(JsonFormatter())
    h.setLevel(level)
    h._repete_json = True
    root.addHandler(h)
    return h
