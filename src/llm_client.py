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


def complete(cfg: dict, system: str, user: str, max_tokens: int,
             model: str | None = None) -> str:
    """One completion, returned as text. Raises on failure — callers decide.

    Every caller in llm.py already wraps its call in a try/except that degrades
    to rules or a template, and those handlers carry the reasoning about what a
    failure means in that specific context. Swallowing exceptions here would
    move that decision to the wrong layer, so this function does not catch.

    `llm.fallback_model` retries ONCE on a different model from the same vendor
    — useful when a model is overloaded or retired, useless when the key is the
    problem. It is unset by default: a quietly weaker judge is a trading
    behaviour change, and this module's job is to make that switch possible, not
    to make it silently.
    """
    client = _create(cfg)
    primary = model or cfg["llm"]["model"]
    try:
        msg = client.messages.create(
            model=primary, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return _text(msg)
    except Exception as e:  # noqa: BLE001 — re-raised below if there is no fallback
        fallback = (cfg.get("llm") or {}).get("fallback_model")
        if not fallback or fallback == primary:
            raise
        log.warning("model %s failed (%s) — retrying once on %s",
                    primary, e, fallback)
        msg = client.messages.create(
            model=fallback, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}])
        return _text(msg)
