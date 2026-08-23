# Source review: downloadable Agent Skills for trading

**Reviewed:** 2026-08-04
**Question asked:** "I want to look into skills.md files I can give to this
agent so it can be even better... more knowledge... more backtested, proven
strategies... more news articles it can read and act on, and just skills that
already exist that are easy to download."
**Verdict: three of the four public trading-skill collections were NOT adopted.
Four utility skills from `anthropics/skills` were, and the crypto subset of one
collection was routed to repete1. What this repo actually needed was six skills
written from its own rules, which no download contains.**

## What a skill is, and what it is not, for this project

A `SKILL.md` is instructions loaded by an **agent** — Claude Code working on the
repo. **The bot is Python and has no skill loader.** Its LLM touchpoints are
`judge_model.py` (a distribution stand-in, no prompt), the news distiller in
`llm.py`, `learn.py`, and the blog. So "give the agent skills" and "give the bot
knowledge" are two different requests with two different mechanisms, and only
the second one can affect a trade.

The bot's actual knowledge channel is `knowledge/principles.md` →
`memory.knowledge_block()` → the judge. It was **906 of 1000 chars** when this
review started. See divergence #15.

## The four collections

| collection | size | licence | verdict |
|---|---|---|---|
| `anthropics/skills` | 17 | — | **PARTIAL ADOPT** — skill-creator, xlsx, pdf, mcp-builder |
| `agiprolabs/claude-trading-skills` | 67 | MIT | **1 of 67 ADOPTED, into repete1** |
| `shakeebshaan/claude-code-quant-skills` | 6 | MIT | **READ, NOT INSTALLED** |
| `tradermonty/claude-trading-skills` | 70+ | MIT | **REJECTED** |

### `shakeebshaan` — the closest fit, and behind us

Its `/backtest-review` is a ~650-word checklist with seven checkpoints:
look-ahead bias, survivorship bias, overfitting (>1 parameter per ~1000 samples),
transaction costs, regime dependence, position sizing, execution realism. It
ends in SHIP / FIX / SCRAP.

Every one of those seven is already **mechanised** here rather than checklisted:

| its checkpoint | our mechanism |
|---|---|
| look-ahead bias | fills at the open of bar *i+1*; `probe_fmp_lookahead.py` |
| survivorship | `survivorship_check.py`, quantified at up to +200.28pp; §52 freeze |
| overfitting | `bonferroni_k`, SHA-256 pre-registration, `run_gate.py` hash refusal |
| transaction costs | fee + slippage model in `backtest.py`, measured fill quality live |
| regime dependence | four-period conjunctions (§37/§44/§48/§50) |
| position sizing | `risk.size_order`, shared by sim and live |
| execution realism | stop-before-take-profit intrabar; divergence register |

Installing it would put a weaker restatement of our own rules in front of a
future session, competing with the stronger one. **Read once as a coverage
cross-check; it named no bias category this repo does not already test.** That
cross-check is the value, and it is now spent.

### `tradermonty` — the wrong paradigm

70+ skills for a **discretionary human trader**: VCP and CANSLIM screeners,
FINVIZ URL builders, chart-image technical analysis, trade journaling, "discipline
gates". Roughly 30 require an FMP key; the theme detector wants FINVIZ Elite at
$39.50/mo.

This bot is deterministic. A skill that helps a person *decide* has nothing to
attach to in a pipeline where `strategy.generate_signal` decides and the LLM may
only veto. And FMP remains an open owner decision (§46), so ~30 of them are
inert here regardless.

### `agiprolabs` — one skill in sixty-seven, and the first read of it was wrong

67 skills, MIT, genuinely competent. The centre of gravity is Solana/DeFi —
Birdeye, DexScreener, Helius, PumpFun, Jito bundles, LP maths, impermanent loss,
MEV, sybil detection — so the initial call was "route the crypto subset to
repete1."

