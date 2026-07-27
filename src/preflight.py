"""Pre-flight validation (2026-07-21, enterprise-hardening pass).

Runs before anything else in the cycle. Polarity is deliberately OPPOSITE the
data-outage convention: data failures degrade gracefully (a quiet morning),
but a misconfigured system must FAIL SAFE — a bot with a mangled risk block
or a corrupted ledger tail must not trade at all. Pure checks, no network.
"""
import json
import os

# Rails where zero is meaningless or dangerous, so the value must be positive.
# min_holding_days at 0 switches off the swing guard (invariant #3);
# daily_loss_limit_pct at 0 switches off the kill switch; either sizing
# percentage at 0 sizes every order to nothing.
REQUIRED_POSITIVE_RISK = (
    "risk_per_trade_pct", "max_position_pct",
    "daily_loss_limit_pct", "min_holding_days",
)

# Rails where 0 legitimately means "disabled" — matching how risk.py itself
# reads them:
#     risk.py:248   if r.get("max_order_value_usd"):        # 0 disables
#     risk.py:693   cfg["risk"].get("max_trades_per_day") or 0
# Still required to be PRESENT and non-negative: a missing key or a negative
# number is a config error; 0 is a deliberate choice.
#
# §29 (2026-07-26) set max_order_value_usd to 0 and taught risk.py and
# backtest.py to read it that way. Nobody told preflight, which still demanded
# a positive number — so from that commit onward every cycle aborted at
# main.py:500 and refused to trade. 861 tests stayed green because not one of
# them ran the SHIPPED config through preflight. That test exists now.
DISABLEABLE_RISK = ("max_order_value_usd", "max_trades_per_day")

REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")

# Shape of a usable Anthropic key. A real one is ~108 chars, one prefix.
# The band is deliberately generous — the point is to catch a paste accident,
# not to pin a vendor format that may change.
_ANTHROPIC_PREFIX = "sk-ant-"
_ANTHROPIC_MIN_LEN = 40
_ANTHROPIC_MAX_LEN = 200


def anthropic_key_shape_fail(value: str) -> str | None:
    """Why this string cannot be an API key, or None if it plausibly is.

    SHAPE ONLY — never a validity check. A revoked, rotated or simply wrong
    key passes this function; only the network can tell those apart, and
    preflight is pure by design (see the module docstring). Read a pass here
    as "nothing was obviously mangled in transit", not "authenticated".

    2026-07-27: setting the key by hand produced FIVE `ANTHROPIC_API_KEY`
    lines in .env — empty, 16 chars, 148, 324 holding three prefixes, and a
    final 216 holding two. python-dotenv takes the last, so the agent held two
    keys concatenated. Preflight said CLEAR TO TRADE, because it asked only
    whether the variable was set. llm.py:112 would have caught the auth error
    and marked every entry `degraded` — correct, but only AFTER the 15:45
    cycle placed unjudged entries at full size.

    The returned text describes the value and never quotes it; log.py redacts
    on the way out, but a diagnosis that needs redacting is the wrong
    diagnosis.
    """
    n = value.count(_ANTHROPIC_PREFIX)
    if n > 1:
        return (f'contains {n} "{_ANTHROPIC_PREFIX}" prefixes (length '
                f'{len(value)}) — looks like several keys pasted end to end; '
                f'keep exactly one')
    if not value.startswith(_ANTHROPIC_PREFIX):
        return (f'does not start with "{_ANTHROPIC_PREFIX}" (length '
                f'{len(value)}) — wrong value pasted into the variable?')
    if not _ANTHROPIC_MIN_LEN <= len(value) <= _ANTHROPIC_MAX_LEN:
        return (f"is {len(value)} characters, outside the plausible "
                f"{_ANTHROPIC_MIN_LEN}-{_ANTHROPIC_MAX_LEN} range — "
                f"truncated or doubled paste?")
    return None


