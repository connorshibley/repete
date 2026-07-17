"""Optional LLM judgment layer (Anthropic API).

Design constraint from the research ('Agents Are Not Algorithms', the
Agentic Trading survey): the LLM is a JUDGMENT layer, not a signal
generator or executor. It may only:
    - APPROVE a signal as-is
    - DOWNSIZE it (scale 0.1–1.0)
    - VETO it (with a reason)
It can never invent a trade, enlarge one, or touch risk limits.
If no API key is configured (or the call fails), signals pass through
approved — the bot degrades gracefully to pure rules.
"""
import json
import logging
import os

log = logging.getLogger("llm")


def _msg_text(msg) -> str:
    """Join text blocks; reasoning models prepend thinking blocks with no .text."""
    return "".join(b.text for b in msg.content
                   if getattr(b, "type", "") == "text").strip()

_SYSTEM = """You are the risk-review layer of an automated PAPER trading bot.
A deterministic strategy produced a trade signal. Your ONLY job is to sanity-check it
against recent performance memory and reply with strict JSON:
{"verdict": "approve" | "downsize" | "veto", "scale": <0.1-1.0>, "reasoning": "<2-3 sentences>"}

Rules:
- You may not propose different trades or symbols.
- "downsize" must include scale < 1.0. "approve" means scale 1.0.
- Veto only with a concrete reason grounded in the memory or the signal itself.
- Be skeptical of patterns from fewer than ~30 trades; do not overfit to recent results.
- Note: memory samples intentionally include losing trades; do not assume the strategy is
  better than the sample shows."""


def review_signal(signal, memory_context: str, cfg: dict) -> dict:
    fallback = {"verdict": "approve", "scale": 1.0,
                "reasoning": "LLM review disabled/unavailable — rule-based execution."}
    if not cfg["llm"]["enabled"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=cfg["llm"]["model"],
            max_tokens=cfg["llm"]["max_tokens"],
            system=_SYSTEM,
            messages=[{"role": "user", "content":
                       f"SIGNAL: {signal.action.upper()} {signal.symbol}\n"
                       f"STRATEGY REASON: {signal.reason}\n"
                       f"INDICATORS: {json.dumps(signal.indicators)}\n\n"
                       f"{memory_context}\n\nReply with JSON only."}],
        )
        text = _msg_text(msg)
        start, end = text.find("{"), text.rfind("}") + 1
        verdict = json.loads(text[start:end])
        # Clamp: the LLM can only reduce, never enlarge.
        verdict["scale"] = min(max(float(verdict.get("scale", 1.0)), 0.0), 1.0)
        if verdict.get("verdict") not in ("approve", "downsize", "veto"):
            return fallback
        return verdict
    except Exception as e:  # noqa: BLE001 — any LLM failure degrades to rules
        log.warning("LLM review failed (%s) — proceeding rule-based", e)
        return fallback


def write_x_post(trade: dict, cfg: dict) -> str | None:
    """Draft a trade-recap post. Returns None if LLM unavailable (caller uses template)."""
    if not cfg["llm"]["enabled"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=cfg["llm"]["model"],
            max_tokens=2000,  # roomy: reasoning models spend thinking tokens from this budget
            system=("Write a single tweet (<270 chars) recapping an automated PAPER trade. "
                    "Plain, honest, no hype, no financial advice, no emojis. Include the "
                    "reasoning in one clause. It MUST mention this is paper trading."),
            messages=[{"role": "user", "content": json.dumps(trade)}],
        )
        return _msg_text(msg).strip('"')[:275]
    except Exception as e:  # noqa: BLE001
        log.warning("LLM post drafting failed (%s) — using template", e)
        return None


_DAILY_SYSTEMS = {
    "plan": ("Write a single tweet (<270 chars): a pre-market note from an "
             "automated PAPER swing-trading bot. List the setups it is "
             "watching (symbol + one-word reason) and the market regime. "
             "Plain, honest, no hype, no emojis, no predictions, no advice. "
             "MUST say it is paper trading and that decisions happen at the "
             "3:45 PM ET cycle."),
    "review": ("Write a single tweet (<270 chars): an end-of-day review from "
               "an automated PAPER swing-trading bot. Summarize what it did "
               "(trades, vetoes, holds) and current equity. Plain, honest, "
               "no hype, no emojis, no advice. MUST say it is paper trading."),
}


def write_daily_post(kind: str, facts: dict, cfg: dict) -> str | None:
    """Draft the morning-plan or evening-review post from a facts dict.
    Returns None if the LLM is unavailable (caller uses its template)."""
    if not cfg["llm"]["enabled"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=cfg["llm"]["model"], max_tokens=2000,
            system=_DAILY_SYSTEMS[kind],
            messages=[{"role": "user", "content": json.dumps(facts)}],
        )
        return _msg_text(msg).strip('"')[:275] or None
    except Exception as e:  # noqa: BLE001
        log.warning("LLM daily-post drafting failed (%s) — using template", e)
        return None