**That was wrong, and checking `repete1/CLAUDE.md` is what corrected it.**
Repete1 is a **Kraken spot** bot with a real order book, not a Solana DEX bot.
Its `slippage-modeling` skill derives cost from constant-product AMM bonding
curves, and its `market-microstructure` skill opens by stating *there are no
orderbooks on AMMs*. Adopting either would corrupt precisely the thing repete1's
§22 exists to fix — the flat 52 bps round trip that **undercharges 12 of its 21
markets**. A wrong cost model is worse than no skill.

Rejected for repete1: ~24 Solana/DEX, 5 prediction-market (Kalshi, Polymarket),
7 US tax and regulatory (it has never traded real money), and ~13 that duplicate
apparatus repete1 already owns and trusts more — `src/fills.py`, the walk-forward
gate, the moving-block bootstrap, the Thompson allocator.

**Adopted: `market-microstructure-traditional`, one skill.** It is limit-order-book
theory for *centralized* exchanges — quoted vs effective spread, the
Roll / Glosten-Harris decomposition, order-book imbalance, execution quality,
plus `scripts/spread_analysis.py`. That is §22's subject. Vendored to
`repete1/.claude/skills/` with a `PROVENANCE.md` recording the licence and the
caveat that **§22 is blocked on hardware, not on knowledge** (§28).

Nothing from this collection belongs in repete, and installing it globally
would fire crypto instructions during equity work.

### `anthropics/skills` — adopted, and none of it is about trading

`skill-creator` (authoring), `xlsx` and `pdf` (they replace the hand-rolled
openpyxl + headless-Chrome timesheet builder), `mcp-builder` (optional). Useful
precisely because they are generic utilities with no opinion about markets.

## What was built instead

Six skills, from this repo's own record, in `.claude/skills/` and symlinked into
`~/.claude/skills/` so repete1 and repete2 sessions get them:

`bot-pre-registration`, `bot-survivorship-audit`, `bot-prove-it`,
`bot-candidate-intake`, `bot-divergence-check`, and `timesheet`.

None of this is downloadable. The +200.28pp figure, the SBNY ticker-reuse trap,
the §52 freeze, the mutation protocol, the pbcopy incident — all local, all
learned expensively, and all previously re-derived from scratch every session.
That is §36's argument applied to documentation, and
`tests/test_skills_are_current.py` is what stops the skills going stale: every
path, gate rule and claim type they name is asserted to exist.

## On "more backtested, proven strategies"

Not adopted, and none parked from these collections either — nothing in them is
a strategy *claim* about equities that this repo could score.

The general answer stands regardless of source: a publicly available "proven"
strategy is one of the most data-mined artifacts in existence — it survived
because it looked good on its author's data, and what you are shown is the
survivor. This repo cannot currently tell such a claim from noise, because
`probe_delisted_coverage.py` REFUSED and the EDGE budget is frozen (§52). The
intake path for the next one is `bot-candidate-intake`: review, park, do not
register.

## What was adopted into the bot

- **`knowledge/principles.md`** rewritten from 8 principles to 10 within the
  same 1000-char budget — the redundant header line was the space. New material:
  correlated positions are one bet (§49), a short record with a small drawdown
  has been lucky rather than proven safe (§51), and a headline everyone has read
  is priced.
- **`llm.knowledge_max_context_chars`** made explicit. It was derived and nearly
  binding; the next principle appended would have been silently dropped.
- **Divergence #15** registered — open since 2026-07-16, unnoticed for nineteen
  days, through the creation of the divergence register itself.
- **One news source added: `Fed`** (federalreserve.gov press feeds), verified
  through `market_context._fetch_rss_source`. **SEC EDGAR and BLS both 403** and
  are recorded as rejected in `config.yaml` alongside Reuters, Barron's and AP.

## Sources

- https://github.com/anthropics/skills
- https://github.com/agiprolabs/claude-trading-skills
- https://github.com/shakeebshaan/claude-code-quant-skills
- https://github.com/tradermonty/claude-trading-skills
