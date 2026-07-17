"""Learning layer — built around the documented failure modes of naive
agent memory, with the research-backed correctives baked in:

  1. OUTCOME EMBARGO — lessons and evidence come only from CLOSED trades;
     counterfactuals resolve only after their full horizon passes.
  2. FORCED NEGATIVE EXAMPLES — every review batch must contain at least
     `negative_example_quota` losing trades, and the lesson block always
     appends one refuted CAUTION when any exist (anti-resonance).
  3. VALIDATED, LIFECYCLED HYPOTHESES — lessons live in an append-only
     store (src/lessons.py) as falsifiable hypotheses that must earn
     `active` status from later evidence and get refuted/retired otherwise.
  4. JUDGE CALIBRATION — the LLM sees its own veto/approve track record
     (src/judgments.py) so it can't drift overconfident unnoticed.
  5. Learnings can only make the bot MORE conservative: everything here
     feeds the veto/downsize review prompt, never sizing or signals.
"""
import random
from datetime import datetime, timezone

from ledger import Ledger
from lessons import LessonStore
from judgments import JudgmentStore, calibration_metrics, calibration_line
import ranking
import regime as regime_mod


class Memory:
    def __init__(self, cfg: dict, ledger: Ledger):
        self.cfg = cfg["memory"]
        self.lcfg = cfg.get("learning", {})
        self.ledger = ledger
        self.lessons = LessonStore(self.lcfg.get("lessons_path",
                                                 "memory/lessons.jsonl"))
        self.judgments = JudgmentStore(self.lcfg.get("judgments_path",
                                                     "memory/judgments.jsonl"))
        self.path = self.cfg["learnings_path"]  # rendered view, kept for compat
        self.knowledge_path = cfg.get("llm", {}).get("knowledge_path")
        self.news_cfg = cfg.get("news", {})

    # ---- write ----

    def add_learning(self, text: str, trade_id: str = "") -> str:
        """Compatibility wrapper: free-text learnings become global-scope
        candidate hypotheses in the store."""
        return self.lessons.add_lesson(text, {}, source=trade_id or "manual")

    # ---- read (for the LLM judgment layer) ----

    def recent_learnings(self, n: int = 15) -> str:
        states = self.lessons.replay()
        live = sorted([s for s in states.values()
                       if s["status"] in ("active", "candidate")],
                      key=lambda s: s["created_ts"], reverse=True)[:n]
        lines = [f"- [{s['status'].upper()} n={len(s['supports'])}/"
                 f"{len(s['contradicts'])}] {s['hypothesis']}" for s in live]
        return "\n".join(lines) or "(no learnings yet)"

    def balanced_trade_sample(self) -> list[dict]:
        """Recent closed trades with a FORCED minimum share of losers."""
        closed = self.ledger.closed_trades()[-self.cfg["review_lookback_trades"]:]
        if not closed:
            return []
        losers = [t for t in closed if t["result"] == "loss"]
        winners = [t for t in closed if t["result"] == "win"]
        quota = self.cfg["negative_example_quota"]
        n = min(len(closed), 10)
        n_losers = max(int(n * quota), 1 if losers else 0)
        n_losers = min(n_losers, len(losers))
        sample = losers[-n_losers:] + winners[-(n - n_losers):]
        random.shuffle(sample)
        return sample

    def knowledge_block(self) -> str:
        """Static curated principles (knowledge/principles.md), config-gated.
        External and unverified — labeled so the judge weighs it below
        realized evidence. Missing/unreadable file degrades to empty."""
        if not self.knowledge_path:
            return ""
        try:
            with open(self.knowledge_path) as f:
                text = f.read().strip()
        except OSError:
            return ""
        if not text:
            return ""
        cap = self.lcfg.get("max_context_chars", 4000) // 4
        return ("KNOWLEDGE (external, unverified — weigh below realized "
                "evidence):\n" + text[:cap])

    def market_context_block(self, symbol: str | None = None) -> str:
        """Today's news context (memory/market_context.json), labeled as
        unverified. Stale/missing context degrades to empty — yesterday's
        news never leaks into today's judgment."""
        import market_context
        ctx = market_context.load({"news": self.news_cfg})
        if not ctx:
            return ""
        parts = [f"TODAY'S MARKET CONTEXT (news, unverified): {ctx['summary']}"]
        if ctx.get("events_today"):
            parts.append("Events today: " + "; ".join(ctx["events_today"]))
        flag = (ctx.get("symbol_flags") or {}).get(symbol or "")
        if flag:
            parts.append(f"{symbol} news: {flag}")
        cap = self.lcfg.get("max_context_chars", 4000) // 4
        return "\n".join(parts)[:cap]

    def context_for_llm(self, symbol: str | None = None,
                        regime: dict | None = None,
                        strategy: str | None = None) -> str:
        """Memory block for the judgment prompt: balanced trades, ranked
        validated lessons (scope match: symbol > regime > strategy > global),
        the judge's own calibration, current regime.
        Hard-capped at learning.max_context_chars."""
        trades = self.balanced_trade_sample()
        lines = []
        for t in trades:
            lines.append(f"  [{t['result'].upper()}] {t['symbol']} {t['action']} — "
                         f"reason: {t['strategy_reason']} — P&L: {t['pnl_pct']}%")
        trade_block = "\n".join(lines) or "  (no closed trades yet)"

        now = datetime.now(timezone.utc)
        regime_label = regime["label"] if regime else None
        ranked = ranking.top_lessons(self.lessons.replay(), symbol, regime_label,
                                     self.lcfg.get("top_k_lessons", 8), now,
                                     strategy=strategy)
        lesson_block = ranking.format_lessons_block(
            ranked, regime_label, self.lcfg.get("max_context_chars", 4000) // 2)

        calib = calibration_line(calibration_metrics(self.judgments.replay()))

        knowledge = self.knowledge_block()
        news = self.market_context_block(symbol)
        ctx = (f"RECENT CLOSED TRADES (balanced sample — losses included on purpose):\n"
               f"{trade_block}\n\n"
               f"VALIDATED LESSONS (hypotheses with evidence counts n=supports/contradicts):\n"
               f"{lesson_block}\n\n"
               + (f"{knowledge}\n\n" if knowledge else "")
               + (f"{news}\n\n" if news else "")
               + f"{calib}\n"
               f"CURRENT REGIME: {regime_mod.describe(regime)}")
        return ctx[:self.lcfg.get("max_context_chars", 4000)]
