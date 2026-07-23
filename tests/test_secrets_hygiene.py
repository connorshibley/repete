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
    # Vendors this project actually uses — the originals covered none of them.
    re.compile(r"\b[AP]K[A-Z0-9]{18}\b"),                       # Alpaca key id
    re.compile("gh" + r"[pousr]_[A-Za-z0-9]{36,}"),             # GitHub token
    re.compile("github" + r"_pat_[A-Za-z0-9_]{50,}"),           # GitHub fine-grained
    re.compile("sk_" + r"(live|test)_[A-Za-z0-9]{20,}"),        # Stripe secret
    re.compile("xox" + r"[baprs]-[A-Za-z0-9-]{10,}"),           # Slack token
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),          # PEM private key
    # (No Resend-specific shape: `re_...` collides with ordinary identifiers
    # like `..._never_breaks_posting`, and a scanner that cries wolf gets
    # ignored. RESEND_API_KEY=<value> is caught by the generic rule below.)
    # Generic assignment. The original required QUOTES, so it missed the very
    # shape .env uses (KEY=value), YAML (key: value) and JSON ("key": "value").
    # `[A-Za-z0-9_]*` after the keyword is what lets ALPACA_SECRET_KEY=... match
    # (the keyword is followed by _KEY, not by the separator).
    re.compile(r"(?i)(api_?key|secret|token|password|passwd)[A-Za-z0-9_]*"
               r"['\"]?\s*[:=]\s*"        # the ?" allows JSON's  "token": "…"
               r"['\"]?[A-Za-z0-9+/_\-]{24,}['\"]?"),
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


def test_patterns_actually_detect_the_shapes_they_claim():
    """Positive control: a regex that matches nothing is a scanner that always
    passes. Each sample is a SYNTHETIC key of the documented shape."""
    samples = {
        "anthropic": "sk-" + "ant-" + "A" * 20,
        "aws":       "AKIA" + "B" * 16,
        "alpaca":    "PK" + "C" * 18,
        "github":    "gh" + "p_" + "d" * 36,
        "github_pat": "github" + "_pat_" + "e" * 50,
        "stripe":    "sk_" + "live_" + "f" * 24,
        "slack":     "xox" + "b-" + "1234567890-abcdef",
        "pem":       "-----BEGIN RSA PRIVATE KEY-----",
        "env_style": "ALPACA_SECRET_KEY=" + "g" * 30,      # unquoted .env shape
        "yaml_style": "api_key: " + "h" * 30,               # YAML shape
        "json_style": '"token": "' + "i" * 30 + '"',        # JSON shape
    }
    for name, sample in samples.items():
        assert any(p.search(sample) for p in PATTERNS), \
            f"no pattern detects a {name}-shaped secret"


def test_patterns_do_not_flag_ordinary_code():
    """Negative control: these must NOT trip, or the scanner gets ignored."""
    benign = [
        "def test_journal_never_breaks_posting():",
        "score_threshold = 0.85  # not a secret",
        "ANTHROPIC_API_KEY  # env var NAME only, no value",
        "os.environ.get('ALPACA_API_KEY')",
        "# password rotation is documented in docs/secrets_rotation.md",
    ]
    for line in benign:
        assert not any(p.search(line) for p in PATTERNS), \
            f"false positive on: {line!r}"
