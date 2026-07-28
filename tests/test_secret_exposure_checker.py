"""A tool that finds leaked secrets must not print them.

Why this file exists
--------------------
`scripts/check_secret_exposure.py` answers "does this key need rotating?" by
searching the session transcripts for the values actually in `.env`. That makes
it, by construction, a program that holds every live secret in memory and prints
a report — so the single most important property is that the report contains no
secret. A leak-finder that echoes what it finds is just a leak with extra steps.

The whole reason the script exists is that the question was previously answered
from memory and got it wrong in BOTH directions: the owner was asked to rotate
an Anthropic key that had never been exposed, and was not told that the Alpaca
pair running production sat in plaintext in a local transcript and had never
been rotated once. The owner is the one who caught it.

`test_no_secret_value_is_ever_printed` is the sharp one here. Everything else
guards against it passing for the wrong reason — a script that printed nothing
at all, or found nothing at all, would satisfy that test trivially, so the
counting and naming tests below are its permissive half.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import check_secret_exposure as chk


# Synthetic values, assembled by concatenation and named without any of the
# words tests/test_secrets_hygiene.py scans for. Same technique that file uses
# on its own patterns, and for the same reason: a fixture in a tracked file
# that LOOKS like `SECRET = "<32 chars>"` trips the committed-secrets guard.
#
# That guard scans `git ls-files`, so it stays quiet until the file is staged —
# the first version of this fixture passed locally and failed in CI, which is
# the guard working correctly and my local run being the incomplete one.
EXPOSED_VALUE = "zz-" + "live-" + "A" * 28
CLEAN_VALUE = "alpaca-" + "B" * 28


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """A .env with one exposed credential, one clean, one empty, one short."""
    p = tmp_path / ".env"
    p.write_text(
        f"ANTHROPIC_API_KEY={EXPOSED_VALUE}\n"
        f"ALPACA_SECRET_KEY={CLEAN_VALUE}\n"
        "X_API_KEY=\n"                       # empty — nothing to look for
        # Below MIN_LEN. Deliberately not an English word: the first version
        # of this fixture used "short", which the no-secret-printed test then
        # flagged because report() writes "too short to search reliably". The
        # assertion was right and the fixture was wrong — a real credential
        # cannot collide with prose, but a toy one can.
        "ALPACA_API_KEY=Zq4Kp\n"
        "LIVE_TRADING_CONFIRMED=NO\n"        # a flag, not a credential
        "# ANTHROPIC_API_KEY=commented-out\n"
    )
    monkeypatch.chdir(tmp_path)
    return p


@pytest.fixture
def transcripts(tmp_path):
    """Two transcripts; EXPOSED_VALUE appears in both (3 times total), CLEAN_VALUE in none."""
    d = tmp_path / "projects" / "some-project"
    d.mkdir(parents=True)
    (d / "a.jsonl").write_text(f'{{"text":"{EXPOSED_VALUE}"}}\n{{"text":"{EXPOSED_VALUE}"}}\n')
    (d / "b.jsonl").write_text(f'{{"text":"prose then {EXPOSED_VALUE} then more"}}\n')
    (d / "c.jsonl").write_text('{"text":"nothing sensitive here"}\n')
    return str(tmp_path / "projects" / "*" / "*.jsonl")


# ---- the property that matters ----

def test_no_secret_value_is_ever_printed(env_file, transcripts, capsys):
    values = chk.load_env()
    chk.report(values, chk.scan(values, transcripts))
    out = capsys.readouterr().out
    for name, value in values.items():
        assert value not in out, f"{name}'s value was printed"
    assert EXPOSED_VALUE not in out and CLEAN_VALUE not in out


def test_the_report_still_says_something(env_file, transcripts, capsys):
    """The permissive half. Without it, a script whose report() did nothing at
    all would pass the test above — the classic way a redaction check passes
    for the wrong reason."""
    values = chk.load_env()
    chk.report(values, chk.scan(values, transcripts))
    out = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY" in out
    assert "ALPACA_SECRET_KEY" in out
    assert "EXPOSED" in out and "clean" in out


# ---- and it counts correctly ----

def test_an_exposed_secret_is_found_in_every_transcript(env_file, transcripts):
    hits = chk.scan(chk.load_env(), transcripts)
    assert hits["ANTHROPIC_API_KEY"] == (2, 3)   # 2 files, 3 occurrences


def test_a_clean_secret_reports_zero(env_file, transcripts):
    hits = chk.scan(chk.load_env(), transcripts)
    assert hits["ALPACA_SECRET_KEY"] == (0, 0)


def test_empty_and_non_credential_values_are_not_watched(env_file):
    values = chk.load_env()
    assert "X_API_KEY" not in values          # empty: nothing to look for
    assert "LIVE_TRADING_CONFIRMED" not in values   # a flag, not a credential


def test_a_commented_line_is_not_read_as_a_value(env_file):
    """`# ANTHROPIC_API_KEY=commented-out` must not override the real one —
    otherwise the checker would search for a placeholder, find nothing, and
    report a live exposed key as clean."""
    assert chk.load_env()["ANTHROPIC_API_KEY"] == EXPOSED_VALUE


def test_a_too_short_value_is_not_searched(env_file, transcripts):
    """A 5-character value would match ordinary prose across the corpus and
    report a frightening number that means nothing."""
    hits = chk.scan(chk.load_env(), transcripts)
    assert hits["ALPACA_API_KEY"] == (0, 0)


# ---- exit codes, so CI and cron can use it ----

def test_exit_code_is_1_when_something_is_exposed(env_file, transcripts, capsys):
    values = chk.load_env()
    assert chk.report(values, chk.scan(values, transcripts)) == 1


def test_exit_code_is_0_when_nothing_is_exposed(env_file, tmp_path, capsys):
    empty = str(tmp_path / "no-such-dir" / "*" / "*.jsonl")
    values = chk.load_env()
    assert chk.report(values, chk.scan(values, empty)) == 0
    assert "No live secret" in capsys.readouterr().out


def test_a_missing_env_file_is_not_a_pass(tmp_path, monkeypatch, capsys):
    """Exiting 0 here is correct — there is nothing to check — but it must SAY
    so rather than printing a clean-looking empty report. 'Nothing found' and
    'nothing looked at' are different facts."""
    monkeypatch.chdir(tmp_path)
    assert chk.main() == 0
    assert "nothing to check" in capsys.readouterr().out
