"""Per-symbol research dossier — the deep half of the judge's context.

WHAT THIS IS FOR

The judge already receives a lot (§61 named nine context budgets totalling
10,966 chars). But almost all of it is GLOBAL: the last 20 resolved calls
across every symbol, aggregate calibration, a 600-char news block covering
whatever the distiller flagged today. What it has never had is the file on
THIS NAME:

  - what news this symbol has actually carried, and how those trades resolved
  - how the judge has called this symbol before, and whether it was right
  - how close this symbol is to an earnings date
  - what the book already holds in it and in its sector

Those are the questions a person would ask before sizing a trade, and the
judge has been answering without them.

WHAT THIS IS NOT

**No model call.** Every tool here is deterministic retrieval over state this
repo already keeps. That is deliberate and it is not a limitation dressed up
as a virtue:

  - it is testable offline, which a model call is not;
  - it costs nothing, so it runs while the account has no credits;
  - it cannot hallucinate, because it never generates text — it selects it;
  - and it does not move the judge's *verdict distribution* the way a
    reasoning change would, so `knowledge/judge_calibration.json` — fitted
    over 34 days and 250 judged buys — stays valid.

A synthesis call over these findings is a later, separate step, and it is the
step that invalidates the calibration. Keeping the retrieval half free of it
is what lets this ship today.

INVARIANT

This module NEVER decides anything. It returns evidence. The judge still only
vetoes or shrinks (`llm._clamp_scale`), the signal still comes from the
deterministic ensemble, and `tests/test_judge_verdict_surface.py` pins the
fact that no key here can redirect a trade.

`src/backtest.py` must never import this. The simulator models the judge as a
distribution (`judge_model.py`); a per-symbol dossier has nothing for that
distribution to attach to — the same argument divergence #15 makes about
curated principles.

FAILURE POLICY

A tool that raises produces a Finding with `ok=False` and a reason. It does
NOT raise, and it does NOT return an empty finding that reads as "nothing
found" — those are different states and conflating them is the mistake this
repo keeps finding. A degraded brief is still a brief; a crashed cycle is a
missed trading day.
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

MAX_NEWS_ITEMS = 5
MAX_PRIOR_CALLS = 6


@dataclass
class Finding:
    """One tool's answer.

    `ok=False` means the tool FAILED. It does not mean "found nothing" —
    that is `ok=True` with an empty summary, and the two must stay
    distinguishable or a broken retriever reads as a quiet market.
    """
    tool: str
    ok: bool
    summary: str = ""
    detail: dict = field(default_factory=dict)
    error: str | None = None

    def line(self) -> str:
        if not self.ok:
            return f"- {self.tool}: UNAVAILABLE ({self.error})"
        return f"- {self.tool}: {self.summary}" if self.summary else \
               f"- {self.tool}: nothing on record"


@dataclass
class ResearchBrief:
    symbol: str
    action: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True if ANY tool failed. Surfaced so the judge can discount the
        brief rather than read a partial dossier as a complete one."""
        return any(not f.ok for f in self.findings)

    @property
    def failed_tools(self) -> list[str]:
        return [f.tool for f in self.findings if not f.ok]

    def to_block(self, budget: int) -> str:
        """Render for the judge prompt, inside `budget` characters.

        Truncation drops WHOLE findings from the end and says how many it
        dropped. A mid-sentence cut is what §61 found the judge had been
        living with — `"Iran deal h"` — and a block that lies about its own
        completeness is worse than a shorter one.
        """
        header = f"RESEARCH ON {self.symbol} ({self.action})"
        if self.degraded:
            header += f" — PARTIAL, unavailable: {', '.join(self.failed_tools)}"
        lines = [header]
        used = len(header)
        dropped = 0
        for f in self.findings:
            line = f.line()
            if used + len(line) + 1 > budget:
                dropped += 1
                continue
            lines.append(line)
            used += len(line) + 1
        if dropped:
            note = f"- ({dropped} finding(s) omitted for space)"
            if used + len(note) + 1 <= budget:
                lines.append(note)
        return "\n".join(lines)


def _safe(tool: str, fn) -> Finding:
    """Run a tool so that its failure degrades the brief and nothing else.

    Broad except on purpose: this is an evidence-gathering path, and the
    alternative to a caught exception here is a cycle that does not trade.
    The reason is carried, not swallowed — an UNAVAILABLE with no cause is
    the shape of a silent failure.
    """
    try:
        summary, detail = fn()
        return Finding(tool=tool, ok=True, summary=summary, detail=detail or {})
    except Exception as e:  # noqa: BLE001 — see docstring
        return Finding(tool=tool, ok=False,
                       error=f"{type(e).__name__}: {e}".strip()[:120],
                       detail={"traceback": traceback.format_exc()[-400:]})