def run(cfg: dict) -> list[str]:
    """All failures found (empty list = clear to trade)."""
    fails: list[str] = []

    r = cfg.get("risk")
    if not isinstance(r, dict):
        fails.append("risk block missing from config")
        r = {}
    for key in REQUIRED_POSITIVE_RISK:
        v = r.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            fails.append(f"risk.{key} missing or not a positive number ({v!r})")
    for key in DISABLEABLE_RISK:
        v = r.get(key)
        if not isinstance(v, (int, float)) or v < 0:
            fails.append(f"risk.{key} missing or negative ({v!r}) — "
                         f"use 0 to disable it, not a negative number")

    if cfg.get("mode", "paper") not in ("paper", "live"):
        fails.append(f"mode must be paper|live, got {cfg.get('mode')!r}")
    if (cfg.get("mode") == "live"
            and os.environ.get("LIVE_TRADING_CONFIRMED", "NO") != "YES"):
        # Broker enforces this too; surfacing it here makes the cycle log say
        # WHY it is still paper instead of silently downgrading.
        fails.append("mode: live without LIVE_TRADING_CONFIRMED=YES in env "
                     "(double interlock) — refusing to run half-configured")

    if cfg.get("strategy", {}).get("timeframe") != "1Day":
        fails.append("strategy.timeframe must stay 1Day (swing-only invariant)")

    # The judge is optional, but claiming to have one and not having one is a
    # misconfiguration — and a quiet one. llm.review_signal() returns a clean
    # `approve` at full size when the key is absent, so trades run UNJUDGED at
    # the size the rules asked for, and evidence.py reports every entry as
    # judged because an llm_review block is present either way. Set
    # llm.enabled: false to trade on rules alone; that is a decision, not a
    # silent hole.
    if cfg.get("llm", {}).get("enabled"):
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            fails.append("llm.enabled: true but ANTHROPIC_API_KEY is not set — "
                         "every trade would be approved unjudged at full size; "
                         "set the key or set llm.enabled: false")
        else:
            # Present is not the same as usable. A mangled key fails at the
            # first API call, by which point the cycle has already traded.
            shape = anthropic_key_shape_fail(key)
            if shape:
                fails.append(f"ANTHROPIC_API_KEY {shape}. The judge would fail "
                             f"on every call and each entry would be approved "
                             f"unjudged at full size. Fix the value in .env "
                             f"(one line, one key) or set llm.enabled: false")

    for key in REQUIRED_ENV:
        if not os.environ.get(key):
            fails.append(f"env {key} missing")

    # The regime block is indexed directly inside the cycle (main.run_cycle ->
    # regime.compute_regime), so a missing key or a nonsensical period is a
    # config error that belongs here rather than as a mid-cycle exception.
    rg = (cfg.get("learning") or {}).get("regime")
    if not isinstance(rg, dict):
        fails.append("learning.regime block missing from config")
    else:
        for key, floor in (("sma_period", 1), ("vol_period", 2)):
            v = rg.get(key)
            if not isinstance(v, int) or v < floor:
                fails.append(f"learning.regime.{key} must be an int >= {floor} "
                             f"({v!r}) — vol_period < 2 divides by zero")

    ledger_path = cfg.get("memory", {}).get("ledger_path", "memory/ledger.jsonl")
    mem_dir = os.path.dirname(ledger_path) or "."
    if os.path.isdir(mem_dir) and not os.access(mem_dir, os.W_OK):
        fails.append(f"memory dir {mem_dir} not writable")
    fails.extend(_ledger_tail_fails(cfg, ledger_path))
    return fails


def _ledger_tail_fails(cfg: dict, ledger_path: str) -> list[str]:
    """Integrity of the LIVE audit trail, whichever backend holds it.

    Preflight deliberately runs before store.configure(), so it must resolve the
    backend itself — reading the raw .jsonl under a sqlite deployment validated
    a file the agent no longer writes. store.read_only_reader() resolves from
    cfg without mutating the process-wide backend and cannot write."""
    try:
        import store as store_mod
        backend, _ = store_mod.backend_kind(cfg)
    except Exception:  # noqa: BLE001 — store unavailable: fall back to the file
        backend = "jsonl"

    if backend == "jsonl":
        tail = _last_nonempty_line(ledger_path)
        if tail is None:
            return []
        try:
            json.loads(tail)
        except ValueError:
            return [f"ledger tail line is not valid JSON "
                    f"(partial write / corruption): {tail[:80]!r}"]
        return []

    try:
        import store as store_mod
        records = store_mod.read_only_reader(cfg, ledger_path).read_all()
    except Exception as e:  # noqa: BLE001
        return [f"ledger unreadable via {backend} backend: {e}"]
    if records and not isinstance(records[-1], dict):
        return [f"ledger tail record is not a JSON object ({backend} backend)"]
    return []


def _last_nonempty_line(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        return lines[-1].decode("utf-8", "replace") if lines else None
    except OSError:
        return None  # no ledger yet — first run is fine
