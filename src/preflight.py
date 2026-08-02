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

# Rails expressed as a PERCENTAGE OF EQUITY. Every check above this line asks
# whether a number is present and non-negative; none of them asks whether it is
# survivable, so until 2026-08-02 `risk_per_trade_pct: 150` and
# `max_position_pct: 500` both passed preflight and shipped straight into the
# sizing arithmetic.
#
# THE BOUND IS DELIBERATELY LOOSE, and that is the design, not a compromise.
# This repo's owner runs values other people would call reckless on purpose and
# with evidence (§29 raised per-trade risk 1.0 -> 8.0 and moved the drawdown
# tolerance to 10pp, both recorded as owner decisions). Preflight has no
# business relitigating that. What it CAN say without an opinion is that a
# percentage of equity above 100 is not aggressive, it is incoherent: you
# cannot risk more than the account on one trade, hold more than the whole
# account in one name, or fall further than 100% from a peak. Those are
# arithmetic facts, not risk preferences.
#
# So the ceiling catches the failure that actually happens — a missing decimal
# point or a doubled keystroke (8.0 -> 80 is NOT caught; 8.0 -> 800 is) — while
# leaving every coherent value, however bold, to the owner.
PERCENT_OF_EQUITY_RAILS = (
    "risk_per_trade_pct", "max_position_pct", "daily_loss_limit_pct",
    "max_drawdown_pct", "max_portfolio_heat_pct",
)
PERCENT_CEILING = 100.0

REQUIRED_ENV = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")

