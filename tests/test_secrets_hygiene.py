"""Secrets never enter the git tree (regression guard, 2026-07-19).

Prompted by the common-trade audit finding: unsanitized news headlines flow
into an LLM, and if secrets sit inside the working tree they are one tool
grant away from exfiltration. This bot's judge holds no file tools, but the
cheap hardening is making 'no secrets in git' a failing test, not a habit.
Skips (never fails) where git is unavailable.
"""
import os
import re
import subprocess

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# High-signal secret shapes. Assembled by concatenation so this file can
# never match its own patterns.
PATTERNS = [
    re.compile("sk-" + "ant-" + r"[A-Za-z0-9_\-]{16,}"),      # Anthropic key
    re.compile("AKIA" + r"[0-9A-Z]{16}"),                       # AWS access key
    re.compile(r"(?i)(api_?key|secret|token|password)\s*=\s*"
               r"['\"][A-Za-z0-9+/_\-]{24,}['\"]"),             # generic assignment
]
ALLOW = {".env.example", "tests/test_secrets_hygiene.py"}
TEXT_EXT = {".py", ".yaml", ".yml", ".md", ".sh", ".json", ".txt",
            ".html", ".toml", ".cfg", ".ini", ""}


def _tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git unavailable")
    if out.returncode != 0:
        pytest.skip("not a git repository")
    return [f for f in out.stdout.splitlines() if f]


def test_env_and_memory_are_not_tracked():
    tracked = _tracked_files()
    assert ".env" not in tracked, ".env (live keys) must never be committed"
    leaked = [f for f in tracked if f.startswith("memory/")]
    assert not leaked, f"memory/ stores must stay local, found: {leaked}"


def test_gitignore_covers_secrets():
    with open(os.path.join(ROOT, ".gitignore")) as f:
        lines = {line.strip() for line in f}
    assert ".env" in lines
    assert any(line.startswith("memory") for line in lines)


def test_no_secret_shapes_in_tracked_files():
    hits = []
    for f in _tracked_files():
        if f in ALLOW or os.path.splitext(f)[1] not in TEXT_EXT:
            continue
        path = os.path.join(ROOT, f)
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except OSError:
            continue
        for pat in PATTERNS:
            if pat.search(content):
                hits.append((f, pat.pattern[:30]))
    assert not hits, f"possible secrets committed: {hits}"
