"""One place that knows how to talk to a model vendor.

Until 2026-07-27 `llm.py` constructed `anthropic.Anthropic()` in five separate
places and `preflight.py` hard-coded the string `sk-ant-`. That is fine right
up until the day one vendor is unavailable — and on 2026-07-27 a single mangled
key silenced the judge for the whole system, which is the same failure with a
different cause.

This module is the seam, not the second vendor. It centralises:

  * which provider is configured, and which env var holds its key
  * what that provider's key is shaped like (used by preflight)
  * the single call path every caller in llm.py goes through

Adding a second vendor means adding an entry to PROVIDERS and one branch in
`_create()`. It is deliberately NOT done here: a second provider needs a second
dependency and a second API key that this deployment does not have, and
shipping an untested fallback path would be worse than having none.

Nothing at module scope imports a vendor SDK. `preflight.py` is documented pure
(no network, no heavy imports) and imports this module, so the SDK import stays
inside the function that actually calls out.
"""
import logging
import os

log = logging.getLogger("llm_client")

DEFAULT_PROVIDER = "anthropic"

# Wall-clock ceiling on ONE judge call, seconds.
#
# The SDK's own default is 600s. That is reasonable for a batch script and
# dangerous inside a trading cycle: the judge runs once per actionable signal,
# so a hung vendor could hold the 15:45 cycle for ten minutes PER SIGNAL and
# push execution past the close — or past the point where the price the signal
# was computed on still means anything.
#
# 30s is generous against observed latency (a ~1k-token verdict returns in
# single-digit seconds) and bounds the damage. The SDK also retries internally,
# so the true worst case per signal is roughly this value times (1 +
# max_retries); the point is that it is SHORT AND BOUNDED, not that it is
# exact. Override with `llm.timeout_seconds` when a slower model is configured.
DEFAULT_TIMEOUT_SECONDS = 30.0

# Per-provider facts. `key_len` is a deliberately generous band: the point is to
# catch a paste accident (truncated, doubled, wrong variable), not to pin a
# vendor format that may change without notice.
PROVIDERS = {
    "anthropic": {
        "key_env": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
        "key_len": (40, 200),
    },
}


def provider_name(cfg: dict) -> str:
    """Configured provider, defaulting to anthropic for every existing config."""
    return (cfg.get("llm") or {}).get("provider") or DEFAULT_PROVIDER


def provider_spec(cfg: dict) -> dict:
    """Facts for the configured provider. Unknown names fall back to the
    default rather than raising — an unknown provider is preflight's problem to
    report, not a crash inside the cycle."""
    return PROVIDERS.get(provider_name(cfg), PROVIDERS[DEFAULT_PROVIDER])


def key_env_var(cfg: dict) -> str:
    return provider_spec(cfg)["key_env"]


def key_shape_fail(value: str, spec: dict | None = None) -> str | None:
    """Why this string cannot be an API key for `spec`, or None if it plausibly is.

    SHAPE ONLY — never a validity check. A revoked, rotated or simply wrong key
    passes this function; only the network can tell those apart. Read a pass as
    "nothing was obviously mangled in transit", not "authenticated".

    The returned text describes the value and never quotes it. A diagnosis that
    has to be redacted on its way to a log is the wrong diagnosis.
    """
    spec = spec or PROVIDERS[DEFAULT_PROVIDER]
    prefix = spec["key_prefix"]
    lo, hi = spec["key_len"]
    n = value.count(prefix)
    if n > 1:
        return (f'contains {n} "{prefix}" prefixes (length {len(value)}) — '
                f'looks like several keys pasted end to end; keep exactly one')
    if not value.startswith(prefix):
        return (f'does not start with "{prefix}" (length {len(value)}) — '
                f'wrong value pasted into the variable?')
    if not lo <= len(value) <= hi:
        return (f"is {len(value)} characters, outside the plausible {lo}-{hi} "
                f"range — truncated or doubled paste?")
    return None


