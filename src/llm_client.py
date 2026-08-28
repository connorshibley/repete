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
    # An OpenAI-compatible server the operator runs themselves — vLLM, Ollama,
    # LiteLLM. `key_env: None` is the load-bearing field: it means this
    # provider is KEYLESS, not that its key is unknown. The Bizon serves
    # qwen3.8-27b from a vLLM container on its own docker network, so there is
    # no vendor account, no secret to place in .env, and nothing to rotate.
    #
    # The docstring above this module said a second provider "needs a second
    # dependency and a second API key that this deployment does not have".
    # Both premises stopped being true once the deployment BECAME the box.
    "local": {
        "key_env": None,
        "key_prefix": None,
        "key_len": None,
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


def needs_key(cfg: dict) -> bool:
    """Does the configured provider authenticate with an API key at all?

    False for a self-hosted OpenAI-compatible server. Kept as a named
    predicate rather than an `if provider == "local"` scattered through
    preflight and `configured()`, because the question being asked is about
    the PROVIDER's nature, not its name — a second keyless provider should not
    require finding every place the name was special-cased.
    """
    return provider_spec(cfg).get("key_env") is not None


def thinking_kwargs(cfg: dict) -> dict:
    """Extra request body that turns the local model's hidden reasoning off.

    Measured on the Bizon 2026-08-28, identical real judge prompt, n=3 each:

        enable_thinking unset -> 47.1s, 2261 completion tokens
        enable_thinking false ->  4.8s,  224 completion tokens

    Same verdict both ways. The 2,000 extra tokens are prose the extractor in
    llm.py throws away when it slices `{`..`}` — so the ten-fold latency was
    buying reasoning that NOTHING records: `reasoning_content` came back empty,
    the ledger stores only the visible `reasoning` field, and the prompt hash
    covers the input, not the trace. An audit cannot see it, which is the whole
    argument for not paying for it.

    It also decides whether the judge works at all. At 47s every call exceeded
    `llm.timeout_seconds` (30) and returned a degradation, so the entries the
    judge gates stayed blocked — switching provider WITHOUT this would have
    looked like a fix and changed nothing.

    Tri-state on purpose. `chat_template_kwargs` is a vLLM extension, and a
    different OpenAI-compatible server may reject an unknown field outright:

      None (default) -> send nothing, take the server's own default
      False          -> ask for no reasoning trace
      True           -> ask for one explicitly

    Ignored by the keyed vendor path, which has no such knob.
    """
    raw = (cfg.get("llm") or {}).get("enable_thinking")
    if raw is None:
        return {}
    return {"chat_template_kwargs": {"enable_thinking": bool(raw)}}


def configured(cfg: dict) -> bool:
    """Judge switched on, and reachable.

    For a keyed provider that means a key is present; it still says nothing
    about the key working. For a keyless one, enabled IS configured — demanding
    an absent key would report the judge as unavailable while a perfectly good
    model sits on the same host, and `on_unavailable: block` would then refuse
    every entry for a reason that is not true.
    """
    if not (cfg.get("llm") or {}).get("enabled"):
        return False
    if not needs_key(cfg):
        return True
    return bool(os.environ.get(key_env_var(cfg)))


class _Block:
    """One text block, shaped like the vendor SDK's."""

    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, inp, out):
        self.input_tokens = inp
        self.output_tokens = out
        # Absent, not zero: a server that reports no cache accounting has not
        # told us it read nothing from cache. `_meta` reads these with
        # getattr(.., None) and this repo's standing rule is that absent must
        # never collapse to 0.
        self.cache_read_input_tokens = None
        self.cache_creation_input_tokens = None


class _Message:
    """The subset of the vendor Message that `_text` and `_meta` actually read.

    Deliberately shaped to that subset rather than to the OpenAI response, so
    `complete_detailed` — including its fallback retry — needs no branch at
    all. A conditional in the hot path would be a second place for the two
    providers to drift; an adapter is one place.
    """

    def __init__(self, payload):
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        self.content = [_Block(msg.get("content") or "")]
        self.model = payload.get("model")
        self.id = payload.get("id")
        self.stop_reason = choice.get("finish_reason")
        u = payload.get("usage") or {}
        self.usage = _Usage(u.get("prompt_tokens"), u.get("completion_tokens"))


class _OpenAICompatMessages:
    def __init__(self, base_url, timeout, extra_body=None):
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        # Server-specific request fields (see thinking_kwargs). Held here
        # rather than threaded through create() so every caller — judge,
        # news, learning — gets the same body without knowing it exists.
        self._extra = dict(extra_body or {})

    def create(self, *, model, max_tokens, system, messages):
        """Same signature the Anthropic SDK is called with, above.

        `system` is a top-level argument there and a role here — that mapping
        is the whole adapter, and doing it in one place is why the caller does
        not need to know which provider it is talking to.
        """
        import json as _json
        import urllib.error
        import urllib.request

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}] + list(messages),
        }
        # Never let an extra field shadow one of the three above: a config
        # typo must not be able to silently re-point the model or lift the
        # token ceiling.
        payload.update({k: v for k, v in self._extra.items()
                        if k not in payload})
        body = _json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self._base}/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as r:
                return _Message(_json.loads(r.read()))
        except urllib.error.HTTPError as e:
            # Carry the server's body: vLLM explains a bad model name or an
            # over-long prompt in it, and an HTTPError alone says only "400".
            detail = ""
            try:
                detail = e.read().decode()[:300]
            except Exception:  # noqa: BLE001 — best effort on an error path
                pass
            raise RuntimeError(f"local model server {e.code}: {detail}") from e


class _OpenAICompatClient:
    """Minimal stand-in for the vendor client, for a server we host ourselves."""

    def __init__(self, base_url, timeout, extra_body=None):
        self.messages = _OpenAICompatMessages(base_url, timeout, extra_body)


def base_url(cfg: dict) -> str:
    """Where the self-hosted server lives. Required for a keyless provider."""
    url = (cfg.get("llm") or {}).get("base_url")
    if not url:
        raise RuntimeError(
            "llm.provider is keyless but llm.base_url is unset — a local "
            "provider has no default endpoint to fall back to, and guessing "
            "one would send the judge's prompt somewhere nobody chose")
    return url


def _create(cfg: dict):
    """Construct the vendor client. SDK import is deliberately in here.

    Tests stub `sys.modules["anthropic"]`, which only works while the import is
    executed per call rather than cached at module scope.
    """
    name = provider_name(cfg)
    if name not in PROVIDERS:
        raise RuntimeError(
            f"llm.provider is {name!r} but only {sorted(PROVIDERS)} are "
            f"implemented; add it to llm_client.PROVIDERS and _create() "
            f"before configuring it")
    if not needs_key(cfg):
        return _OpenAICompatClient(base_url(cfg), timeout_seconds(cfg),
                                   thinking_kwargs(cfg))
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
    # THE ONE CASE WHERE 0.0 IS A MEASUREMENT, not the absent-vs-zero collapse
    # this function's docstring forbids. A self-hosted model incurs no vendor
    # charge — that is a known fact, not a missing price. Returning None here
    # would report "we do not know what this cost" about the one call whose
    # cost we know exactly, and a spend total that silently omitted every local
    # call would understate nothing and misattribute everything.
    #
    # It is deliberately NOT a claim that inference is free. Electricity and
    # two A100s are real; they are simply not a per-call vendor line item, and
    # this function prices vendor calls.
    if not needs_key(cfg):
        return 0.0
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
