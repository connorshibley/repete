# Wiring in the shadow judge — two edits, both for you to apply

`src/llm_shadow.py` and `scripts/score_llm_shadow.py` are new files with no existing tests to break, so they were written directly. `main.py` and `config.yaml` are different — `main.py` has 1,234 tests and this repo's own stated rule is "ask Connor, don't just do it" for anything touching what trades. I don't have a way to run your test suite from where I'm working, so these two edits are written out exactly below for you (or a Claude Code session with the full repo in context) to apply and verify with `pytest` before Sunday.

Both edits are small and both are inert until you also set `llm_shadow.enabled: true` — until then this is dead code that changes nothing.

---

## 1. `config.yaml` — add this block next to `llm:`

```yaml
llm_shadow:
  enabled: false          # flip true only once base_url below answers
  base_url: "http://<bizon-tailscale-ip>:11434/v1"   # Ollama's OpenAI-compatible endpoint
  model: "nemotron-3.5-lightning"    # match whatever tag `ollama list` shows on Bizon
  max_tokens: 4000          # same as llm.max_tokens — keep them equal for a fair comparison
  timeout_seconds: 60       # local inference is slower than the cloud call; llm.py bounds
                             # the REAL judge at 30s — do not shrink this to match, it will
                             # just produce spurious shadow timeouts
  log_path: "knowledge/llm_shadow_log.jsonl"   # optional — this is the default if omitted
```

Keep it a **separate top-level block** from `llm:`. Never point `llm.provider` at this — `llm:` stays on `anthropic` until the comparison log has enough signal to trust, which is the whole point of running it as a shadow first.

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
