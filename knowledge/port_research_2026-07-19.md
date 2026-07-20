# Port research — six upgrades from the common-trade comparison (2026-07-19)

Evidence pass behind the six items ported/adapted from `eastyryan/common-trade`
(reviewed 2026-07-19). Inline web research; sources at the bottom of each
section. Labeled by evidence strength so nothing gets treated as more proven
than it is. Strategy-side items (§1–§3) go through the frozen-snapshot gate
(pre-registered rules in backtest_candidates.md §7–§9) — the literature only
picks the CANDIDATES; our own walk-forward decides adoption.

## 1. Trailing stop (chandelier exit) — evidence: MODERATE, momentum-conditional

- Kaminski & Lo, "When Do Stop-Loss Rules Stop Losses?" (J. Financial Markets,
  2014): under a random walk, simple stop rules always LOWER expected return;
  they add value only when returns have positive serial correlation
  (momentum). Empirically (daily futures 1993–2011) volatility-based stops
  raised monthly returns ~1.5pp and cut volatility.
- Han, Zhou & Zhu, "Taming Momentum Crashes: A Simple Stop-Loss Strategy"
  (2014/2016, US stocks 1926–2011): a stop overlay on cross-sectional momentum
  flipped skewness from −1.18 to +1.86 — the classic momentum left tail
  largely removed.
- Practitioner backtests (unverified, likely optimistic): chandelier
  (HH − 3×ATR) beat fixed trails on expectancy and maxDD in multi-method
  comparisons; 2.5–3×ATR repeatedly cited as the swing-horizon sweet spot.
- IMPLICATION FOR THIS BOT: theory predicts the trail helps tsmom (a momentum
  strategy) and does little or harm for meanrev (exits already fast:
  SMA-5 / 7-day max hold). Note our earlier walk-forwards REJECTED take-profit
  caps ("capping winners hurt") — a ratcheting trail is the mirror-image
  mechanism (caps losers' giveback, not winners' upside), so the TP result
  does not pre-judge this one.
- Sources: sciencedirect.com/science/article/abs/pii/S138641811300030X,
  papers.ssrn.com/sol3/papers.cfm?abstract_id=968338,
  dualmomentum.net/2015/06/13/momentum-and-stop-losses,
  quantifiedstrategies.com/chandelier-exit-strategy,
  volatilitybox.com/research/volatility-adjusted-stop-losses.

## 2. Risk-based (stop-distance) sizing — evidence: WEAK-TO-MODERATE, practitioner-standard

- The fixed-fractional-RISK school (Van Tharp, Elder, Turtles): size =
  (equity × risk%) / stop distance, so each trade risks the same equity
  fraction. Balsara (1992) is the usual citation for smoother equity curves;
  recent academic support is thin — the strong academic cousin is
  volatility-managed sizing (Moreira & Muir 2017), which we already gate-tested
  as §5 (adopted for meanrev only).
- Because both risk-sizing and vol-target normalize by realized volatility,
  the implementation makes them mutually exclusive per strategy (risk-sizing,
  when active, replaces the vol_target multiplier).
- Sources: quantstrategy.io/blog/the-power-of-fixed-fractional-position-sizing-calculating,
  trendsandbreakouts.com/position-sizing-methods, therobusttrader.com/position-size.

## 3. Re-entry cooldown — evidence: WEAK (hygiene, not alpha)

- No solid academic result; the case is churn/whipsaw reduction (repeated
  stop-outs on false signals compound transaction costs) and the general
  overtrading literature (Barber & Odean's "Trading Is Hazardous to Your
  Wealth" family). Treated as a hygiene candidate; weakest of the three,
  registered with the strictest do-no-harm rule.
- Sources: signalshieldhq.com/learn/trading-cooldown-rules,
  abovethegreenline.com/whipsaw-trading.

## 4. Post-exit runner tracking — evidence: PRACTITIONER (measurement, not signal)

- No academic literature to verify; this is journaling discipline made
  systematic (common-trade's best original mechanism). It creates the
  EVIDENCE for future exit-rule gate candidates (e.g. "meanrev's 7-day max
  hold leaves X% on the table on average") instead of tuning exits on vibes.
  Pure measurement, embargo-consistent (marks happen after close), feeds
  reports only — never sizing or signals.

## 5. Model-version fingerprint — evidence: METHODOLOGICAL (track-record integrity)

- Direct analog of clinical-trial protocol registration: a paper track record
  is only attributable if the decision surface that produced it can be named.
  Every config/code change otherwise silently blends rulebooks inside one
  P/L series (we shipped vol-target sizing mid-record on 07-18 — the record
  is ALREADY two models). Stamp-only here (no buy-blocking freeze):
  the gate process intentionally iterates; segmentation, not freezing,
  is the honest fit.

## 6. Secrets hygiene + citation-graded lessons

- OWASP GenAI Top 10 (2025): LLM01 prompt injection — indirect injection via
  external content (news headlines are exactly that surface) — and
  sensitive-information disclosure. Our judge holds NO file tools (API text
  in/out), so the common-trade exfil path (headlines → model with
  Read/Grep/WebFetch → .env) does not exist here; the cheap hardening is a
  regression test that secrets never enter the git tree.
- Citation-graded lessons: decision-time attribution (the judge names the
  lesson ids that drove a verdict; resolved outcomes then score those
  lessons) — cleaner evidence linkage than post-hoc matching alone. Analog of
  common-trade's "lessons are graded, not gospel" citation loop. Guarded
  against double-counting (a trade never evidences the same lesson twice).
- Sources: genai.owasp.org/llmrisk/llm01-prompt-injection,
  aembit.io/blog/owasp-top-10-llm-risks-explained.
