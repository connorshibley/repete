---
name: bot-candidate-intake
description: Use whenever an external trading idea arrives for any of the three bots — a downloaded skill, a paper, a blog post, an MCP server, a "proven backtested strategy", a YouTube method, or the owner asking "can we use this?". Produces a source review and a PARKED candidate. It never registers an EDGE claim, which matters because the EDGE budget is frozen and the project is hands-off.
---

# Candidate intake: how an outside idea enters this project

Outside ideas are welcome. Adopting them on the strength of someone else's
backtest is not. A publicly available "proven" strategy is, by construction, one
of the most data-mined artifacts in existence: it survived because it looked
good on the data its author had, and you are being shown the survivor.

This skill turns an idea into a **reviewed, parked candidate** with its
disposition written down. That is the whole deliverable. It does not run a gate.

## Step 1 — Is it even about this bot?

Most rejections happen here, and the reason is almost always the same: the thing
tests *a different system*.

The `source-review-tradingview-mcp.md` case is the template. 78 MCP tools, a real
product, genuinely useful to somebody — and useless here, because
`src/backtest.py` replays historical bars through **the same deterministic
functions the live bot calls** (`strategy.generate_signal`, `strategy.atr`,
`risk.size_order`, `risk.pure_checks`), with signals on the close of bar *i*,
fills at the **open of bar i+1**, and bracket legs checked pessimistically
intrabar. A strategy tested in TradingView runs Pine, not this code and not
these rails. A pass there says nothing about a pass here.

Ask, in order:

1. Does evaluating it require code the live bot does not run? → not portable.
2. Does it need data we do not have (point-in-time membership, fundamentals,
   order book, full-text news)? → record the data dependency and stop.
3. Which bot? `agiprolabs`-style crypto/DeFi/on-chain material belongs to
   **repete1**; FX carry and financing to **repete2**; equities to
   **trading-agent**. Cross-filing an idea into the wrong bot wastes a review.

## Step 2 — Has it already been rejected?

`knowledge/backtest_candidates.md` is ~5,400 lines and 50+ numbered sections.
Search it before writing anything. Ideas that arrive repeatedly and have already
been decided include: vol-regime stop widths (§2, rejected), regime gates on
tsmom (§3, adopted then reverted on API drift), per-cycle entry caps (§4,
rejected), gross-exposure caps in down regimes (§6, cannot bind), and
exposure-matched benchmarks (§48 showed any long-biased strategy passes).

If it duplicates a decided section, the review is one paragraph pointing at that
section. That is a complete answer.

## Step 3 — Write the source review

`knowledge/source-review-<slug>.md`, following the existing three
(`autonomoustrading-io`, `oria-one`, `tradingview-mcp`). The established shape:

- title, **Reviewed:** date, **Question asked:** the owner's actual words
- **Verdict** on line 4, in bold, before any discussion
- what the thing actually is, factually
- a table of its parts against *what it gives this repo*
- the blockers, numbered and independent — each one sufficient on its own
- what, if anything, was adopted

Write the verdict first. A review that reasons its way to a conclusion over
three pages is a review that can be argued into any conclusion.

## Step 4 — Park it, do not register it

Append to `knowledge/backtest_candidates.md` as **PARKED**, with:

- what the claim is, in one sentence
- what it would cost to test: which snapshot, which rule
  (`beats_benchmark_symbol`, never `beats_exposure_matched`), which arms
- what would have to be true first — usually "the §52 freeze lifts"
- the honest prior

**Do not run `register_gate.py`.** Two independent reasons:

1. **§52 freeze** — `register_gate.py` mechanically refuses `claim: EDGE` on
   `data/snapshots/`, and `--override-freeze` is not for getting a result you
   want.
2. **Hands-off since 2026-08-02** — the owner decided to stop building and wait
   for the decay monitor to reach n=20 closed trades (~early Sept). It was at 7.

A parked candidate loses nothing by waiting. A registered one spends K
permanently. See `bot-pre-registration` and `bot-survivorship-audit`.

## Step 5 — If it is a judgement, not a testable rule

Some good ideas are not gateable at all: they are heuristics about when to be
skeptical. Those belong in `knowledge/principles.md`, which is fed to the review
prompt labelled *"external, unverified — weigh below realized evidence"*.

That path is safe **only** because of invariant #2: the judge may VETO or
DOWNSIZE and may never enlarge or create a trade. So a principle can subtract
trades and can never add one.

It is also a **live-only** change the simulator can never reproduce — see
`docs/divergences.md` #15 and `bot-divergence-check`. Adding to that file means
opening or extending a divergence, not just editing a markdown list.

## What "adopted" would require

Nothing here adopts. For the record, adoption needs: the freeze lifted by a data
source that passes `scripts/probe_delisted_coverage.py`, a fresh registration
spending K, a pass on a survivorship-free benchmark, and the owner's decision.
Four things, none of them yours to skip.
