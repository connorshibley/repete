"""Shadow judge — runs a SECOND, local-model judgment alongside the real one,
purely for comparison. Never affects trading.

Why this file exists
---------------------
The real judge is `llm.review_signal()`, and its provider is fixed by
`cfg["llm"]["provider"]` — today, exclusively "anthropic" (llm_client.py
refuses any other name outright). Swapping that provider to a local model
without first knowing whether the local model agrees with Claude on real
signals would be evaluating blind.

This module makes a SEPARATE call to a local model server (Ollama / vLLM,
OpenAI-compatible `/v1/chat/completions`) on the SAME signal, using the SAME
system prompt Claude sees (`llm._SYSTEM`, imported not duplicated, so the two
can never drift on the rules that matter). The result:
  - is never returned to main.py's trading path
  - never sizes, vetoes, or otherwise touches an order
  - is only ever appended to its own comparison log (`log_comparison`, below),
    completely separate from `memory.judgments` / the trade ledger — this file
    does not touch the judgment-store schema or the calibration math in
    judgments.py at all.

WHAT IS DUPLICATED, ON PURPOSE
-------------------------------
`_user_message()` below is a deliberate copy of the f-string built inline in
`llm.review_signal()`. This file does not edit llm.py, because llm.py's test
suite (tests/test_llm_parsing.py, tests/test_llm_client.py) could not be run
from the environment this file was written in, and a production judge module
should not be modified by anyone who cannot verify the change against its own
tests. If `review_signal()`'s user message changes, update `_user_message()`
here to match — otherwise the shadow comparison silently starts scoring a
different question than the real judge answers. Worth a regression test in
tests/ once this lands (assert the two strings match for a shared fixture
signal) — not added here for the same reason.

HOW THIS GETS WIRED IN
------------------------
Off by default (`llm_shadow.enabled: false`) and inert until two things
happen, neither done by this file — see docs/llm_shadow_setup.md for both,
written so a human (you, or a Claude Code session with the repo and test
suite in context) applies and verifies them with pytest before this runs
against a real cycle:
  1. config.yaml gets an `llm_shadow:` block (separate from `llm:` — never
     point `llm.provider` at this; `llm:` stays on Anthropic until the
     comparison log says otherwise)
  2. main.py's judge call site gets a 5-line guarded hook calling
     `log_comparison()` right after the real `review_signal()` call
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import llm      # for _SYSTEM and _clamp_scale — the two things that MUST
                # match the real judge; everything else here is independent.
import store as store_mod

log = logging.getLogger("llm_shadow")

DEFAULT_TIMEOUT_SECONDS = 60.0  # local inference is typically slower than
                                  # the cloud call llm_client bounds at 30s —
                                  # do not shrink this to match; it will just
                                  # produce spurious shadow timeouts

_LOG_PATH = "knowledge/llm_shadow_log.jsonl"  # matches the knowledge/ home
                                                # judge_calibration.json already
                                                # uses; change here only, see
                                                # docs/llm_shadow_setup.md


def _user_message(signal, memory_context: str) -> str:
    """MUST match the user message built inline in llm.review_signal().
    Duplicated, not imported — see this module's docstring for why."""
    return (f"SIGNAL: {signal.action.upper()} {signal.symbol}\n"
            f"STRATEGY REASON: {signal.reason}\n"
            f"INDICATORS: {json.dumps(signal.indicators)}\n\n"
            f"{memory_context}\n\nReply with JSON only.")


