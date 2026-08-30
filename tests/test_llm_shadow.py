"""The shadow judge: logs-only by contract, and the contract is what's tested.

WHY THIS EXISTS. src/llm_shadow.py sat untracked on one laptop from
2026-08-20 to 2026-08-30 — divergence #25's closure path, existing nowhere
else. Committing it needed the tests its own docstring asked for.

Three properties, in the order they'd hurt:

  1. It never raises into a cycle. The live judge's failure already produced
     one seven-day outage; a COMPARISON tool taking down the cycle would be
     strictly worse than not comparing.
  2. It asks the exact question the live judge answers. As written it carried
     a byte-copy of the prompt with a docstring begging for a drift test;
     it now delegates to llm.review_user_message(), and the test pins the
     delegation so a copy cannot quietly come back.
  3. Disabled means silent. No call, no row, no network.
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm         # noqa: E402
import llm_shadow  # noqa: E402


def _sig():
    return types.SimpleNamespace(
        action="buy", symbol="AAPL", strategy="tsmom",
        reason="tsmom: 20d +6.1%", indicators={"rsi": 58.3})


def _cfg(tmp_path, **over):
    sh = {"enabled": True, "base_url": "http://127.0.0.1:9/v1",
          "model": "qwen3.8-27b", "timeout_seconds": 0.5,
          "log_path": str(tmp_path / "shadow_log.jsonl")}
    sh.update(over)
    return {"llm_shadow": sh}


# --- 2. the same question ---------------------------------------------------

def test_the_shadow_asks_the_live_judges_exact_question():
    """Pinned by VALUE, not by inspecting the source for a call: a future
    byte-copy that drifts by one field must fail here, however implemented."""
    sig, ctx = _sig(), "MEMORY CONTEXT BLOCK"
    assert llm_shadow._user_message(sig, ctx) == llm.review_user_message(sig, ctx)


def test_the_shadow_uses_the_live_system_prompt(monkeypatch, tmp_path):
    """_SYSTEM must be the imported object, verified at the wire: capture the
    request body and compare against llm._SYSTEM."""
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["body"] = json.loads(req.data.decode())

        class _R:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {
                    "content": '{"verdict": "approve", "scale": 1.0}'}}]}).encode()
        return _R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    verdict, err, _ = llm_shadow.shadow_review(_sig(), "ctx", _cfg(tmp_path))
    assert err is None and verdict["verdict"] == "approve"
    assert sent["body"]["messages"][0] == {"role": "system",
                                           "content": llm._SYSTEM}


# --- 1. never raises --------------------------------------------------------

def test_shadow_review_survives_a_dead_endpoint(tmp_path):
    verdict, err, latency = llm_shadow.shadow_review(_sig(), "ctx",
                                                     _cfg(tmp_path))
    assert verdict is None
    assert err and err.startswith("call_failed")
    assert latency >= 0


def test_log_comparison_never_raises_even_when_the_store_breaks(monkeypatch,
                                                                tmp_path):
    """Both layers broken at once — dead endpoint AND a store that raises —
    and the contract must still hold, because main.py's hook is documented
    as safe to call unconditionally.

    The store's failure is a NON-OSError on purpose. A first draft used a
    nonexistent directory, whose FileNotFoundError is an OSError — and a
    mutation narrowing `except Exception` to `except OSError` survived it.
    The store layer's real alternative backend is sqlite, whose
    OperationalError is not an OSError, so the narrow catch would let a
    database hiccup crash the live cycle from inside a logs-only tool."""
    class _Boom:
        def append(self, row):
            raise RuntimeError("sqlite3.OperationalError: database is locked")

    monkeypatch.setattr(llm_shadow.store_mod, "open_store",
                        lambda path: _Boom())
    cfg = _cfg(tmp_path)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx",
                              {"verdict": "approve", "scale": 1.0}, cfg)


def test_a_failed_call_still_writes_a_comparison_row(tmp_path):
    """A shadow that logs only its successes reports a format-reliability
    rate over a denominator it chose — the flattering-instrument failure
    scripts/score_llm_shadow.py exists to avoid (its file-count is the
    denominator, so the failures must be IN the file)."""
    cfg = _cfg(tmp_path)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx",
                              {"verdict": "veto", "scale": 0.0}, cfg)
    rows = [json.loads(ln) for ln in
            open(cfg["llm_shadow"]["log_path"]) if ln.strip()]
    assert len(rows) == 1
    assert rows[0]["shadow_ok"] is False
    assert rows[0]["shadow_error"].startswith("call_failed")
    assert rows[0]["live_verdict"] == "veto"


# --- 3. disabled means silent ----------------------------------------------

def test_disabled_makes_no_call_and_writes_no_row(monkeypatch, tmp_path):
    def explode(*a, **k):
        raise AssertionError("disabled shadow opened a connection")

    monkeypatch.setattr("urllib.request.urlopen", explode)
    cfg = _cfg(tmp_path, enabled=False)
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx",
                              {"verdict": "approve", "scale": 1.0}, cfg)
    assert not Path(cfg["llm_shadow"]["log_path"]).exists()


def test_missing_shadow_block_is_disabled_not_a_crash(tmp_path):
    llm_shadow.log_comparison(_sig(), "AAPL", "ctx",
                              {"verdict": "approve", "scale": 1.0}, {})


# --- the parse applies the live judge's own constraints ---------------------

def test_parse_clamps_scale_through_the_shared_enforcement_point():
    """Invariant #2 has one home (llm._clamp_scale); the shadow must go
    through it, or a comparison could record a shadow verdict the live judge
    could never emit."""
    v = llm_shadow._parse_verdict('{"verdict": "downsize", "scale": 3.7}')
    assert v["scale"] == 1.0


def test_parse_rejects_an_out_of_set_verdict():
    import pytest
    with pytest.raises(ValueError):
        llm_shadow._parse_verdict('{"verdict": "maybe", "scale": 0.5}')
