"""Structured lesson store — the hypothesis book of the learning loop.

Append-only JSONL events (mirroring the ledger pattern); current state is
derived by replay. Lessons are falsifiable hypotheses with a lifecycle:

    candidate --(>=N supports, ratio)--> active
    candidate/active --(contradicts >= supports, n>=M)--> refuted   (terminal)
    candidate/active --(no evidence in staleness_days)--> retired   (terminal)

learnings.md is a RENDERED VIEW regenerated from this store — never the
source of truth. Lessons feed ONLY the veto/downsize judgment layer
(conservative-only invariant); nothing here touches sizing or signals.
"""
import json
import os
import re
import uuid
from datetime import datetime, timezone

TERMINAL = ("refuted", "retired")

_LEGACY_BULLET = re.compile(r"^- \*\*(\d{4}-\d{2}-\d{2})\*\*(?: \(trade ([^)]+)\))?: (.+)$")


class LessonStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # ---- writes (append-only) ----

    def append(self, record: dict):
        record["ts"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def add_lesson(self, hypothesis: str, scope: dict, source: str) -> str:
        lesson_id = f"ls-{uuid.uuid4().hex[:8]}"
        self.append({"event": "lesson_created",
                     "lesson": {"id": lesson_id, "hypothesis": hypothesis,
                                "scope": scope or {}, "source": source}})
        return lesson_id

    def add_evidence(self, lesson_id: str, trade_id: str, relation: str, reason: str):
        assert relation in ("supports", "contradicts")
        self.append({"event": "evidence", "lesson_id": lesson_id,
                     "trade_id": trade_id, "relation": relation, "reason": reason})

    def change_status(self, lesson_id: str, from_s: str, to_s: str, reason: str):
        self.append({"event": "status_change", "lesson_id": lesson_id,
                     "from": from_s, "to": to_s, "reason": reason})

    def mark_trade_evaluated(self, trade_id: str, n_lessons: int):
        self.append({"event": "trade_evaluated", "trade_id": trade_id,
                     "n_lessons": n_lessons})

    # ---- reads (replay) ----

    def _records(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def replay(self) -> dict:
        """lesson_id -> current state (lesson fields + status + evidence)."""
        states: dict = {}
        for r in self._records():
            if r["event"] == "lesson_created":
                lesson = r["lesson"]
                states[lesson["id"]] = {
                    **lesson, "created_ts": r["ts"], "status": "candidate",
                    "supports": [], "contradicts": [], "last_evidence_ts": r["ts"],
                }
            elif r["event"] == "evidence" and r["lesson_id"] in states:
                s = states[r["lesson_id"]]
                s[r["relation"]].append(r["trade_id"])
                s["last_evidence_ts"] = r["ts"]
            elif r["event"] == "status_change" and r["lesson_id"] in states:
                states[r["lesson_id"]]["status"] = r["to"]
        return states

    def evaluated_trade_ids(self) -> set:
        return {r["trade_id"] for r in self._records()
                if r["event"] == "trade_evaluated"}

    def by_status(self, *statuses: str) -> list[dict]:
        return [s for s in self.replay().values() if s["status"] in statuses]


# ---- pure lifecycle rules ----

def apply_transitions(states: dict, now: datetime, lcfg: dict) -> list[tuple]:
    """Deterministic lifecycle pass. Returns [(lesson_id, from, to, reason)].

    Refutation is checked BEFORE promotion (a contradicted hypothesis never
    activates on the same pass); refuted/retired are terminal.
    """
    out = []
    for lid, s in states.items():
        if s["status"] in TERMINAL:
            continue
        n_sup, n_con = len(s["supports"]), len(s["contradicts"])
        total = n_sup + n_con

        if n_con >= n_sup and total >= lcfg["refutation_min_evidence"]:
            out.append((lid, s["status"], "refuted",
                        f"contradicts={n_con} >= supports={n_sup} with n={total}"))
            continue

        last = datetime.fromisoformat(max(s["created_ts"], s["last_evidence_ts"]))
        if (now - last).days > lcfg["staleness_days"]:
            out.append((lid, s["status"], "retired",
                        f"no new evidence in {lcfg['staleness_days']}d"))
            continue

        if (s["status"] == "candidate"
                and n_sup >= lcfg["promotion_min_supports"]
                and n_sup >= lcfg["promotion_support_ratio"] * n_con):
            out.append((lid, "candidate", "active",
                        f"supports={n_sup} contradicts={n_con} "
                        f"(>={lcfg['promotion_min_supports']} and >="
                        f"{lcfg['promotion_support_ratio']}x)"))
    return out


# ---- rendered view ----

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


def _bullet(s: dict) -> str:
    day = s["created_ts"][:10]
    ref = f" (trade {s['source']})" if s.get("source") else ""
    return (f"- **{day}**{ref}: [{s['status'].upper()} "
            f"n={len(s['supports'])}/{len(s['contradicts'])}, "
            f"scope {_fmt_scope(s['scope'])}] {s['hypothesis']}")


def render_markdown(states: dict, path: str):
    """Regenerate learnings.md from the store. GENERATED — never hand-edit."""
    active = [s for s in states.values() if s["status"] == "active"]
    candidates = [s for s in states.values() if s["status"] == "candidate"]
    closed = sorted([s for s in states.values() if s["status"] in TERMINAL],
                    key=lambda s: s["created_ts"], reverse=True)[:10]

    lines = ["# Learnings (append-only, date-stamped)", "",
             "<!-- GENERATED from memory/lessons.jsonl by src/learn.py — do not hand-edit. -->",
             "<!-- Entries are generated from CLOSED trades only. -->", ""]
    for title, group in (("## Active hypotheses", active),
                         ("## Candidates (unproven)", candidates),
                         ("## Recently refuted / retired", closed)):
        lines.append(title)
        lines += [_bullet(s) for s in
                  sorted(group, key=lambda s: s["created_ts"], reverse=True)] or ["(none)"]
        lines.append("")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def parse_legacy_markdown(path: str) -> list[dict]:
    """Extract hand-written bullets from a pre-store learnings.md for migration."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            if "GENERATED from memory/lessons.jsonl" in line:
                return []  # already migrated — nothing hand-written to import
            m = _LEGACY_BULLET.match(line.strip())
            if m:
                out.append({"date": m.group(1), "trade_id": m.group(2) or "migrated",
                            "text": m.group(3)})
    return out
