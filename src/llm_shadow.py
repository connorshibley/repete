"""Shadow judge — runs a SECOND, local-model judgment alongside the real one,
purely for comparison. Never affects trading.

Why this file exists
---------------------
The real judge is `llm.review_signal()`, and its provider is fixed by
`cfg["llm"]["provider"]`. When this was written (2026-08-20) that meant
exclusively "anthropic", and the plan was shadow-first: measure whether a
local model agrees with Claude on real signals BEFORE switching, because
switching blind would be evaluating blind.

CORRECTED 2026-08-30 — history went the other way. Anthropic ran out of
credits on 2026-08-21, the judge was unreachable for seven days, and on
2026-08-28 `llm.provider` was switched to the local model WITHOUT this
baseline (divergence #25 records why waiting was not neutral). So the
comparison this module was built for is now reversed: the LOCAL model is the
live judge, and the missing measurement is how *Claude* would have judged the
same prompts. This module only speaks OpenAI-compatible `/v1/chat/completions`
(`_call_local`), so it cannot itself call Anthropic as the shadow — but the
prompt sidecar (`Ledger._store_prompt`) records every live prompt from
2026-08-31 forward, so when credits return, Claude can be replayed against
those. Either way the scoring in scripts/score_llm_shadow.py applies
unchanged; only which side is "live" flipped.

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

THE PROMPT IS DELEGATED, NOT DUPLICATED (corrected 2026-08-30)
---------------------------------------------------------------
As written on 2026-08-20, `_user_message()` was a deliberate copy of the
f-string then built inline in `llm.review_signal()` — copied because the
author could not run llm.py's test suite from that environment, and flagged
in this very docstring as a drift risk worth a regression test. Step 5a
(2026-08-22) made `llm.review_user_message()` public precisely so a shadow
could delegate, and this session CAN run the suite — so `_user_message` now
delegates, the drift class is structurally gone, and
tests/test_llm_shadow.py pins the delegation so a future copy-paste cannot
quietly reintroduce it.

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
    """The exact user message the real judge is sent — BY DELEGATION.

    Was a byte-identical copy until 2026-08-30 (see the module docstring); a
    shadow scoring a different question than the live judge answers is the
    silent failure this module exists to avoid, and delegation makes it
    impossible rather than merely tested-for."""
    return llm.review_user_message(signal, memory_context)


def _call_local(base_url: str, model: str, system: str, user: str,
                max_tokens: int, timeout: float,
                extra_body: dict | None = None) -> str:
    """One OpenAI-compatible chat completion. stdlib only — no new dependency,
    matching llm_client.py's own choice to import its vendor SDK only inside
    the function that calls out.

    `extra_body` carries server-specific fields, in practice
    `chat_template_kwargs: {enable_thinking: false}`. Without it, a shadow
    pointed at the Bizon's vLLM (served with --reasoning-parser=qwen3) spends
    ~47s per call generating a reasoning trace the extractor then discards,
    against ~4.8s with it — measured, llm_client.thinking_kwargs. At 8-13
    signals a cycle that is the difference between a shadow that fits inside
    the cycle and one that does not.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }
    # Never let an extra field shadow one of the four above: a config typo must
    # not silently re-point the model or lift the token ceiling. Same rule as
    # llm_client._OpenAICompatMessages.
    body.update({k: v for k, v in (extra_body or {}).items() if k not in body})
    payload = json.dumps(body).encode()
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


