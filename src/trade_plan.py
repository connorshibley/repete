"""The bot's game plan for a trade, in readable form.

Why this exists
---------------
At entry the bot already knows the whole plan: the thesis its strategy fired
on, the protective stop it just placed, the rule that will take it out, the
earliest it is allowed to leave, and the market regime it entered into. None of
that was ever written down — the ledger stored a one-line reason, the raw
indicators, and the judge's verdict, and nothing else. So "why does it hold
this, and for how long?" had no answer on file.

The design rule that keeps this honest
--------------------------------------
**Surface what the bot computed. Never invent a narrative.** Every field here
is read from a real config value, a real order, or a real signal — nothing is
generated prose, and nothing is a forecast. Where the bot genuinely does not
know something (a price target on an all-time-high breakout, for instance), the
field says so rather than filling the gap with a plausible-sounding number.

That distinction matters: this is a *reporting* gap being closed, not a
*judgement* gap. Dressing up an unknown as a target would manufacture
confidence the evidence does not support — and §18 is explicit that no strategy
here has an out-of-sample edge distinguishable from zero.
"""
from __future__ import annotations


def _hold_expectation(strategy: str, params: dict, cfg: dict) -> dict:
    """What actually ends this trade, per strategy, from real config values.

    These are descriptions of the shipped exit rules, not predictions. A trend
    strategy has no expected hold length — saying "about two weeks" would be an
    invention — so it is reported as open-ended with the rule that ends it.
    """
    floor = (cfg.get("risk") or {}).get("min_holding_days", 0)
    common = {"min_holding_days": floor,
              "earliest_exit": f"day {floor + 1}" if floor else "same day"}

    if strategy == "meanrev":
        cap = params.get("max_hold_days")
        sma = params.get("exit_sma_period")
        return {**common, "shape": "short, mean-reverting",
                "typical": f"exits as soon as price closes back above its "
                           f"{sma}-day average — usually days, not weeks",
                "hard_cap_days": cap,
                "ends_when": f"close > SMA{sma}, or {cap} days elapse, "
                             "or the stop is hit"}
    if strategy == "tsmom":
        return {**common, "shape": "open-ended trend follow",
                "typical": "no fixed horizon — it rides the trend until the "
                           "trend ends, which may be days or months",
                "hard_cap_days": None,
                "ends_when": f"{params.get('momentum_bars')}-bar momentum turns "
                             f"negative, or price closes below its "
                             f"SMA{params.get('trend_sma_period')}, or a "
                             "trailing stop is hit"}
    if strategy == "ma_crossover":
        return {**common, "shape": "open-ended trend follow",
                "typical": "no fixed horizon — held while the fast average "
                           "stays above the slow one",
                "hard_cap_days": None,
                "ends_when": f"SMA{params.get('fast_period')} crosses back "
                             f"below SMA{params.get('slow_period')}, or the "
                             "stop is hit"}
    if strategy == "donchian":
        return {**common, "shape": "open-ended breakout",
                "typical": "no fixed horizon — held while the breakout holds",
                "hard_cap_days": None,
                "ends_when": f"price breaks the "
                             f"{params.get('exit_channel_bars')}-bar low, "
                             "or the stop is hit"}
    if strategy == "xsmom":
        return {**common, "shape": "open-ended relative strength",
                "typical": "held while the name stays strong versus the universe",
                "hard_cap_days": None,
                "ends_when": "its cross-sectional rank falls out of the top "
                             f"{params.get('exit_below_fraction')} of the "
                             "universe, or the stop is hit"}
    return {**common, "shape": "unknown",
            "typical": "unknown — no hold profile recorded for this strategy",
            "hard_cap_days": None, "ends_when": "unknown"}


