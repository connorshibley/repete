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
from judgments import (JudgmentStore, calibration_metrics, calibration_line,
                       recent_outcomes_block)
import ranking
import regime as regime_mod


# The label `Memory.knowledge_block()` prefixes to the file's text. It sits
# OUTSIDE the slice, so the block's real footprint is its budget plus this.
KNOWLEDGE_LABEL_CHARS = 66

# Every block of the judge prompt, in assembly order.
# ORDER IS MEANINGFUL. `context_for_llm` slices the TAIL when the cap bites,
# and `preflight` reads CONTEXT_BLOCKS[-4:] to name the blocks that go first.
# `research` is inserted after `trades`, not appended, so that the four
# at-risk blocks §61 identified stay the same four.
CONTEXT_BLOCKS = ("book", "trades", "research", "lessons", "knowledge",
                  "market_context", "news_memory", "scoreboard",
                  "calibration", "regime")


def context_budgets(cfg: dict) -> dict[str, int | None]:
    """Chars each block of the judge prompt may occupy. `None` = unbounded.

    A module function, not just a method, so `preflight` can check the
    arithmetic without constructing a `Memory` — preflight is pure by design
    and building one would open the lesson and judgment stores.

    Reads each block's budget from wherever its feature lives: `knowledge` and
    `news_memory` already had their own homes and keep them; the rest are named
    under `learning.context_budgets`.
    """
    lcfg = cfg.get("learning") or {}
    total = lcfg.get("max_context_chars", 4000)
    named = lcfg.get("context_budgets") or {}
    news = cfg.get("news") or {}

    def cfg_or(key, fallback):
        return int(named[key]) if named.get(key) else fallback

    knowledge = int((cfg.get("llm") or {}).get("knowledge_max_context_chars")
                    or total // 4)
    return {
        # Previously unbudgeted. `max_open_positions: 0` means UNCAPPED (§29),
        # so the book can list every name in the universe — the largest block
        # in the prompt was the one nobody had bounded.
        "book": cfg_or("book", None),
        "trades": cfg_or("trades", None),
        "lessons": cfg_or("lessons", total // 2),
        "knowledge": knowledge + KNOWLEDGE_LABEL_CHARS,
        "market_context": cfg_or("market_context", total // 4),
        "news_memory": int((news.get("memory") or {}).get(
            "max_context_chars") or 600),
        "scoreboard": cfg_or("scoreboard", total // 4),
        "calibration": cfg_or("calibration", None),
        "regime": cfg_or("regime", None),
        # The per-symbol dossier (src/research.py). Budgeted from the day it
        # was added rather than after it silently evicted something — §61 was
        # the lesson: `TODAY'S MARKET CONTEXT` was being cut mid-word at
        # "Iran deal h" and NEWS MEMORY, the scoreboard, calibration and the
        # regime label were dropped entirely, because a `ctx[:4000]` returned
        # happily and nothing marked the prompt.
        "research": cfg_or("research", None),
    }


def budget_overage(cfg: dict) -> int:
    """How far the named budgets exceed the total. 0 when they fit.

    Unbounded blocks contribute nothing, so this is a LOWER bound — a config
    that leaves the book unbudgeted can still overflow without this noticing,
    which is why the runtime `context_evicted` event exists beside it.
    """
    b = context_budgets(cfg)
    total = (cfg.get("learning") or {}).get("max_context_chars", 4000)
    return max(0, sum(v for v in b.values() if v is not None) - total)


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
        self.llm_cfg = cfg.get("llm", {})
        self.knowledge_path = self.llm_cfg.get("knowledge_path")
        self.news_cfg = cfg.get("news", {})
        # The WHOLE config. `self.cfg` above is only cfg["memory"]; the
        # research block resolves sectors (strategies.base.sector_map) and
        # earnings dates, which live at the top level. Kept rather than
        # re-read so the block cannot disagree with the config this Memory
        # was constructed from.
        self.full_cfg = cfg

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

    def similar_trade_sample(self, signal,
                             regime_label: str | None = None) -> list[dict]:
        """Closed trades MOST SIMILAR to the current signal (deterministic
        port of OpenProphet's find_similar_setups — no embeddings, 2026-07-21).
        Category score: same strategy +3, same regime +2, same symbol +2,
        same action +1; indicator closeness is a <1pt tiebreak so it can
        never outrank a category match. Invariant #5 still holds: the most
        similar LOSERS are force-included, so similarity can never produce
        a winners-only highlight reel."""
        closed = self.ledger.closed_trades()[-self.cfg["review_lookback_trades"]:]
        if not closed:
            return []
        sig_ind = {k: v
                   for k, v in (getattr(signal, "indicators", None) or {}).items()
                   if isinstance(v, (int, float))}

        def score(t: dict) -> float:
            s = 0.0
            if t.get("strategy") and t["strategy"] == getattr(signal, "strategy", None):
                s += 3
            if regime_label and t.get("regime") == regime_label:
                s += 2
            if t.get("symbol") == getattr(signal, "symbol", None):
                s += 2
            if t.get("action") == getattr(signal, "action", None):
                s += 1
            t_ind = t.get("indicators") or {}
            shared = [k for k in sig_ind
                      if isinstance(t_ind.get(k), (int, float))]
            if shared:
                dist = sum(min(abs(float(sig_ind[k]) - float(t_ind[k]))
                               / max(abs(float(sig_ind[k])),
                                     abs(float(t_ind[k])), 1e-9), 1.0)
                           for k in shared) / len(shared)
                s += (1.0 - dist) * 0.9
            return s

        ranked = sorted(closed, key=score, reverse=True)
        n = min(len(ranked), 10)
        quota = self.cfg["negative_example_quota"]
        losers = [t for t in ranked if t["result"] == "loss"]
        n_losers = min(max(int(n * quota), 1 if losers else 0), len(losers))
        keep_ids = {id(t) for t in losers[:n_losers]}
        for t in ranked:
            if len(keep_ids) >= n:
                break
            keep_ids.add(id(t))
        return [t for t in ranked if id(t) in keep_ids][:n]

    def budgets(self) -> dict[str, int | None]:
        """Chars each block of the judge prompt may occupy. `None` = unbounded.

        THE SINGLE SOURCE OF TRUTH, and the fix for a bug that ran for months.
        Three of these used to be DERIVED SHARES of the total — lessons at
        `// 2`, market context and the scoreboard at `// 4` each. That is
        exactly 100% of the cap between them, before knowledge, news memory,
        the book, the trade block, calibration or the regime label got
        anything. The oversubscription was scale-invariant: doubling the total
        left it at exactly 100%.

        It was not theoretical. Measured 2026-08-11 against the live memory
        files, `context_for_llm` assembled 5,613 chars against a 4,000 cap and
        the bare `ctx[:4000]` at the end dropped NEWS MEMORY, YOUR LAST
        RESOLVED CALLS, YOUR RECENT CALIBRATION and CURRENT REGIME entirely,
        cutting TODAY'S MARKET CONTEXT mid-word. Silently, on every judge call,
        with no marker in the prompt and no test that could fail. §61.

        Naming a budget must not CHANGE it. Every block that had one keeps its
        old value as the fallback, and the four that had none fall back to
        `None` rather than to a number — an unset config is byte-identical to
        before, which is the rollback path and is pinned by
        `test_an_unset_config_is_byte_identical`.
        """
        return context_budgets({"learning": self.lcfg, "llm": self.llm_cfg,
                                "news": self.news_cfg})

    def budget_overage(self) -> int:
        """How far the named budgets exceed the total. 0 when they fit."""
        return budget_overage(
            {"learning": self.lcfg, "llm": self.llm_cfg, "news": self.news_cfg})

    def budget_unbounded(self) -> list[str]:
        """Blocks with no budget, and therefore no share this can guarantee."""
        return sorted(k for k, v in self.budgets().items() if v is None)

    # Headings in assembly order, so an eviction can name what it dropped
    # rather than reporting a character count nobody can act on.
    _BLOCK_MARKERS = (
        ("book", "CURRENT BOOK"),
        # Matches BOTH headers: "MOST SIMILAR PAST CLOSED TRADES" when a live
        # signal is present, "RECENT CLOSED TRADES" for the balanced sample.
        ("trades", "CLOSED TRADES"),
        ("lessons", "VALIDATED LESSONS"),
        ("knowledge", "KNOWLEDGE (external"),
        ("market_context", "TODAY'S MARKET CONTEXT"),
        ("news_memory", "NEWS MEMORY"),
        ("scoreboard", "YOUR LAST RESOLVED"),
        ("calibration", "YOUR RECENT CALIBRATION"),
        ("regime", "CURRENT REGIME"),
    )

    def _evicted_blocks(self, ctx: str, cap: int) -> list[str]:
        """Which blocks the final cap removes or cuts. Present-then-gone only.

        A block absent from the UNCUT context was never there to lose — a flat
        book has no CURRENT BOOK block, and reporting that as an eviction would
        cry wolf on the ordinary case.
        """
        lost = []
        for name, marker in self._BLOCK_MARKERS:
            at = ctx.find(marker)
            if at < 0:
                continue
            if at >= cap:
                lost.append(name)
            elif at + len(marker) < len(ctx) and cap < len(ctx):
                # Present but the cut lands inside it.
                nxt = min((ctx.find(m, at + 1) for _, m in self._BLOCK_MARKERS
                           if ctx.find(m, at + 1) > 0), default=len(ctx))
                if cap < nxt:
                    lost.append(f"{name} (cut)")
        return lost

    def _record_eviction(self, ctx: str, cap: int) -> None:
        """Ledger the fact that the judge did not see everything it was given.

        This is the signal whose absence let four blocks go missing for months:
        `ctx[:cap]` returned happily, nothing marked the prompt, and the only
        symptom was worse judgments. Never raises — it sits on the path to every
        judge call, and `alerting.py`'s rule is that observability must not be
        able to break trading.
        """
        try:
            self.ledger.log_context_eviction(
                cap=cap,
                assembled_chars=len(ctx),
                blocks_lost=self._evicted_blocks(ctx, cap),
                budget_overage=self.budget_overage(),
                unbounded_blocks=self.budget_unbounded(),
            )
        except Exception:  # noqa: BLE001 — observability never breaks a review
            pass

    def knowledge_budget(self) -> int:
        """Chars the principles TEXT may occupy, before the label.

        Its OWN budget as of 2026-08-04, defaulting to the historical
        `learning.max_context_chars // 4` so an unset config is byte-identical
        to before. Named rather than derived because the derived form hid how
        tight it was: `principles.md` had reached 906 of 1000 chars and the next
        principle anyone added would have been silently dropped — the same trap
        `news.memory.max_context_chars` was given its own budget to avoid.

        RAISED 2026-08-11, after the constraint above it was fixed. The old
        docstring said DELIBERATELY NOT RAISED, and it was right at the time:
        while the sub-budgets summed to 100% of the cap, every extra char here
        came off the tail and bought principles by losing the regime label.
        `budgets()` now names every block and `learning.max_context_chars`
        covers their sum, so growth here costs the tail nothing. §61.
        """
        fallback = self.lcfg.get("max_context_chars", 4000) // 4
        return int(self.llm_cfg.get("knowledge_max_context_chars") or fallback)

    def knowledge_block(self) -> str:
        """Static curated principles (knowledge/principles.md), config-gated.
        External and unverified — labeled so the judge weighs it below
        realized evidence. Missing/unreadable file degrades to empty.

        Divergence #15: the live judge reads this and `judge_model` cannot —
        it is a distribution stand-in with no prompt for text to attach to. Safe
        in direction only because invariant #2 lets the judge veto or downsize
        and never enlarge, so a principle can subtract live trades and never add
        one."""
        if not self.knowledge_path:
            return ""
        try:
            with open(self.knowledge_path) as f:
                text = f.read().strip()
        except OSError:
            return ""
        if not text:
            return ""
        return ("KNOWLEDGE (external, unverified — weigh below realized "
                "evidence):\n" + text[:self.knowledge_budget()])

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
        cap = self.budgets()["market_context"]
        return "\n".join(parts)[:cap]

    def book_block(self, positions: dict | None,
                   account: dict | None) -> str:
        """Deterministic snapshot of the CURRENT book for the judge — a risk
        desk never reviews a trade blind to the portfolio (2026-07-21).
        Broker-fresh data only (invariant #4); empty string when flat/absent."""
        if not positions or not account:
            return ""
        equity = account.get("equity") or 0.0
        gross = sum(p.get("market_value", 0.0) for p in positions.values())
        lines = [f"CURRENT BOOK ({len(positions)} open positions, gross "
                 f"exposure {gross / equity * 100:.0f}% of "
                 f"${equity:,.0f} equity):"]
        for sym, p in sorted(positions.items()):
            lines.append(f"  {sym}: qty {p.get('qty', 0):g}, value "
                         f"${p.get('market_value', 0):,.0f}, unrealized "
                         f"${p.get('unrealized_pl', 0):+,.0f}")
        cap = self.budgets()["book"]
        text = "\n".join(lines)
        return text[:cap] if cap else text

    def context_for_llm(self, symbol: str | None = None,
                        regime: dict | None = None,
                        strategy: str | None = None,
                        signal=None,
                        positions: dict | None = None,
                        account: dict | None = None) -> str:
        """Memory block for the judgment prompt: similar (or balanced) trades,
        ranked validated lessons (scope match: symbol > regime > strategy >
        global), the judge's own calibration, current regime.
        Hard-capped at learning.max_context_chars."""
        regime_label = regime["label"] if regime else None
        # With a live signal, show the MOST SIMILAR past trades instead of a
        # random balanced sample — same loser quota either way (invariant #5).
        trades = (self.similar_trade_sample(signal, regime_label)
                  if signal is not None else self.balanced_trade_sample())
        header = ("MOST SIMILAR PAST CLOSED TRADES (deterministic match on "
                  "strategy/regime/symbol — losses force-included on purpose):"
                  if signal is not None else
                  "RECENT CLOSED TRADES (balanced sample — losses included "
                  "on purpose):")
        lines = []
        for t in trades:
            lines.append(f"  [{t['result'].upper()}] {t['symbol']} {t['action']} — "
                         f"reason: {t['strategy_reason']} — P&L: {t['pnl_pct']}%")
        trade_block = "\n".join(lines) or "  (no closed trades yet)"
        budgets = self.budgets()
        if budgets["trades"]:
            trade_block = trade_block[:budgets["trades"]]

        now = datetime.now(timezone.utc)
        ranked = ranking.top_lessons(self.lessons.replay(), symbol, regime_label,
                                     self.lcfg.get("top_k_lessons", 8), now,
                                     strategy=strategy)
        lesson_block = ranking.format_lessons_block(
            ranked, regime_label, budgets["lessons"])

        replayed = self.judgments.replay()
        calib = calibration_line(calibration_metrics(replayed))
        if budgets["calibration"]:
            calib = calib[:budgets["calibration"]]
        scoreboard = recent_outcomes_block(replayed, 20)[:budgets["scoreboard"]]

        knowledge = self.knowledge_block()
        news = self.market_context_block(symbol)
        recall = self.news_memory_block(symbol)
        book = self.book_block(positions, account)
        dossier = self.research_block(
            symbol, getattr(signal, "action", None), positions,
            budgets.get("research"))
        regime_desc = regime_mod.describe(regime)
        if budgets["regime"]:
            regime_desc = regime_desc[:budgets["regime"]]
        ctx = ((f"{book}\n\n" if book else "")
               + f"{header}\n"
               f"{trade_block}\n\n"
               + (f"{dossier}\n\n" if dossier else "")
               + f"VALIDATED LESSONS (hypotheses with evidence counts n=supports/contradicts):\n"
               f"{lesson_block}\n\n"
               + (f"{knowledge}\n\n" if knowledge else "")
               + (f"{news}\n\n" if news else "")
               + (f"{recall}\n\n" if recall else "")
               + (f"{scoreboard}\n\n" if scoreboard else "")
               + f"{calib}\n"
               f"CURRENT REGIME: {regime_desc}")
        cap = self.lcfg.get("max_context_chars", 4000)
        if len(ctx) > cap:
            self._record_eviction(ctx, cap)
        return ctx[:cap]

    def research_block(self, symbol, action, positions, budget) -> str:
        """Per-symbol dossier (src/research.py), or "" if off/unavailable.

        Never raises. This sits on the path to every judge call, and the same
        rule `news_memory_block` states applies with more force here: a
        research layer that can abort a cycle is a research layer that costs
        trading days to gain context.

        Enabled by `research.enabled`, default TRUE. It makes no model call
        and no network call — every tool is retrieval over state this repo
        already keeps — so there is no cost argument for shipping it off.
        """
        if not symbol:
            return ""
        rcfg = (self.full_cfg.get("research") or {})
        if rcfg.get("enabled") is False:
            return ""
        try:
            import research
            brief = research.build(symbol, action or "", self.full_cfg,
                                   positions=positions,
                                   judgments=self.judgments.replay())
            return brief.to_block(budget or 900)
        except Exception:  # noqa: BLE001 — see docstring
            return ""

    def news_memory_block(self, symbol: str | None = None) -> str:
        """Accumulated news history for this symbol (W7), distinct from
        `market_context_block()` which shows only TODAY's flag.

        Carries its OWN budget (`news.memory.max_context_chars`) rather than
        sharing the learning cap. A new block without a budget of its own eats
        the tail of `max_context_chars` and silently truncates whatever sorts
        after it — a regression that would never show up in a diff, only in
        worse judgments. `tests/test_news_memory.py` asserts the lesson block
        does not shrink when this one is present.

        Empty string when disabled, absent, or unreadable. Never raises: this
        sits on the path to every judge call.
        """
        try:
            import news_memory
            # `self.cfg` is the MEMORY sub-config, not the whole config — the
            # same shape `market_context_block()` re-wraps above.
            return news_memory.format_block(symbol or "",
                                            {"news": self.news_cfg})
        except Exception:  # noqa: BLE001 — memory never breaks a review
            return ""