def candidates(cfg: dict) -> list[dict]:
    """The shadow judges to run, normalised to a list.

    Accepts either shape. A `candidates:` list is the N-way form; a bare
    `base_url`/`model` on the block itself is the original single-shadow form
    that docs/llm_shadow_setup.md documents, kept working so wiring the doc
    verbatim still does something sensible.

    Why N-way at all: comparing two judges needs them scored on the SAME
    signal, and a signal happens once. Re-running a candidate tomorrow
    compares two models on two different market days, which measures the
    market. One row per candidate per signal is the only shape that does not.
    """
    sh = (cfg.get("llm_shadow") or {})
    listed = sh.get("candidates")
    if listed:
        out = []
        for i, c in enumerate(listed):
            merged = {k: v for k, v in sh.items() if k != "candidates"}
            merged.update(c or {})
            merged.setdefault("name", merged.get("model") or f"candidate{i}")
            out.append(merged)
        return out
    if sh.get("base_url") and sh.get("model"):
        single = dict(sh)
        single.setdefault("name", sh["model"])
        return [single]
    return []


def _thinking_body(candidate: dict) -> dict:
    """Reuses llm_client's resolver so the shadow and the live judge cannot
    disagree about what "thinking off" means on the wire."""
    try:
        import llm_client
        return llm_client.thinking_kwargs({"llm": candidate})
    except Exception:  # noqa: BLE001 — a shadow must never break on a helper
        return {}


def review_one(signal, memory_context: str,
               candidate: dict) -> tuple[dict | None, str | None, float]:
    """One candidate's opinion on one signal. Never raises."""
    t0 = time.monotonic()
    try:
        text = _call_local(
            candidate["base_url"], candidate["model"], llm._SYSTEM,
            _user_message(signal, memory_context),
            max_tokens=candidate.get("max_tokens", 4000),
            timeout=candidate.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            extra_body=_thinking_body(candidate))
    except Exception as e:  # noqa: BLE001 — vendor/network failure
        return None, f"call_failed: {e}"[:200], time.monotonic() - t0
    try:
        return _parse_verdict(text), None, time.monotonic() - t0
    except Exception as e:  # noqa: BLE001 — reply arrived, not a usable verdict
        return None, f"parse_failed: {e}"[:200], time.monotonic() - t0


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
    cands = candidates(cfg)
    if not cands:
        return None, "no shadow candidate configured", 0.0
    return review_one(signal, memory_context, cands[0])


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
        # THE JOIN KEY. Scoring a shadow against AGREEMENT only says which
        # model resembles the incumbent; scoring it against OUTCOMES needs the
        # trade this signal became. `llm.review_signal` puts `_prompt` on the
        # review dict and `ledger.log_decision` pops it onto the decision row
        # as `prompt_sha256` — and this hook runs before that pop, so the hash
        # is still here. Same hash on both rows: shadow -> decision ->
        # trade_id -> outcome, exactly, with no (symbol, timestamp) guessing.
        pr = (live_review or {}).get("_prompt") or {}
        prompt_sha = pr.get("prompt_sha256")
        for candidate in candidates(cfg):
            shadow, error, latency = review_one(signal, memory_context,
                                                candidate)
            _write_row(sh, candidate, signal, symbol, live_review, shadow,
                       error, latency, prompt_sha)
    except Exception as e:  # noqa: BLE001 — this function's entire contract is "never raise"
        log.warning("llm_shadow.log_comparison failed (%s) — no row written", e)


def _write_row(sh: dict, candidate: dict, signal, symbol: str,
               live_review: dict, shadow: dict | None, error: str | None,
               latency: float, prompt_sha: str | None) -> None:
    try:
        row = {
            "event": "llm_shadow_comparison",
            # store.py's append() does not stamp a timestamp itself (unlike
            # judgments.JudgmentStore._append, which wraps it) — added here,
            # same convention.
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "action": signal.action,
            "signal_reason": signal.reason,
            "candidate": candidate.get("name"),
            "shadow_model": candidate.get("model"),
            # None = this signal was never judged (a fallback verdict), so
            # there is no decision row to join to. Not an empty string.
            "prompt_sha256": prompt_sha,
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
    except Exception as e:  # noqa: BLE001 — one bad row must not stop the rest
        log.warning("llm_shadow row failed for %s (%s)",
                    candidate.get("name"), e)