# ---- tools -----------------------------------------------------------------

def _news_on_symbol(symbol, cfg, now):
    import news_memory
    hist = news_memory.history(symbol, cfg, now=now) or []
    if not hist:
        return "", {"n": 0}
    recent = hist[-MAX_NEWS_ITEMS:]
    bits = []
    for h in recent:
        # `text` is the field news_memory actually writes (news_memory.py:121,
        # from the distiller's per-symbol flag or nomination). The first cut of
        # this guessed `note`/`headline`, which do not exist — so the block
        # rendered "11 flagged; 2026-07-31 | 2026-07-31 | ..." : five dates and
        # no information, against a real ledger. Caught by rendering it, not by
        # reading the code.
        when = str(h.get("date") or h.get("ts", ""))[:10]
        text = str(h.get("text") or "").strip()
        pnl = h.get("pnl_pct")
        tail = f" -> {pnl:+.1f}%" if isinstance(pnl, (int, float)) else ""
        nom = "*" if h.get("nominated") else ""
        bits.append(f"{when}{nom} {text[:70]}{tail}".strip())
    return f"{len(hist)} flagged; " + " | ".join(bits), {"n": len(hist)}


def _prior_calls_on_symbol(symbol, judgments):
    """How the judge has called THIS name before, and whether it was right.

    The global scoreboard the judge already sees is the last 20 across every
    symbol; on a 38-name universe that is rarely more than one row for the
    name in front of it.
    """
    mine = [j for j in (judgments or {}).values() if j.get("symbol") == symbol]
    if not mine:
        return "", {"n": 0}
    mine = sorted(mine, key=lambda j: str(j.get("ts", "")))[-MAX_PRIOR_CALLS:]
    bits = []
    wins = losses = 0
    for j in mine:
        pnl = j.get("pnl_pct")
        if isinstance(pnl, (int, float)):
            wins += pnl > 0
            losses += pnl < 0
            outcome = f"{pnl:+.1f}%"
        else:
            outcome = "open" if j.get("executed") else "blocked"
        bits.append(f"{str(j.get('ts',''))[:10]} {j.get('verdict')}->{outcome}")
    head = f"{len(mine)} prior call(s)"
    if wins or losses:
        head += f", {wins}W/{losses}L resolved"
    return head + "; " + " | ".join(bits), {"n": len(mine), "w": wins, "l": losses}


def _earnings_proximity(symbol, cfg, now):
    import earnings
    dates = earnings.get_dates(symbol) or []
    if not dates:
        return "", {"known": False}
    on = now.strftime("%Y-%m-%d")
    for horizon in (3, 7, 14):
        if earnings.next_within(dates, on, horizon):
            return f"earnings within {horizon} days", {"known": True,
                                                       "within": horizon}
    return "no earnings within 14 days", {"known": True, "within": None}


def _book_exposure(symbol, positions, cfg):
    """What the book already carries here and in this name's sector.

    Both helpers are called with the repo's own argument order —
    `sector_of(cfg, symbol)` (strategies/base.py:121) and
    `sector_open_count(cfg, symbol, positions)` (risk.py:1311), the latter
    taking the SYMBOL and resolving the sector itself. It is the single
    implementation the live cycle and both simulators share, so calling it
    rather than re-deriving a count here is the whole point — re-derived
    counters are the shape behind §13's missing rails and §19b's global ones.
    """
    import risk
    from strategies.base import sector_of
    held = (positions or {}).get(symbol)
    sector = sector_of(cfg, symbol)
    same = risk.sector_open_count(cfg, symbol, positions or {})
    bits = []
    if held:
        bits.append(f"already held ({held.get('qty')} sh)")
    if sector:
        bits.append(f"sector {sector}: {same} other open")
    return "; ".join(bits), {"held": bool(held), "sector": sector,
                             "sector_open": same}


# ---- assembly --------------------------------------------------------------

def build(symbol: str, action: str, cfg: dict, *,
          positions: dict | None = None,
          judgments: dict | None = None,
          now: datetime | None = None) -> ResearchBrief:
    """Gather the dossier. Never raises; never decides."""
    now = now or datetime.now(timezone.utc)
    brief = ResearchBrief(symbol=symbol, action=action)
    brief.findings = [
        _safe("news", lambda: _news_on_symbol(symbol, cfg, now)),
        _safe("prior_calls", lambda: _prior_calls_on_symbol(symbol, judgments)),
        _safe("earnings", lambda: _earnings_proximity(symbol, cfg, now)),
        _safe("book", lambda: _book_exposure(symbol, positions, cfg)),
    ]
    return brief
