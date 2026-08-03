# Source review: tradingview-mcp (github.com/tradesdontlie/tradingview-mcp)

**Reviewed:** 2026-08-03
**Question asked:** "Is it possible to connect this MCP and start backtesting
different trade strategies?"
**Verdict: NOT connected, and it would not backtest anything if it were.**

## What the server actually is

78 MCP tools that drive a **locally running TradingView Desktop application**
over the Chrome DevTools Protocol on `localhost:9222`. Node.js, built on
`chrome-remote-interface`. It never talks to TradingView's servers — it
automates the app on your own machine, through undocumented internal interfaces
its own README says "may change or break at any time."

| Tool category | What it does | What it gives this repo |
|---|---|---|
| Chart reads (`data_get_ohlcv`, `quote_get`) | scrapes bars off a chart | worse than `data/snapshots/` — see (c) |
| Pine Script (`pine_smart_compile`, `pine_save`) | authors/compiles Pine | nothing; the bot is Python |
| Pane control, drawings, alerts | arranges charts, draws | nothing; `src/dashboard.py` covers charts |
| Replay mode (`replay_step`, `replay_trade`) | steps bars, simulates positions **in the app** | not a backtest — see (a) |
| Streaming (polling) | watches quotes/values | nothing; the cycle is scheduled, not streamed |

There is no backtest tool in the list. Replay mode moves a playhead and lets you
place simulated orders by hand; it does not score a strategy.

## Three independent blockers

**(a) It cannot backtest *this bot*.** `src/backtest.py` replays historical bars
through the *same deterministic functions the live bot calls* —
`strategy.generate_signal`, `strategy.atr`, `risk.size_order`,
`risk.pure_checks` — with a fee/slippage model, signals computed on the close of
bar *i* and filled at the **open of bar i+1**, and bracket legs checked
pessimistically intrabar (stop before take-profit when both touch). A strategy
tested inside TradingView runs *Pine*, not this code and not these rails. A pass
there would say nothing about this bot. §43 exists because a simulator that does
not match live is worthless.

**(b) The prerequisites are absent and are not free.** TradingView Desktop is
not installed on the deployment machine. The server needs a paid TradingView
subscription plus paid data access, and needs the app launched with its
remote-debugging port open. That port is an **unauthenticated local control
channel**: any process that can reach `:9222` can drive the application. That is
a change to the security posture of a machine holding broker credentials, and
not one to make for a capability the repo already has.

**(c) The data would be worse than what is already frozen.** `data/snapshots/`
holds five hash-pinned snapshots (2000–2026, 38 / 68 / wide universes) with
vendor and adjustment policy recorded per file in `MANIFEST.json`. That manifest
records why the discipline matters: an early intraday build used
`adjustment=RAW`, carried 11 unadjusted splits the simulator read as overnight
collapses, and **voided a gate run**. Chart OHLCV scraped over CDP carries no
hash, no manifest, and no reproducible adjustment policy.

## The objection that matters most

The bottleneck here has never been the ability to try more strategies.

§32 measured a Spearman rank correlation of **−0.543** between in-sample and
out-of-sample profit factor across six arms; the in-sample best finished fourth.
§33 was written to ask whether the selection procedure ranks *at all*, because
if it does not, more hunting only buys more chances at a false positive.

So everything here is counted. `gatespec.canonical_sha256()` freezes a claim
before it runs, `scripts/register_gate.py` records the hash, `scripts/run_gate.py`
refuses to score a spec whose hash moved, and every EDGE claim spends a
Bonferroni budget (`bonferroni_k` in the spec). §33 RUN 1 is the standing
reminder of the alternative: it printed VALIDATED and was an artifact.

An interactive, chat-driven "try this on the chart, now try that" loop is an
**uncounted multiple-comparisons machine**. Every arm tried and silently
discarded in conversation is a trial that never reaches the budget. That is not
a tooling gap this repo has — it is the failure mode its machinery was built to
prevent.

## How to actually test a new strategy here

Write the pre-registration as data, freeze it, then run it:

    $EDITOR research/specs/<id>.yaml        # copy research/specs/s44a.yaml
    python scripts/register_gate.py <id>    # records the spec hash — the goalpost
    python scripts/run_gate.py <id>         # refuses to score if the hash moved

`prior` and `failure_modes` are mandatory and carry no machine meaning: a spec
that does not say what the author expected, and how the result could fool them,
is not a pre-registration. §36 built this path specifically because *writing
runners* was the bottleneck, not compute — §32 scored 500 symbols in 13m35s.

## If you want it anyway

Buying TradingView Desktop as a **human** charting and Pine-sketching tool is a
perfectly good reason to buy it. Sketch an idea there, then bring the *rule* back
as a spec and let the gate decide. What must not happen is the MCP sitting in the
agent loop, where trials stop being counted.

Nothing enabled. No config changed. No dependency added. The EDGE record is in
`backtest_candidates.md`; it is not restated here, because duplicated tallies
drift.
