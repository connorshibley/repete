"""Pure lesson ranking + token-bounded context assembly for the review prompt.

Scope match (symbol > regime > global) beats evidence strength beats recency.
One refuted lesson is always force-included as a CAUTION when any exist —
the retrieval-side analog of the forced-negative-example quota.
"""
import math
from datetime import datetime

RECENCY_HALF_LIFE_DAYS = 30


def score_lesson(lesson: dict, symbol: str | None, regime: str | None,
                 now: datetime, strategy: str | None = None) -> float:
    scope = lesson.get("scope") or {}
    score = 0.0
    if symbol and symbol in (scope.get("symbols") or []):
        score += 3.0
    if regime and scope.get("regime") == regime:
        score += 2.0
    if strategy and scope.get("strategy") == strategy:
        score += 1.0
    if not scope.get("symbols") and not scope.get("regime"):
        score += 0.5  # global lessons apply everywhere, weakly

    n_sup, n_con = len(lesson["supports"]), len(lesson["contradicts"])
    score += min(n_sup - n_con, 3) * 0.5

    last = datetime.fromisoformat(max(lesson["created_ts"], lesson["last_evidence_ts"]))
    age_days = max((now - last).days, 0)
    score += math.exp(-age_days * math.log(2) / RECENCY_HALF_LIFE_DAYS)
    return score


def top_lessons(states: dict, symbol: str | None, regime: str | None,
                k: int, now: datetime, strategy: str | None = None) -> list[dict]:
    """Active lessons first, candidates fill remaining slots; plus one refuted
    caution appended (marked with _caution=True) when any refuted exist."""
    def ranked(status):
        pool = [s for s in states.values() if s["status"] == status]
        return sorted(pool,
                      key=lambda s: score_lesson(s, symbol, regime, now, strategy),
                      reverse=True)

    picked = ranked("active")[:k]
    if len(picked) < k:
        picked += ranked("candidate")[:k - len(picked)]

    refuted = sorted([s for s in states.values() if s["status"] == "refuted"],
                     key=lambda s: len(s["contradicts"]), reverse=True)
    if refuted:
        picked.append({**refuted[0], "_caution": True})
    return picked


def _fmt_scope(scope: dict) -> str:
    parts = []
    if scope.get("symbols"):
        parts.append(",".join(scope["symbols"]))
    if scope.get("regime"):
        parts.append(scope["regime"])
    if scope.get("direction"):
        parts.append(scope["direction"])
    if scope.get("strategy"):
        parts.append(scope["strategy"])
    return "|".join(parts) or "global"


def format_lessons_block(ranked: list[dict], current_regime: str | None,
                         max_chars: int) -> str:
    """Render the ranked lessons, truncating to the char budget."""
    if not ranked:
        return "(no validated lessons yet)"
    lines = []
    for s in ranked:
        n = f"n={len(s['supports'])}/{len(s['contradicts'])}"
        scope = s.get("scope") or {}
        tag = (f"CAUTION (refuted, {n})" if s.get("_caution")
               else f"{s['status'].upper()} {n}, scope {_fmt_scope(scope)}")
        off = (" (different regime)"
               if current_regime and scope.get("regime")
               and scope["regime"] != current_regime and not s.get("_caution") else "")
        hyp = s["hypothesis"][:200]
        line = f"  [{tag}]{off} {hyp}"
        if sum(len(l) + 1 for l in lines) + len(line) > max_chars:
            break
        lines.append(line)
    return "\n".join(lines) or "(no validated lessons yet)"