def _json_call(cfg: dict, max_tokens: int, system: str, user: str):
    """Shared strict-JSON call: returns the parsed object/array or None."""
    if not cfg["llm"]["enabled"] or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=cfg["llm"]["model"], max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": user}],
        )
        text = _msg_text(msg)
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0),
                    default=-1)
        end = max(text.rfind("}"), text.rfind("]")) + 1
        return json.loads(text[start:end])
    except Exception as e:  # noqa: BLE001 — learning pauses, cycle continues
        log.warning("LLM JSON call failed (%s)", e)
        return None


_LESSON_SYSTEM = """You review closed trades for an automated PAPER trading strategy.
From ONE closed trade, write ONE cautious, FALSIFIABLE hypothesis to watch. Strict JSON:
{"hypothesis": "<one sentence, phrased as 'possible pattern (n=1): ...'>",
 "scope": {"symbols": ["<SYM>"] | null, "regime": "<the trade's regime>" | null,
           "direction": "buy" | null, "strategy": "<the trade's strategy>" | null}}

Rules:
- Never generalize a single trade into a rule; it is n=1 evidence, nothing more.
- Scope must be NO BROADER than what this one trade shows (its symbol, its regime,
  its strategy).
- The hypothesis must be checkable against a future closed trade's fields
  (symbol, exit_reason, pnl_pct, regime, strategy). No vague advice, no advice
  to trade more."""


def generate_lesson_structured(closed_trade: dict, memory_context: str,
                               cfg: dict) -> dict | None:
    """Structured falsifiable hypothesis from a CLOSED trade (embargo respected).
    Returns {"hypothesis", "scope"} or None."""
    out = _json_call(cfg, 1500, _LESSON_SYSTEM,
                     f"CLOSED TRADE: {json.dumps(closed_trade)}\n\n{memory_context}")
    if not out or not isinstance(out, dict) or not out.get("hypothesis"):
        return None
    scope = out.get("scope") or {}
    return {"hypothesis": str(out["hypothesis"])[:400],
            "scope": {k: scope.get(k)
                      for k in ("symbols", "regime", "direction", "strategy")}}


_EVAL_SYSTEM = """You audit hypotheses for an automated PAPER trading bot. Given ONE closed
trade and a list of hypotheses, decide for EACH whether this trade's outcome SUPPORTS it,
CONTRADICTS it, or is UNRELATED. Strict JSON array only:
[{"lesson_id": "...", "relation": "supports" | "contradicts" | "unrelated",
  "reason": "<one sentence>"}]

Rules:
- A trade is evidence only if it falls INSIDE the hypothesis scope (symbol, direction,
  regime). Out of scope = "unrelated". When in doubt, "unrelated" — do not stretch.
- One trade is weak evidence (n=1). Never treat a single trade as proof either way.
- Judge only what each hypothesis actually claims. Do not invent new hypotheses."""


def evaluate_trade_vs_lessons(closed_trade: dict, lessons: list[dict],
                              cfg: dict) -> list[dict] | None:
    """One call batching all live lessons against one closed trade.
    Returns validated [{"lesson_id","relation","reason"}] or None on failure."""
    if not lessons:
        return []
    listing = [{"lesson_id": s["id"], "hypothesis": s["hypothesis"],
                "scope": s["scope"]} for s in lessons]
    out = _json_call(cfg, 3000, _EVAL_SYSTEM,
                     f"CLOSED TRADE: {json.dumps(closed_trade)}\n\n"
                     f"HYPOTHESES: {json.dumps(listing)}")
    if not isinstance(out, list):
        return None
    known = {s["id"] for s in lessons}
    return [r for r in out
            if isinstance(r, dict) and r.get("lesson_id") in known
            and r.get("relation") in ("supports", "contradicts", "unrelated")]


_MERGE_SYSTEM = """You maintain a hypothesis book for a PAPER trading bot. Given the ACTIVE
and CANDIDATE hypotheses with evidence counts, propose AT MOST ONE merge of near-duplicates.
Strict JSON: {"merge": null} or
{"merge": {"source_ids": ["ls-...", "ls-..."], "hypothesis": "<combined>",
           "scope": {"symbols": [...] | null, "regime": "..." | null, "direction": "..." | null}}}

Rules:
- Merge ONLY near-duplicates: the same claim in the same scope family.
- NEVER generalize (e.g. "tech stocks" from one AAPL + one MSFT lesson is FORBIDDEN).
- The combined hypothesis must be no broader than the narrowest source.
- Evidence is the UNION of the sources' counts — claim nothing the counts don't support.
- When unsure: {"merge": null}."""


def propose_merge(lessons: list[dict], cfg: dict) -> dict | None:
    """Meta-review: at most one near-duplicate merge proposal, or None."""
    listing = [{"lesson_id": s["id"], "status": s["status"],
                "hypothesis": s["hypothesis"], "scope": s["scope"],
                "n_supports": len(s["supports"]),
                "n_contradicts": len(s["contradicts"])} for s in lessons]
    out = _json_call(cfg, 2000, _MERGE_SYSTEM, json.dumps(listing))
    if not isinstance(out, dict):
        return None
    merge = out.get("merge")
    if not merge or not isinstance(merge, dict):
        return None
    ids = merge.get("source_ids") or []
    known = {s["id"] for s in lessons}
    if len(ids) < 2 or not set(ids) <= known or not merge.get("hypothesis"):
        return None
    return merge
