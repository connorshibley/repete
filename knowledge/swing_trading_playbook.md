# Swing-trading playbook — distilled from SMB Capital (external knowledge)

**Source.** SMB Capital, *Trading Floor Podcast* Ep. 18 — "Beginner's Guide: The
4-step swing trading process (with EXACT strategy details)" (~1:16:02).
Speakers: **Tim** and **Garrett** (hosts) and **Ryan**, a veteran swing trader
on the SMB desk. Prop-desk, discretionary US-equity swing trading. Timestamps
below (`[MM:SS]`) point into that video.

This is *external knowledge, unverified by our own trade evidence* — same status
as `knowledge/principles.md`. Nothing here is adopted into Repete. See the
**translation layer** at the end for the narrow subset that could ever become a
gated candidate, and the large part that cannot.

---

## READ THIS FIRST — the discretionary caveat

The methodology in this video is **overwhelmingly discretionary**, and one of
the hosts says so explicitly. Tim, on a trader who wanted to fully automate it
[28:56]:

> "there's so much discretionary elements that go into stock selection for these
> swings. And if you don't have the theme correct, if you don't have the
> relative strength correct… it doesn't matter if you're waiting for a nice
> base. If it's a nice high-tight flag in the wrong stock, it's not going to
> work."

Repete is the opposite kind of system: deterministic signals, an LLM that may
only *shrink* risk and can **never generate a trade**, one frozen universe. The
edge these traders describe lives in exactly the parts a bot cannot do — reading
theme/narrative/sentiment, judging relative strength across a discretionary
watchlist, and grading trades by feel. **Most of this playbook is therefore NOT
automatable and NOT a Repete roadmap.** It is captured because (a) it is a good,
specific description of how professional discretionary swing traders actually
operate, and (b) a small, quantifiable subset maps to testable hypotheses.

---

## The 4 steps

1. **Review your biggest winners** [01:35]. Not your biggest *swing* winners —
   your biggest winners *period*, even day trades. Pull them up on the daily/
   weekly and notice how far many ran *after* you exited (Garrett's AMD: "never
   took out a prior bar low on the daily… never saw that price again" [03:04]).
   The ones with a strong contextual backdrop (higher-timeframe technicals +
   catalyst, traded on *day one* not day five) are latent swing trades you
   already know how to enter — "low-hanging fruit" [14:44]. If none of your
   plays have that character, that's fine — then step 3 is learning fresh.

2. **Get reps with small size** [08:41]. Don't swing for the fences. Hold a
   *small core* of a trade you already take: 5 shares overnight, then 10, then
   20 (~20% of the position) [09:39]. It "bridges the gap" so swing trading
   isn't a whole new discipline — you're just holding part of your existing
   trade [13:46].

3. **Learn your setups** [15:56]. **Swing trading is a *style*, not a setup**
   (Ryan, repeatedly [17:14]). "If you say I'm swing trading, that doesn't mean
   you're trading a specific strategy." Master **one** setup, backtest it, then
   leverage those skills into the next — don't try to master 5–7 at once
   [22:53]. Ryan's own path: small-cap low-float day-one short fades → the same
   skill on overextended large caps → today, mostly long-biased across caps/
   ETFs/indexes [20:00].

4. **Have a system to manage the trades** [61:23]. Stops, trailing, scaling —
   "you're never going to outsmart the system… I need those rules to protect
   myself from myself" [67:03].

---

## MCR — the momentum-to-core ratio [09:55]

Predetermine, **before day 1 ends**, how the entry lot splits:

- **Momentum** portion — sold into strength / momentum as the initial move
  slows.
- **Core** portion — held for the bigger multi-day/multi-week swing.

E.g. **60/40** momentum/core. It can flex mid-trade (60/40 → 80/20) but you
always keep a default baseline [13:24]. Its real value is psychological: taking
the momentum piece off is *following the plan*, not a mistake, so it "eliminates
a lot of FOMO" [12:36]. Set the ratio from how you expect it to move — explosive
one-day-then-unclear → bigger momentum lot; NVDA-style clean breakout → tiny
momentum lot, mostly core [11:57].

---

## Ryan's setups (with his exact criteria)

### A. Mean reversion / blow-off top [23:44]
- A stock in a *steady uptrend* that then **disconnects sequentially** from its
  key SMAs: first the **50-day**, then the **20-day**, then the **10-day**, and
  finally — the blow-off tell — the **5-day SMA**.
- Quantify the disconnect in **ATRs** (his rough figure: ~7–12 ATRs off the
  50-day; "everyone's a little different") [24:24].
- Confirm with **2–3 days of volume expansion AND range expansion** plus
  significant disconnect from the 5-day [24:44].
- Mental model: the **5-day SMA on the daily ≈ VWAP on the intraday** [25:04].

### B. Consolidation breakout — "the easiest, bearing the most fruit" [25:16]
Ryan's checklist, in order:
1. **Market context first** — do not buy breakouts in a bear market / correction
   [26:05].
2. **Leading sector** with excitement/euphoria (his 2025 examples: photonics,
   semis, memory, space, quantum, drones) [25:44].
3. **Not the already-extended name** — avoid stocks 100%+ over the 200-day that
   are themselves setting up for *mean reversion*; pick the names **holding up
   best** [26:27].
4. **Clean base + tight range** = defined risk/reward, with **converging
   short-term MAs (10-day & 20-day are critical for entry)** above a **rising
   200-day** [26:38]. The "rubber-band effect": the tighter the coil, the more
   stored energy [26:53].
5. **Character** — only if the stock has *historically* shown momentum on its
   breakouts. "If it's a nice high-tight flag in the wrong stock, it's not going
   to have the juice" (Tim) [29:11].
6. **Narrative/catalyst is the fuel** — earnings catalyst or a hot theme; the
   move needs *something* underneath it [30:29].
7. **Multi-timeframe alignment** (weekly base + daily + hourly levels line up)
   upgrades the grade toward A/A+ [38:35].

**Worked example — ARM [31:29]:** ~2-year weekly base after an earnings move;
ceasefire catalyst; broke ~190; then **3 weeks flagging above the breakout,
holding the top half**; 10-EMA > 20-EMA both rising; "surfing" the MAs, tight,
slight gap-up; broke out **May 20** into an NVDA earnings print. Tim entered a
**starter above 230** (mid-range, small), **added above 240** on the actual
breakout, then bought dips as behaviour confirmed.

### C. Continuation / trend-surf [53:56]
For a stock that has **already** broken out and you *missed* — and only if it
showed momentum + relative strength.
- Entry: wait for a pullback into the **rising 10- or 20-day SMA**, holding
  above the prior breakout level; confirm a **higher low** by taking out the
  **prior day's (or prior week's) high**.