def _call_local(base_url: str, model: str, system: str, user: str,
                max_tokens: int, timeout: float) -> str:
    """One OpenAI-compatible chat completion. stdlib only — no new dependency,
    matching llm_client.py's own choice to import its vendor SDK only inside
    the function that calls out."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    return body["choices"][0]["message"]["content"]


def _parse_verdict(text: str) -> dict:
    """Same parse + clamp + field-sanitize sequence as llm.review_signal(),
    applied to the shadow model's reply. Raises on anything unusable — the
    caller (shadow_review) is the one place that turns that into "no
    comparison data" rather than a crash."""
    start, end = text.find("{"), text.rfind("}") + 1
    verdict = json.loads(text[start:end])
    verdict = llm._clamp_scale(verdict)  # same enforcement point as the real judge
    if verdict.get("verdict") not in ("approve", "downsize", "veto"):
        raise ValueError(f"unknown verdict {verdict.get('verdict')!r}")
    cited = verdict.get("cited_lessons")
    verdict["cited_lessons"] = ([str(c) for c in cited if isinstance(c, str)][:5]
                                if isinstance(cited, list) else [])
    for side in ("bull_case", "bear_case"):
        verdict[side] = str(verdict.get(side) or "")[:300]
    try:
        conf = verdict.get("confidence")
        verdict["confidence"] = (min(max(float(conf), 0.0), 1.0)
                                 if conf is not None else None)
    except (TypeError, ValueError):
        verdict["confidence"] = None
    return verdict


def shadow_review(signal, memory_context: str, cfg: dict) -> tuple[dict | None, str | None, float]:
    """Best-effort second opinion from a local model.

    NEVER raises — a broken or unreachable local model must be invisible to
    the live cycle, the same contract llm.review_signal() follows for the
    real judge. Unlike the real judge, an unparseable or unreachable shadow
    call gets no permissive fallback verdict: it has no trading consequence
    to protect by defaulting to "approve", so failure just means "no
    comparison data for this signal."

    Returns (verdict_or_None, error_or_None, latency_seconds).
    """
    sh = (cfg.get("llm_shadow") or {})
    t0 = time.monotonic()
    try:
        text = _call_local(
            sh["base_url"], sh["model"], llm._SYSTEM,
            _user_message(signal, memory_context),
            max_tokens=sh.get("max_tokens", 4000),
            timeout=sh.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    except Exception as e:  # noqa: BLE001 — vendor/network failure, mirrors llm.py's split
        return None, f"call_failed: {e}"[:200], time.monotonic() - t0

    try:
        verdict = _parse_verdict(text)
    except Exception as e:  # noqa: BLE001 — reply arrived, not a usable verdict
        return None, f"parse_failed: {e}"[:200], time.monotonic() - t0

    return verdict, None, time.monotonic() - t0


def log_comparison(signal, symbol: str, memory_context: str, live_review: dict,
                   cfg: dict) -> None:
    """Run the shadow judge and append one comparison row. Swallows every
    exception itself — this is the function main.py's hook calls, and it must
    be safe to call unconditionally once `llm_shadow.enabled` is true without
    a caller-side try/except (main.py's hook still wraps it anyway, per
    docs/llm_shadow_setup.md, as defense in depth — this repo's own pattern
    throughout llm.py is never trust a single guard rail alone)."""
    sh = (cfg.get("llm_shadow") or {})
    if not sh.get("enabled"):
        return
    try:
        shadow, error, latency = shadow_review(signal, memory_context, cfg)
        row = {
            "event": "llm_shadow_comparison",
            # store.py's append() does not stamp a timestamp itself (unlike
            # judgments.JudgmentStore._append, which wraps it) — added here,
            # same convention.
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": signal.action,
            "signal_reason": signal.reason,
            "shadow_model": sh.get("model"),
            "shadow_ok": shadow is not None,
            "shadow_error": error,
            "shadow_latency_seconds": round(latency, 2),
            "live_verdict": live_review.get("verdict"),
            "live_scale": live_review.get("scale"),
            "live_confidence": live_review.get("confidence"),
            "live_reasoning": live_review.get("reasoning", "")[:300],
            "shadow_verdict": (shadow or {}).get("verdict"),
            "shadow_scale": (shadow or {}).get("scale"),
            "shadow_confidence": (shadow or {}).get("confidence"),
            "shadow_reasoning": (shadow or {}).get("reasoning", "")[:300] if shadow else None,
        }
        store_mod.open_store(sh.get("log_path", _LOG_PATH)).append(row)
    except Exception as e:  # noqa: BLE001 — this function's entire contract is "never raise"
        log.warning("llm_shadow.log_comparison failed (%s) — no row written", e)
