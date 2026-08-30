# Wiring in the shadow judge — two edits, both for you to apply

> **CORRECTED 2026-08-30, read this first.** This document was written
> 2026-08-20, when the live judge was Claude and the local model was the
> candidate. On 2026-08-28 the provider switched to the local model *without*
> the shadow baseline (divergence #25 records why), so the roles reversed:
> the local model **is** the live judge, and the missing comparison is how
> Claude would have judged the same prompts. `llm_shadow.py` only speaks
> OpenAI-compatible endpoints, so it cannot run Claude as the shadow — the
> reverse comparison instead comes from replaying the prompt sidecar
> (`Ledger._store_prompt`, recording every live prompt once cycles resume)
> when credits return. The wiring below still works and is still unapplied;
> what it measures today is a *second local model* against the live one,
> which is useful for candidate-testing, not for closing #25.
>
> Two details below were also overtaken: the example endpoint/model (the box
> runs vLLM `qwen3.8-27b` at `http://vllm-qwen38:8000/v1` on `ollama-net`,
> not Ollama on :11434), and a hazard learned in #152: qwen3 served with
> `--reasoning-parser=qwen3` generates a ~47s hidden reasoning trace unless
> the request carries `chat_template_kwargs: {enable_thinking: false}`.
> `_call_local` does not send that field, so shadow calls against that server
> take the slow path — inside this module's 60s timeout, but budget for it.

`src/llm_shadow.py` and `scripts/score_llm_shadow.py` are new files with no existing tests to break, so they were written directly. `main.py` and `config.yaml` are different — `main.py` has 1,234 tests and this repo's own stated rule is "ask Connor, don't just do it" for anything touching what trades. I don't have a way to run your test suite from where I'm working, so these two edits are written out exactly below for you (or a Claude Code session with the full repo in context) to apply and verify with `pytest` before Sunday.

Both edits are small and both are inert until you also set `llm_shadow.enabled: true` — until then this is dead code that changes nothing.

---

## 1. `config.yaml` — add this block next to `llm:`

```yaml
llm_shadow:
  enabled: false          # flip true only once base_url below answers
  base_url: "http://vllm-qwen38:8000/v1"   # (corrected 2026-08-30) the vLLM
                                            # container on ollama-net — the agent
                                            # service already joins that network.
                                            # NOTE: this is also the LIVE judge's
                                            # endpoint now; a shadow pointed at the
                                            # same model as the live judge measures
                                            # sampling noise, not a second opinion.
  model: "qwen3.8-27b"      # the id vLLM SERVES — a name it does not serve is a
                            # 400, not a fallback
  max_tokens: 4000          # same as llm.max_tokens — keep them equal for a fair comparison
  timeout_seconds: 60       # local inference is slower than the cloud call; llm.py bounds
                             # the REAL judge at 30s — do not shrink this to match, it will
                             # just produce spurious shadow timeouts
  log_path: "knowledge/llm_shadow_log.jsonl"   # optional — this is the default if omitted
```

Keep it a **separate top-level block** from `llm:`. (The 2026-08-20 version of this paragraph said `llm:` stays on `anthropic` until the log says otherwise — overtaken on 2026-08-28; see the correction at the top.)

## 2. `main.py` — around your judge call site (line ~1403 as of today; search for `llm.review_signal`)

**Before:**
```python
        review = llm.review_signal(
            sig, memory.context_for_llm(symbol=symbol, regime=market_regime,
                                        strategy=sig.strategy, signal=sig,
                                        positions=positions, account=account)
            + (f"\n\n{extra_context}" if extra_context else ""), cfg)
```

**After:**
```python
        memory_context = (memory.context_for_llm(
            symbol=symbol, regime=market_regime, strategy=sig.strategy,
            signal=sig, positions=positions, account=account)
            + (f"\n\n{extra_context}" if extra_context else ""))
        review = llm.review_signal(sig, memory_context, cfg)

        # --- shadow judge: local-model comparison, logs only, never affects trading ---
        if cfg.get("llm_shadow", {}).get("enabled"):
            try:
                import llm_shadow
                llm_shadow.log_comparison(sig, symbol, memory_context, review, cfg)
            except Exception as e:  # noqa: BLE001 — shadow must never affect the live cycle
                log.warning("llm_shadow comparison failed (%s) — ignored", e)
```

**What actually changed:** the `memory.context_for_llm(...)` call got pulled out into a local variable (`memory_context`) instead of being built inline as an argument expression. That's it for the real judge — `review_signal(sig, memory_context, cfg)` receives byte-identical input to before. The five lines after it are new, fully guarded by the `enabled` flag and their own try/except, and `llm_shadow.log_comparison()` itself never raises (see its docstring) — so this is two guard rails deep against a shadow-judge problem ever reaching the live cycle.

**Before you turn `enabled: true`:**
1. `pytest tests/ -k "llm or main"` — confirm nothing regressed from the `memory_context` extraction. It should be a no-op; if any test fails, that's real signal something about the inline expression's evaluation order or timing mattered, which would be worth knowing regardless of this feature.
2. Confirm Bizon's Ollama is reachable at the `base_url` you set — `curl http://<ip>:11434/v1/models` from the same machine `main.py` runs on.
3. Only then flip `llm_shadow.enabled: true`.

## 3. Reading the results

Once it's been running a while (even a handful of signals is a start):

```bash
python scripts/score_llm_shadow.py
```

Prints format reliability, direction agreement, missed-veto count (the number to actually gate on — see the script's own docstring for why), over-veto count, and latency. That's the table for Sunday, built from real signals rather than a backfill guess.

## 4. Repete1 and Repete2

This was written against `repete` specifically, since that's the one I read. If `repete1` and `repete2` share this same `llm.py` / `judgments.py` structure (forked from the same base, most likely), the same three files should port with only the import paths changing. Worth confirming rather than assuming — say the word and I'll check both repos' `src/llm.py` for how closely they match before porting the patch.