- Brian Shannon's rule Ryan and Garrett both cite: **"don't buy the dip, buy the
  strength *after* the dip"** [58:52].

### D. Tactical day-2 pull-in [58:27]
Advanced, *not* a beginner setup. After a stock is the *strongest in the market*
/ had a day-1 catalyst, the next morning its prior-day levels often hold on a
pull-in and it resumes. Held ~1–3 days, sized like a day trade, entirely
dependent on a hot 1–3 day market window. Garrett is explicit it "cannot be
undervalued… without that overlay of the environment" [63:31].

---

## Grading trades — A+ / A / A- / B [32:57]

The **grade sets the size and the risk taken**. An A+ stacks: multi-timeframe
alignment + hot theme + catalyst + relative strength + clean base + historical
breakout momentum + market risk-on. Grade can be *upgraded intraday* as
behaviour confirms — Tim "graduated the risk" on ARM from A- to full size as the
volume and hold proved out [36:15].

- **ARM ≈ A-/A** — hot institutional theme, catalyst, clean 3-week flag, into
  NVDA earnings.
- **ONDS ≈ B** [41:46] — clean multi-month levels, a catalyst, gapping near the
  breakout, *but* no big theme/euphoria, no historical momentum, mid-range. So:
  trade smaller, **trim into ATR extension**, turn it risk-free, let a small
  core run.
- **Short-interest nuance** [43:44]: high short interest (ONDS ~20–25%) → a
  **1–2 day squeeze** dynamic, tactical, *not* a daily-chart trend trade. Low
  short interest + institutional theme (ARM) → a real trend you can project.

---

## Market environment — the master filter [45:20]

"**The environment's everything**" (their team leader, K. Fitz). When it fires,
be long; when it's murky, sit on your hands — don't chase entry perfection if
the environment is right [46:04].

**Garrett's 3 buckets** [50:47]:
1. **Breadth** — but only matters **tactically at turns**: a breadth *surge* off
   a bearish regime signals a real turn; breadth *deterioration* at a top
   foreshadows. Mid-trend, don't obsess — the biggest names can carry a narrow
   market far longer than feels right [52:03].
2. **Risk appetite** — *what's leading?* Offensive/high-beta (crypto, leaders)
   vs defensive ("toilet paper and cereal," healthcare). Crypto is a clean risk
   barometer [50:56].
3. **Market behavior** (his favorite) — **"are breakouts working?"** How do
   stocks react to good vs bad news? In some markets good news is *sold* [51:23].

**Ryan's failed-breakout barometer** [47:50]: track the 20–50 hottest names
across sectors; when *leaders start failing breakouts / losing their MAs*, it's
risk-off **even if the indices are still above their moving averages**. His
late-2024 tell: Bitcoin failed its breakout first, then quantum, then a cascade
of false breakouts.

---

## Risk & trade management [35:55], [61:23]

