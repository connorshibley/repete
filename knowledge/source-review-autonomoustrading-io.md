# Source review: autonomoustrading.io knowledge base

**Fetched:** 2026-07-16
**Status: NOT a usable methodology source.** All external claims here are UNVERIFIED.

The owner asked to build a knowledge base from this site (starting from
`/knowledge-base/ideal-user-profile-who-intuitive-code-serves-best/`). Seven
pages were fetched and reviewed, including every article whose title promised
concrete methodology. Findings, per page:

| Page | Verdict |
|---|---|
| Ideal User Profile | Marketing — audience-qualification content, no methodology |
| Rethinking Stop Losses | Paywalled sales page; critiques static stops, discloses no alternative |
| Stateful Stop Loss Intelligence ("PADSL") | Paywalled; acronym introduced, never defined |
| Earnings Participation Rules | Paywalled; thresholds and categories all withheld |
| Adaptive Scaling Strategy (Python guide) | Paywalled; no code in the public portion |
| Kasparov Move (4-phase framework) | Names four phases (STOP/FOCUS/SEE/PLAY); no criteria |
| Short-Squeeze Protection Algorithm | 3-step framework promised; step 1 vague, 2 truncated, 3 absent |

Every substantive-sounding article cuts off at "This post is for subscribers
on the Education Access tier only." The public content is a conversion funnel,
not a knowledge base. **No formulas, thresholds, rules, or code were
extractable from any page.**

## Salvageable general principles (public portions only)

These are generic, widely known ideas — none original to this site, and none
verified by it. Where noted, our system already implements them:

1. **Define invalidation before entry, not after.** Stops should encode "the
   thesis is wrong," set at entry time — not placed later out of fear.
   *Already implemented:* ATR stops are computed deterministically at entry,
   before any LLM involvement.
2. **Static tight stops whipsaw in normal volatility.** Stop distance should
   scale with volatility. *Already implemented:* 2×ATR stops.
3. **Earnings events break stop assumptions.** Gaps replace continuous price
   discovery; stops may fill far beyond their trigger. → Testable candidate:
   earnings-blackout entry filter (see backtest_candidates.md).
4. **Doing nothing is often the correct decision.** *Already implemented:*
   hold is the default; the judge can only approve/downsize/veto, never force
   a trade.
5. **Universe discipline:** trade liquid, institutionally held names.
   *Already implemented:* 25 large-cap universe.

Nothing from this site feeds the lesson book (closed-trade evidence only) and
nothing changes live config without passing the backtest enablement gate.