def anthropic_key_shape_fail(value: str) -> str | None:
    """Why this string cannot be an Anthropic API key, or None if it plausibly is.

    The rule itself lives in `llm_client.key_shape_fail`, so preflight and the
    call path cannot disagree about what a key looks like. That is the §29
    lesson: preflight and risk.py read `max_order_value_usd: 0` differently and
    every cycle silently aborted for a day. One definition, two readers.

    Kept as a named function because it is the documented entry point and the
    tests written for it name it. `run()` does the provider-aware check.

    SHAPE ONLY — never a validity check. A revoked, rotated or simply wrong key
    passes; only the network can tell those apart, and preflight is pure by
    design (see the module docstring). Read a pass as "nothing was obviously
    mangled in transit", not "authenticated".

    2026-07-27: setting the key by hand produced FIVE `ANTHROPIC_API_KEY` lines
    in .env — empty, 16 chars, 148, 324 holding three prefixes, and a final 216
    holding two. python-dotenv takes the last, so the agent held two keys
    concatenated. Preflight said CLEAR TO TRADE, because it asked only whether
    the variable was set.
    """
    import llm_client
    return llm_client.key_shape_fail(value, llm_client.PROVIDERS["anthropic"])


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

    # Upper bounds. Only values that are PRESENT and numeric are checked: a
    # missing key is either already reported above (required rails) or a
    # legitimate omission (disableable ones), and reporting it twice would
    # bury the real fault in noise.
    for key in PERCENT_OF_EQUITY_RAILS:
        v = r.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) \
                and v > PERCENT_CEILING:
            fails.append(
                f"risk.{key} is {v!r}% of equity, which is not possible — "
                f"a percentage of equity cannot exceed {PERCENT_CEILING:g}. "
                f"Check for a missing decimal point or a doubled keystroke; "
                f"this rail would otherwise size orders off a number the "
                f"account cannot back")

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

    # THE HEAT INVERSION TRAP (§41, 2026-07-28). portfolio_heat charges a
    # position with no recorded stop `equity x risk_per_trade_pct`, and the
    # heat check charges an entry with no candidate stop the same
    # (src/risk.py:626,633-634 and :845-847). Shipped, that fallback is 8.0%
    # of equity against a cap of 4.0% — double the ENTIRE budget, so any
    # stopless entry would be blocked unconditionally and every other entry
    # blocked behind it.
    #
    # Measured 2026-07-28 on 2022-2026: 0 of 244,920 entries had no stop and
    # 0 of 465 heat evaluations saw a stopless open position, because
    # brackets are on and unprotectable_entry (src/risk.py:355-392) refuses
    # the rest. So the inversion is LATENT, not live, and NO risk number was
    # changed on the strength of a diagnostic.
    #
    # What makes it latent is `brackets.enabled`. Turning brackets off would
    # arm it silently — the bot would simply stop entering, exactly the §40
    # failure shape. So the trap is closed here rather than left documented:
    # a documented check is not a check.
    heat_cap = r.get("max_portfolio_heat_pct") or 0
    per_trade = r.get("risk_per_trade_pct") or 0
    brackets_on = (r.get("brackets") or {}).get("enabled")
    if heat_cap and per_trade > heat_cap and not brackets_on:
        fails.append(
            f"risk.risk_per_trade_pct ({per_trade}) exceeds "
            f"risk.max_portfolio_heat_pct ({heat_cap}) while "
            f"risk.brackets.enabled is off — every stopless entry would be "
            f"blocked by the heat cap on its own fallback charge, and the bot "
            f"would stop trading without saying why. Raise the heat cap, lower "
            f"the per-trade risk, or leave brackets on.")

    # The judge is optional, but claiming to have one and not having one is a
    # misconfiguration — and a quiet one. llm.review_signal() returns a clean
    # `approve` at full size when the key is absent, so trades run UNJUDGED at
    # the size the rules asked for, and evidence.py reports every entry as
    # judged because an llm_review block is present either way. Set
    # llm.enabled: false to trade on rules alone; that is a decision, not a
    # silent hole.
    if cfg.get("llm", {}).get("enabled"):
        import llm_client
        provider = llm_client.provider_name(cfg)
        if provider not in llm_client.PROVIDERS:
            fails.append(f"llm.provider is {provider!r}, which llm_client does "
                         f"not implement — supported: "
                         f"{', '.join(sorted(llm_client.PROVIDERS))}")
        spec = llm_client.provider_spec(cfg)
        env_name = spec["key_env"]
        key = os.environ.get(env_name)
        if not key:
            fails.append(f"llm.enabled: true but {env_name} is not set — "
                         f"every trade would be approved unjudged at full size; "
                         f"set the key or set llm.enabled: false")
        else:
            # Present is not the same as usable. A mangled key fails at the
            # first API call, by which point the cycle has already traded.
            shape = llm_client.key_shape_fail(key, spec)
            if shape:
                fails.append(f"{env_name} {shape}. The judge would fail on "
                             f"every call and each entry would be approved "
                             f"unjudged at full size. Fix the value in .env "
                             f"(one line, one key) or set llm.enabled: false")

        # A misspelled policy silently reverts to `approve` at runtime (the
        # cycle must not crash on a typo), which means an owner who set
        # `blok` believing they had stopped unsupervised trading would get
        # exactly the behaviour they were trying to switch off, with no
        # symptom. Reported here rather than left to be noticed in a ledger.
        raw_policy = cfg["llm"].get("on_unavailable")
        if raw_policy is not None and str(raw_policy).strip().lower() not in (
                "approve", "block"):
            fails.append(
                f"llm.on_unavailable is {raw_policy!r}, which is neither "
                f"'approve' nor 'block' — it would silently fall back to "
                f"'approve' and trade unjudged entries at full size")

        # An unbounded judge call inside a cycle is the 600s-SDK-default
        # failure this setting exists to prevent; a typo must not reinstate it
        # silently either.
        raw_timeout = cfg["llm"].get("timeout_seconds")
        if raw_timeout is not None:
            try:
                bad = float(raw_timeout) <= 0
            except (TypeError, ValueError):
                bad = True
            if bad:
                fails.append(
                    f"llm.timeout_seconds is {raw_timeout!r} — must be a "
                    f"positive number of seconds; it would fall back to the "
                    f"{llm_client.DEFAULT_TIMEOUT_SECONDS}s default")

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