def _exit_plan(price: float, order: dict | None, cfg: dict,
               strategy: str) -> dict:
    """The actual protective levels placed with this order."""
    o = order or {}
    stop = o.get("stop_price") or o.get("stop")
    tp = o.get("take_profit_price") or o.get("tp")
    b = (cfg.get("risk") or {}).get("brackets") or {}
    trail = b.get("trailing_atr_mult", 0)
    t_strats = b.get("trailing_strategies")
    trailing_on = bool(trail) and (not t_strats or strategy in t_strats)

    plan = {
        "stop_price": round(float(stop), 2) if stop else None,
        "take_profit_price": round(float(tp), 2) if tp else None,
        "risk_per_share": (round(float(price) - float(stop), 2)
                           if stop else None),
        "risk_pct_of_entry": (round((float(price) - float(stop))
                                    / float(price) * 100, 2)
                              if stop and price else None),
        "trailing_stop": (f"ratchets to high-water − {trail}×ATR, never down"
                          if trailing_on else "none — fixed stop only"),
    }
    plan["invalidated_if"] = (
        f"price closes below the ${plan['stop_price']:,.2f} stop"
        if plan["stop_price"] else
        "no protective stop was placed — position is unbracketed")
    if not plan["take_profit_price"]:
        # Say so plainly rather than inventing a level.
        plan["target"] = ("no fixed target — the exit rule decides, so the "
                          "upside is not capped by a preset price")
    return plan


def build(sig, cfg: dict, price: float, qty: int, order: dict | None,
          regime_label: str | None, review: dict | None = None,
          rank: int | None = None, candidates: int | None = None) -> dict:
    """Assemble the plan. Every value comes from a real signal, order, or
    config entry — nothing here is generated prose or a forecast."""
    strategy = getattr(sig, "strategy", "") or "unknown"
    params = ((cfg.get("strategies") or {}).get(strategy) or {})
    r = review or {}

    plan = {
        "thesis": getattr(sig, "reason", "") or "unknown",
        "strategy": strategy,
        "entry_price": round(float(price), 2) if price else None,
        "shares": qty,
        "position_value": (round(float(price) * int(qty), 2)
                           if price and qty else None),
        "signals_that_fired": dict(getattr(sig, "indicators", {}) or {}),
        "expected_hold": _hold_expectation(strategy, params, cfg),
        "exit_plan": _exit_plan(price, order, cfg, strategy),
        "market_context": {
            "regime": regime_label or "unknown",
            "note": ("entries are gated on the strategy's own trend filter; "
                     "the regime tag is recorded for the learning loop"),
        },
        "review": {
            "verdict": r.get("verdict", "unknown"),
            "size_scale": r.get("scale"),
            "reasoning": r.get("reasoning", ""),
        },
    }
    if rank is not None and candidates:
        plan["why_this_one"] = (f"candidate {rank} of {candidates} that cleared "
                                "every rail this cycle")
    return plan


def to_text(plan: dict) -> str:
    """Human-readable rendering for posts, the dashboard, and alerts."""
    if not plan:
        return ""
    ex, hold, mkt = plan.get("exit_plan", {}), plan.get("expected_hold", {}), \
        plan.get("market_context", {})
    lines = [
        f"WHY: {plan.get('thesis')}",
        f"STRATEGY: {plan.get('strategy')} ({hold.get('shape', 'unknown')})",
        f"PLAN: {hold.get('typical', 'unknown')}",
        f"EXITS WHEN: {hold.get('ends_when', 'unknown')}",
    ]
    if ex.get("stop_price"):
        lines.append(f"STOP: ${ex['stop_price']:,.2f} "
                     f"(risking ${ex.get('risk_per_share', 0):,.2f}/share, "
                     f"{ex.get('risk_pct_of_entry')}%)")
    lines.append(f"TARGET: {ex.get('target') or ex.get('take_profit_price')}")
    lines.append(f"TRAIL: {ex.get('trailing_stop')}")
    lines.append(f"EARLIEST EXIT: {hold.get('earliest_exit', 'unknown')} "
                 f"(swing guard)")
    lines.append(f"REGIME: {mkt.get('regime')}")
    if plan.get("why_this_one"):
        lines.append(f"SELECTION: {plan['why_this_one']}")
    return "\n".join(lines)