def timeout_seconds(cfg: dict) -> float:
    """Per-call wall-clock ceiling. Falls back to DEFAULT_TIMEOUT_SECONDS.

    A non-positive or unparseable value falls back rather than raising: an
    unbounded judge call is the failure this exists to prevent, so a typo in
    config must not be able to reinstate one.
    """
    raw = (cfg.get("llm") or {}).get("timeout_seconds")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        val = float(raw)
    except (TypeError, ValueError):
        log.warning("llm.timeout_seconds is not a number — using %ss",
                    DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS
    if val <= 0:
        log.warning("llm.timeout_seconds must be positive — using %ss",
                    DEFAULT_TIMEOUT_SECONDS)
        return DEFAULT_TIMEOUT_SECONDS
    return val


def configured(cfg: dict) -> bool:
    """Judge switched on AND a key present. Says nothing about the key working."""
    return bool((cfg.get("llm") or {}).get("enabled")
                and os.environ.get(key_env_var(cfg)))


def _create(cfg: dict):
    """Construct the vendor client. SDK import is deliberately in here.

    Tests stub `sys.modules["anthropic"]`, which only works while the import is
    executed per call rather than cached at module scope.
    """
    name = provider_name(cfg)
    if name != "anthropic":
        raise RuntimeError(
            f"llm.provider is {name!r} but only 'anthropic' is implemented; "
            f"add it to llm_client.PROVIDERS and _create() before configuring it")
    import anthropic
    # Timeout is set on the CLIENT, not per request, so it covers the fallback
    # retry in `complete()` too. A timeout applied to only the first of two
    # calls is the kind of gap that reads as fixed and is not.
    return anthropic.Anthropic(timeout=timeout_seconds(cfg))


def _text(msg) -> str:
    """Join text blocks; reasoning models prepend thinking blocks with no .text."""
    return "".join(b.text for b in msg.content
                   if getattr(b, "type", "") == "text").strip()


def _meta(msg, requested: str, served_by_fallback: bool) -> dict:
    """What the vendor's Message carries that complete() used to throw away.

    `model` is the SERVED id — what the vendor actually ran — and it can
    differ from `requested` in two ways: an alias like "claude-sonnet-5"
    resolves to a dated snapshot, and the fallback retry below swaps models
    outright. Before 2026-08-22 the ledger could not tell which model judged a
    trade; the moment `fallback_model` is set, every calibration silently
    mixes two models. Recording the served id is what makes that visible.

    Cache fields are Optional in the SDK and read with getattr(.., None):
    absent must never collapse to 0 — this repo's standing rule.
    """
    usage = getattr(msg, "usage", None)
    return {
        "model": getattr(msg, "model", None),
        "requested_model": requested,
        "fell_back": served_by_fallback,
        "message_id": getattr(msg, "id", None),
        "stop_reason": getattr(msg, "stop_reason", None),
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
    }


def estimate_cost_usd(cfg: dict, meta: dict) -> float | None:
    """USD for one call from `llm.pricing` in config.yaml. None when unpriced.

    None, never 0.0. A model missing from the price table is "we do not know
    what this cost", and 0.0 would read as "free" — the same absent-vs-zero
    confusion the benchmark fields, fill quality and Step 3c's Sharpe all
    refuse. Prices live in config with a verified-on date, not in code: a
    stale table producing confident wrong numbers is worse than None.
    """
    table = ((cfg.get("llm") or {}).get("pricing") or {})
    row = table.get(meta.get("model") or "") or table.get(meta.get("requested_model") or "")
    if not row:
        return None
    try:
        inp = meta.get("input_tokens"); out = meta.get("output_tokens")
        if inp is None or out is None:
            return None
        cost = (inp * float(row["input_per_mtok"])
                + out * float(row["output_per_mtok"])) / 1_000_000
        cr = meta.get("cache_read_input_tokens") or 0
        if cr and "cache_read_per_mtok" in row:
            cost += cr * float(row["cache_read_per_mtok"]) / 1_000_000
        return round(cost, 6)
    except (KeyError, TypeError, ValueError):
        return None


def complete_detailed(cfg: dict, system: str, user: str, max_tokens: int,
                      model: str | None = None) -> tuple[str, dict]:
    """One completion: (text, meta). Raises on failure — callers decide.

    Every caller in llm.py already wraps its call in a try/except that degrades
    to rules or a template, and those handlers carry the reasoning about what a
    failure means in that specific context. Swallowing exceptions here would
    move that decision to the wrong layer, so this function does not catch.

    `llm.fallback_model` retries ONCE on a different model from the same vendor
    — useful when a model is overloaded or retired, useless when the key is the
    problem. It is unset by default: a quietly weaker judge is a trading
    behaviour change, and this module's job is to make that switch possible, not
    to make it silently. `meta["fell_back"]` is how the ledger sees it happen.
    """
    client = _create(cfg)
    primary = model or cfg["llm"]["model"]
    try:
        msg = client.messages.create(
            model=primary, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return _text(msg), _meta(msg, primary, False)
    except Exception as e:  # noqa: BLE001 — re-raised below if there is no fallback
        fallback = (cfg.get("llm") or {}).get("fallback_model")
        if not fallback or fallback == primary:
            raise
        log.warning("model %s failed (%s) — retrying once on %s",
                    primary, e, fallback)
        msg = client.messages.create(
            model=fallback, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return _text(msg), _meta(msg, primary, True)


def complete(cfg: dict, system: str, user: str, max_tokens: int,
             model: str | None = None) -> str:
    """Text only. A thin wrapper so the five existing callers in llm.py are
    untouched; only review_signal moves to complete_detailed, because only
    the judge's calls need to be reconstructible and priced."""
    return complete_detailed(cfg, system, user, max_tokens, model)[0]
