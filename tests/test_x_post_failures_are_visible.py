"""A post that never went out must not look like a post that did.

Why this file exists
--------------------
`x_poster.post_text` caught every failure, wrote a `log.warning`, appended an
archive row reading `"failed"`, and stopped there. No ledger event — so the
watchdog could not see it, `evidence.py` did not count it, and the degradation
SLO was blind to it.

The cost was measured, not imagined. The four `X_*` values in `.env` were
emptied some time after Friday 2026-07-24. On 2026-07-27 the bot placed ten
trades and **failed to post all ten recaps**, while reporting a clean cycle. The
owner found out by asking whether it had posted.

That is the same defect class as every other one found that day — the Friday
cycle that died silently, the watchdog that could not run, the judge that could
be absent unnoticed. Each was "it failed quietly."

Two failures, deliberately kept apart, because they need different fixes:

  * **no_credentials** — configured ON but unusable. Fix the environment.
  * **failed**         — credentials present, the API rejected or errored.
                         Wait it out, or investigate the account.

The sharpest test here is `test_a_silent_failure_is_impossible`: whatever goes
wrong, something lands in the ledger.
"""
import json
import types

import pytest

import x_poster


@pytest.fixture
def cfg(tmp_path):
    return {
        "x_posting": {"enabled": True, "dry_run": False,
                      "disclose_paper": True,
                      "posts_log_path": str(tmp_path / "posts.jsonl")},
        "memory": {"ledger_path": str(tmp_path / "ledger.jsonl")},
    }


def _events(cfg):
    path = cfg["memory"]["ledger_path"]
    try:
        return [json.loads(l) for l in open(path) if l.strip()]
    except OSError:
        return []


def _degradations(cfg):
    return [r for r in _events(cfg)
            if r.get("type") == "event" and r.get("event") == "degradation"]


def _archived(cfg):
    try:
        return [json.loads(l) for l in open(cfg["x_posting"]["posts_log_path"])
                if l.strip()]
    except OSError:
        return []


def _creds(monkeypatch, value="x" * 20):
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, value)


# ---- empty is not the same as absent ----

def test_empty_credentials_count_as_missing(monkeypatch):
    """The case that actually happened: the names were all still in .env with
    nothing after the `=`, so os.environ returned "" instead of raising."""
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")
    assert x_poster.missing_credentials() == list(x_poster.CREDENTIALS)


def test_whitespace_only_credentials_count_as_missing(monkeypatch):
    _creds(monkeypatch, "   ")
    assert x_poster.missing_credentials() == list(x_poster.CREDENTIALS)


def test_real_looking_credentials_are_not_flagged(monkeypatch):
    """The permissive half — without it a function that flags everything passes
    both tests above."""
    _creds(monkeypatch)
    assert x_poster.missing_credentials() == []


def test_a_single_missing_credential_is_named(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setenv("X_ACCESS_TOKEN", "")
    assert x_poster.missing_credentials() == ["X_ACCESS_TOKEN"]


# ---- the ledger sees it ----

def test_missing_credentials_write_a_degradation_naming_the_variables(
        monkeypatch, cfg):
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")
    x_poster.post_text("hello", cfg)
    degs = _degradations(cfg)
    assert len(degs) == 1
    detail = degs[0]["detail"]
    assert "x_post" in detail
    for k in x_poster.CREDENTIALS:
        assert k in detail, f"{k} not named in the ledger entry"


def test_a_publish_failure_writes_a_degradation(monkeypatch, cfg):
    _creds(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("401 Unauthorized")
    monkeypatch.setattr(x_poster, "_client", _boom)
    x_poster.post_text("hello", cfg)
    degs = _degradations(cfg)
    assert len(degs) == 1
    assert "401 Unauthorized" in degs[0]["detail"]


def test_the_two_failures_are_distinguishable(monkeypatch, cfg):
    """They need different fixes — one is the environment, one is the account.
    A single generic message would send an operator to the wrong place."""
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")
    x_poster.post_text("a", cfg)
    _creds(monkeypatch)
    monkeypatch.setattr(x_poster, "_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    x_poster.post_text("b", cfg)

    statuses = [r["status"] for r in _archived(cfg)]
    assert statuses == ["no_credentials", "failed"]
    details = [d["detail"] for d in _degradations(cfg)]
    assert len(details) == 2 and details[0] != details[1]


def test_a_silent_failure_is_impossible(monkeypatch, cfg):
    """The property that matters. Whatever goes wrong on the way out, the
    ledger records it — because the ten failures on 2026-07-27 recorded
    nothing the watchdog could read."""
    for setup in (
        lambda: [monkeypatch.setenv(k, "") for k in x_poster.CREDENTIALS],
        lambda: (_creds(monkeypatch),
                 monkeypatch.setattr(x_poster, "_client",
                                     lambda: (_ for _ in ()).throw(
                                         RuntimeError("network")))),
    ):
        setup()
        before = len(_degradations(cfg))
        x_poster.post_text("something", cfg)
        assert len(_degradations(cfg)) == before + 1


# ---- and it stays quiet when nothing is wrong ----

def test_a_successful_post_writes_no_degradation(monkeypatch, cfg):
    _creds(monkeypatch)
    monkeypatch.setattr(x_poster, "_client", lambda: types.SimpleNamespace(
        create_tweet=lambda text: types.SimpleNamespace(data={"id": "1"})))
    x_poster.post_text("hello", cfg)
    assert _degradations(cfg) == []
    assert [r["status"] for r in _archived(cfg)] == ["posted"]


def test_dry_run_is_not_a_degradation(monkeypatch, cfg):
    """Choosing not to publish is a decision, not a failure — the same
    distinction llm.py draws for a deliberately disabled judge."""
    cfg["x_posting"]["dry_run"] = True
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")
    x_poster.post_text("hello", cfg)
    assert _degradations(cfg) == []
    assert [r["status"] for r in _archived(cfg)] == ["dry_run"]


def test_posting_disabled_is_not_a_degradation(monkeypatch, cfg):
    cfg["x_posting"]["enabled"] = False
    x_poster.post_text("hello", cfg)
    assert _degradations(cfg) == []


def test_a_broken_ledger_never_breaks_posting(monkeypatch, cfg):
    """An outbound-post problem must never become a trading problem. Failing to
    RECORD the failure is logged and swallowed, like watchdog.main()'s write."""
    cfg["memory"]["ledger_path"] = "/nonexistent-dir/ledger.jsonl"
    for k in x_poster.CREDENTIALS:
        monkeypatch.setenv(k, "")
    x_poster.post_text("hello", cfg)      # must not raise
