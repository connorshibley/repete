"""Append-only event storage behind one interface (2026-07-22, backlog #4).

Every store in this project does exactly two things: append a dict, read them
all back in insertion order. That is the whole interface — so the backend can
change without any domain class changing.

  JsonlStore  — one JSON object per line. THE DEFAULT; byte-identical to the
                behavior this project has always had. Zero migration risk.
  SqliteStore — the same events in a single-table SQLite DB (WAL mode), for
                container/server deployments where a real DB beats loose
                files: atomic appends, crash-safe, indexed reads, one file to
                back up. Still append-only — there is no update or delete.

Selection is process-wide and set ONCE at startup by configure(cfg), so every
existing `Ledger(path)` / `LessonStore(path)` call site keeps working
untouched. Nothing here interprets record contents: the audit trail's shape
stays owned by the domain classes.
"""
import json
import os
import sqlite3
import threading

_BACKEND = {"kind": "jsonl", "sqlite_path": "memory/agent.db"}
_LOCK = threading.Lock()


def configure(cfg: dict | None):
    """Set the process-wide backend from config (storage.backend:
    jsonl | sqlite). Unknown/missing config keeps the JSONL default —
    a storage typo must never silently move the audit trail."""
    scfg = (cfg or {}).get("storage") or {}
    kind = str(scfg.get("backend", "jsonl")).lower()
    _BACKEND["kind"] = kind if kind in ("jsonl", "sqlite") else "jsonl"
    _BACKEND["sqlite_path"] = scfg.get("sqlite_path", "memory/agent.db")


def current_backend() -> str:
    return _BACKEND["kind"]


def stream_name(path: str) -> str:
    """A JSONL path maps to a stream name: memory/ledger.jsonl -> 'ledger'."""
    return os.path.splitext(os.path.basename(path))[0]


def open_store(path: str):
    """The store for a given historical JSONL path, per the configured
    backend. Callers keep passing the path they always passed."""
    if _BACKEND["kind"] == "sqlite":
        return SqliteStore(_BACKEND["sqlite_path"], stream_name(path))
    return JsonlStore(path)


class JsonlStore:
    """One JSON object per line, appended. The original format."""

    def __init__(self, path: str):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def append(self, record: dict):
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def read_all(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []


class SqliteStore:
    """Append-only events in SQLite. One row per record, insertion ordered.

    WAL mode + a per-connection lock: the trading cycle is a single writer,
    while dashboards/publishers read concurrently without blocking it.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS events (
      id     INTEGER PRIMARY KEY AUTOINCREMENT,
      stream TEXT NOT NULL,
      data   TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_events_stream ON events(stream, id);
    """

    def __init__(self, db_path: str, stream: str):
        self.db_path = db_path
        self.stream = stream
        d = os.path.dirname(db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with _LOCK:
            self._conn.executescript(self._SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def append(self, record: dict):
        with _LOCK:
            self._conn.execute(
                "INSERT INTO events (stream, data) VALUES (?, ?)",
                (self.stream, json.dumps(record)))
            self._conn.commit()

    def read_all(self) -> list[dict]:
        with _LOCK:
            rows = self._conn.execute(
                "SELECT data FROM events WHERE stream = ? ORDER BY id",
                (self.stream,)).fetchall()
        return [json.loads(r[0]) for r in rows]