- **Risk to the *true* structural stop** (the inflection/structure low), not a
  tightened intraday stop. Tightening pulls you into overtrading something
  "acting totally fine"; take the wider stop and size for the grade instead
  [39:31]. You can always reduce on a *bad close* [41:00].
- **Breakout day-1 discipline**: don't *sell* breakouts on day 1, don't *trim*
  on day 1, don't *add* at new highs (it starts you mismanaging) [37:14].
- **Trim ~5–10% of the position per ATR of extension** [37:16].
- **Trailing menu** (setup-dependent): prior-day low; the 10- or 20-day SMA
  (mid/large caps); a **multi-day / anchored VWAP** (small caps, or anchored to
  the earnings day as a "line in the sand" — the good ones close back above it)
  [65:55].
- **"Last in, first out"** (K. Fitz) [69:31]: early in a fresh trend, hold with
  wide MA trailing stops for the whole move; when the market is **extended/
  mature**, keep new buys on a **tight leash** and cut quickly if they don't
  work — because "one big 2% market day and you lose everything" you made
  [72:51].
- **Single-stock lifecycle sizing** [74:29]: a breakout 50–100% above the
  200-day with 1–2 legs already in → tight leash; a breakout consolidating
  10–20% above a rising 200-day with converging MAs → risk to the consolidation
  low.
- **Beginners**: no margin; backtest; demo / very small size until results
  justify sizing up; "don't risk more than you want to lose" [73:37].

---

## Translation layer — what maps to Repete, and what doesn't

Rep's standing rules apply: **nothing is adopted without a pre-registered gate**
on the committed snapshot, and every gate result must survive the
`src/significance.py` bootstrap. The items below are **candidate hypotheses
only — NOT adopted, NOT shipped.**

### Quantifiable → could become a gated candidate

1. **ATR-disconnect mean-reversion filter** (setup A). The sequential 50→20→10→
   5-day disconnect, measured in ATRs, is fully deterministic and is a *more
   specific* entry/exit than the live `meanrev`'s RSI-2. Relates to the enabled
   `meanrev` strategy and `knowledge/principles.md`'s mean-reversion note. A
   §22+-style gate could test "ATR-disconnect from the 5-day" as an entry trigger
   or an exit. **Not adopted.**

2. **Market-regime / environment gate** (the master filter + "are breakouts
   working?"). This is the highest-value idea in the video for a systematic bot,
   *and* Repete already has the machinery: `tsmom.index_sma_period` (currently
   `0` = off) gates entries on the index vs its SMA. **Caveat, recorded honestly:**
   `backtest_candidates.md` §3 already *adopted then REVERTED* an index-regime
   gate — the in-sample win didn't replicate on a frozen snapshot and the
   original signal was API re-fetch drift. So this is a *re-test*, not a fresh
   idea, and it must run cleanly on the committed `data/snapshots/` file. **Not
   adopted.**

3. **Converging-MA + rising-200-day breakout precondition** (setup B, items 4–7).
   "10/20/50/100-day converging within a tight band, above a rising 200-day" is
   a quantifiable "coil" filter. Distinct from the **§17 Donchian breakout**,
   which was implemented, gated, and **FAILED** (kept `enabled: false`). Could be
   tested as a *precondition filter* on breakout entries. **Not adopted.**

4. **ATR-based scale-outs / SMA trailing** (management). "Trim per ATR of
   extension" and "trail the 10/20-day SMA" relate to **§7** (chandelier trail,
   adopted for tsmom) and **§16** (trail multiples, tested — the 2.5× arm beat
   the incumbent on every point estimate but sat inside the noise band, so 3.0
   stayed). A structured multi-tranche scale-out is a possible extension of
   `src/postexit.py`. **Not adopted.**

5. **Regime-conditional stop tightening** ("last in, first out"). Tighten the
   trail when price is far above its 200-day / the market is extended. Quant
   proxy: distance above the 200-day. A regime-conditional exit rule, testable
   with the ensemble simulator. **Not adopted.**

### Irreducibly discretionary → out of scope for the bot

These are the parts the video's own hosts describe as judgment, and a
deterministic swing bot cannot do them:

- **Theme / narrative selection** ("space, quantum, drones," "the talk of the
  town").
- **Sentiment / euphoria reads** and "is this institutionally hungry?"
- **Trade grading by feel** (A+/A-/B assigned from a gestalt of context).
- **Tape reading** — "bought like crack," "a buy program going," reading the
  opening drive.
- **Discretionary relative-strength stock picking** across a hand-built
  watchlist — Tim's [28:56] warning is precisely that this part does not
  automate.

**Bottom line for Repete:** the video is a strong description of *discretionary*
swing trading. Its transferable residue is a short list of quantifiable filters
— above all a **market-regime gate** — each of which must clear its own
pre-registered gate before it is anything more than a hypothesis. The parts that
make these traders money are, by their own account, the parts a bot can't copy.
