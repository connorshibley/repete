# §64 wave — spec design (drafted 2026-08-12, BEFORE any run)

Five candidate families from `research/candidates_2026-08.md`, all registered
as **DIAGNOSTIC** (spend no K), all frozen together before any is run, per
the two-stage screen-then-promote route the owner approved. Numbers below
are working names; real ids assigned at registration (drafts rule). §63 is
taken (probe write-up), so the wave registers as §64-§68.

## Shared mechanics

- Snapshots: `data/pit/bars_etf_<period>.json.gz` (SPY certified) for the
  SPY-only candidates, 4 periods; `data/pit/bars_<period>.json.gz` (6-symbol
  GEM files) for gem, 3 periods (2000-06 unreachable — EFA/AGG inceptions;
  the spec says so rather than stretching).
- Rate aux: `data/aux/irx_2000-01-01_2026-08-11.json.gz` (^IRX, percent,
  never revised). Feeds gem's T-bill threshold and the financing model.
- Every levered arm sets `risk.margin.multiplier` and pays financing at
  aux-rate + 150bps (fail-closed flat 6% if aux absent). **No levered arm is
  comparable to any published number — that is the point.**
- Benchmark clauses: `beats_benchmark_symbol: SPY` (total return — the
  owner's bar) AND `beats_benchmark_risk_adjusted` reported side by side;
  `maxdd_within` on levered arms; `min_trades` per candidate (low for
  regime switches — see each reading rule's episode honesty).
- Judge model: `false` for all five — these are mechanical ETF switches
  with no judgeable thesis per trade; declaring it and the reason here,
  before data. (§57 declared true for the incumbent ensemble; these specs
  ask a different, judge-free question.)
- The §48/§49 trap is pre-acknowledged: few switches = few independent
  bets. Each reading rule counts EPISODES, not just return deltas.

## s64 — trend_hold (Gayed 200-DMA leverage switch)

- Arms per period: `switch_1x` (multiplier 1.0), `switch_125` (1.25),
  `switch_150` (1.5), each = trend_hold enabled alone, spy_only universe,
  sized to full equity (`risk_per_trade_pct`/`max_position_pct` overlays).
- Grid (one spec, 2014-19 period — the known-hostile grind-up window):
  ma_days ∈ {100, 150, 200, 250} at 1.5x, clause `no_interior_optimum`.
  Choosing the hostile window for the grid is deliberate: a plateau there
  is evidence of robustness, a peak is evidence of fitting.
- **Prior (frozen)**: post-publication replication (2016-2026) at 1.5x with
  honest financing TRAILED SPY TR by ~2.2pp/yr. Expected outcome: total-
  return clause FAILS in 2014-19, PASSES at most in 2000-06/2007-13/2022-26
  (bear-containing windows); maxdd_within passes broadly. McLean-Pontiff
  haircut on the in-sample edge leaves ~1.4pp before financing.
- **Reading rule (frozen)**: total-return passes in ≥3 of 4 periods AND the
  grid shows no interior optimum → licenses exactly ONE EDGE registration
  for this candidate (K=16) via `--override-freeze`, supersession argument
  drafted below. 2 of 4 → record as regime-dependent, no EDGE, may inform
  a future combined spec. ≤1 of 4 → candidate closed for this programme.
  Risk-adjusted-only passes license NOTHING beyond a note (the owner's bar
  is total return).

## s65 — tom_tilt (turn-of-the-month leverage tilt)

- Arms per period: `no_tilt` (baseline: buy-and-hold SPY via trend_hold
  with ma_days=1? NO — baseline arm is plain SPY buy-and-hold expressed as
  the benchmark itself; first arm = tom_tilt alone at 1.0x base + 0.5x
  tilt... simplest honest arms:) `tilt_125` (base 1x + 0.25x TOM slice),
  `tilt_150` (base 1x + 0.5x TOM slice). The comparator is SPY B&H via the
  benchmark clauses, so no separate baseline arm is needed beyond the
  registered ensemble-off default.
- **Prior (frozen)**: McConnell-Xu — TOM window carries the whole 1926-2005
  equity premium; still present in 2026 international data. But calendar
  effects are the canonical mining graveyard, magnitude per window is
  small, and no post-2008 US-specific OOS study of this exact tilt exists.
  Honest expectation: small positive edge over B&H before costs, ~60bps/yr
  drag at 1.5x tilt; total-return clause is a coin flip per period.
- **Reading rule (frozen)**: beats SPY TR in ≥3 of 4 periods at either tilt
  level (same level across periods — no per-period level shopping) →
  licenses ONE EDGE registration (K=16). Else nothing. A pass driven by a
  single period's outlier (one period contributes >75% of the aggregate
  excess) does NOT count as a pass for that period-count.

## s66 — vol_lever (discrete Moreira-Muir slice)

- Arms per period: `slice_125`, `slice_150` (base 1x + slice when calm).
- **Prior (frozen)**: Cederburg — real-time Sharpe edge mostly evaporates;
  MM Table V — the 1.5x cap cuts E[R] below B&H's; Barroso-Detzel — market
  factor uniquely survives costs. Expected outcome: total-return clause
  FAILS most periods; maxdd_within PASSES most periods. This spec exists
  to measure the drawdown-truncation claim honestly, not to find alpha.
- **Reading rule (frozen)**: this candidate CANNOT license an EDGE
  registration regardless of outcome (pre-committed — its own literature
  predicts the total-return bar fails; a surprise pass on 4 periods of
  ~5 episodes each is exactly the luck the record warns about). Outcomes:
  maxdd_within ≥3/4 AND total-return ≥2/4 → note as a candidate *ensemble
  risk-overlay* for a future separately-registered question. Else closed.

## s67 — gem (dual momentum, book spec, falsification)

- Arms per period (3 periods only): `gem_book` (12-mo lookback, monthly,
  SPY/EFA/AGG, ^IRX threshold — the exact Antonacci spec, no variants).
  One arm. No grid — the ReSolve study is the reason in writing: 1,226
  siblings span 64pp of dispersion; a grid here would be spec-shopping
  with extra steps.
- **Prior (frozen)**: tracked OOS 2014-2026: 8.4% vs SPY 13.6%; 2022 haven
  failure (AGG -13%). Expected outcome: FAILS total return in 2014-19 and
  2022-26; 2007-13 is its one structurally-favorable window (long bear).
- **Reading rule (frozen)**: this is a falsification registration. FAIL in
  ≥2 of 3 → the family is CLOSED in this repo (parked note upgraded to a
  decided section; future GEM-shaped proposals get the one-paragraph
  answer). Pass in ≥2 of 3 → surprising; licenses nothing by itself, but
  reopens the family for a properly-powered future registration. No EDGE
  path from this spec.

## s68 — macro_gate (UNRATE-trend on vintages) — BLOCKED, registers last

- Cannot be registered until: FRED key obtained (owner), ALFRED loader
  built, `probe_alfred_vintages.py` PASSES on real vintages. If the probe
  refuses, this spec is never registered and the § records the refusal.
- Arms per period: `gate_1x` (UNRATE>12mo-MA blocks entries on trend_hold
  1x), `gate_150` (same on 1.5x). First-print vintages only, publication-
  lag honored (value known only from first Friday of M+1).
- **Prior (frozen)**: family post-publication record 0-for-2 (2022-24 curve
  false signal, Sahm 2024 false-ish trigger); UNRATE-trend specifically
  survived a first-print-vs-revised check (Philosophical Economics) but
  has ~1-2 switches per period. NFCI excluded entirely (pseudo-PIT before
  2011). T10Y3M excluded from the gate (its 2022-24 record refutes it as
  an equity gate; recording that costs nothing — it is already known).
- **Reading rule (frozen)**: with ~5 independent episodes across all
  periods, NO outcome of this spec alone licenses an EDGE registration.
  Purpose: measure whether the gate improves trend_hold's worst-period
  drawdown without giving up 2014-19. maxdd improvement in the two
  recession periods AND total-return give-up <2pp in 2014-19 → candidate
  for a future combined registration. Else closed.

## The supersession argument (drafted now, before any run, per the route)

If s64 or s65's promotion rule fires, the EDGE registration uses
`--override-freeze` with: "§57 spent the EDGE licence for the price-only
incumbent ensemble on this universe. This claim was screened by a frozen
DIAGNOSTIC whose promotion rule pre-committed exactly one EDGE registration
on passing; the claim brings a mechanism no prior registration examined
(financed leverage timing / calendar tilt), pays financing §57's incumbents
never modeled, and its own reading rule re-freezes the venue on failure.
K increments to 16 and the Bonferroni column updates everywhere it is
pinned." The override reason lands in registrations.jsonl beside the claim.

## What is deliberately NOT in this wave

Banded 130/30 (owner chose to skip), Sell-in-May (skipped), EDGAR (venue
refused twice — probe + literature), utilities-RS gate (parked as a possible
future confirm-signal arm), NFCI and T10Y3M gates (excluded with reasons
above). Nothing here touches the live ensemble; every strategy ships
`enabled: false` and the decay monitor's n=20 continues undisturbed.
