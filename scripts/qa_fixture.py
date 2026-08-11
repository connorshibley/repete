#!/usr/bin/env python3
"""Sanitized, production-SCALE fixture for end-to-end QA of the publisher.

Why this has to exist
---------------------
The live ledger is 11 days and 1 closed trade. `publisher/gates.py` requires
**30 closed trades and 90 days** before checkout opens (invariant #10), so on
real data the entire paid path — checkout, entitlements, webhooks, the paid feed
and the paid dashboard — is **structurally unreachable**. It has never been
exercised against realistic data. That is precisely where defects live.

This generates ~18 months at the live bot's observed decision rate: tens of
thousands of records, dozens of closed trades, every strategy, both regimes,
every judge verdict, plus a subscriber DB covering anon / free / paid /
unsubscribed / expired.

Sanitized by construction
-------------------------
Every email is `@example.invalid` (RFC 2606 — can never route). Every token is a
throwaway. No real key, address, or account number is generated or read. The
data is obviously synthetic: symbols are real tickers because the code parses
them, but every price, fill and P&L is fabricated.

Deterministic
-------------
Seeded; the same seed AND the same --anchor give byte-identical output, so a
bug found here is reproducible by anyone with the same command. (publisher_data/
pub.db is excluded from that promise: SubscriberDB stamps grant expiries from
the wall clock. Compare the JSONL and the markdown, not the DB.)

Four profiles, because one fixture cannot cover the state space
---------------------------------------------------------------
At 450 closed trades every "not yet meaningful" guard is satisfied and every
empty state is unreachable, so the copy a brand-new user sees is exactly the
copy no fixture exercises. --profile picks the corner:

    full     ~18 months, revenue gate OPEN, every region populated
    thin     3 closed / 12 days — pending ratio cards, tiny lists, gate CLOSED
    empty    day one — every "No X yet" string
    hostile  small and adversarial — markup, quotes, RTL, 4k fields,
             javascript: links, fragment-hostile trade ids

    python scripts/qa_fixture.py --out /tmp/qa                      # build
    python scripts/qa_fixture.py --out /tmp/qa --summary            # no write
    python scripts/qa_fixture.py --out /tmp/qa --profile hostile
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "XOM", "JNJ",
           "KO", "PG", "JPM", "V", "CAT", "LLY", "GS", "AMD", "XLV", "IWM"]
STRATEGIES = ["tsmom", "meanrev", "ma_crossover"]
REGIMES = ["up/low", "up/high", "down/low", "down/high"]
VERDICTS = ["approve", "downsize", "veto"]
NEWS_SOURCES = ["rss=12", "fmp=4", "alpaca=0", "sec=2"]

# RFC 2606 reserved TLD: these addresses cannot be resolved or delivered to.
DOMAIN = "example.invalid"

# Strings that have to survive the renderers without becoming markup. Every one
# of these reaches the DOM through a path that only html.escape() defends:
# symbols and reasons land in table cells, journal text lands in <div class=body>,
# a post link lands in an href. A fixture made only of well-behaved tickers
# proves nothing about that.
HOSTILE_TEXT = [
    "<img src=x onerror=alert(1)>",
    "</td><td>injected cell",
    'double " quote and \' apostrophe',
    "RTL ‮override‬ tail",
    "<script>alert('xss')</script>",
    "ampersand & entity &amp; already-escaped &lt;b&gt;",
    "A" * 4000,                     # unbounded field in a fixed-width cell
]
# A javascript: URL in a post link. blog.py escapes the TEXT of a post but
# builds <a href="{url}"> from the same field with no scheme check, so this is
# the one hostile value that could become executable rather than merely ugly.
HOSTILE_LINK = "javascript:alert(document.domain)"
# '#' and a space in a trade_id: journal.html addresses entries as
# journal.html#<trade_id>, so a fragment-hostile id breaks the only deep-link
# surface in the project.
HOSTILE_TRADE_ID = "t#0001 spaced"

# Profiles exist because ONE fixture cannot cover the degraded states. At 450
# closed trades every "not yet meaningful" guard is satisfied and every empty
# state is unreachable — so the copy a new user actually sees on day one is
# exactly the copy no fixture ever exercises.
PROFILES = {
    # ~18 months at the live bot's observed rate. Revenue gate OPEN.
    "full":    {"days": 550, "decisions": (45, 62), "max_open": 8,
                "n_lessons": 24, "hostile": False},
    # Below MIN_CLOSED_FOR_RATIOS (10) and below the 5 closed trades the bar
    # chart needs, and under the gate's 90 days. Pending cards, tiny lists.
    "thin":    {"days": 12,  "decisions": (3, 6),   "max_open": 3,
                "n_lessons": 2,  "hostile": False},
    # Day one. Every "No X yet" string, and the <2-point chart copy.
    "empty":   {"days": 0,   "decisions": (0, 0),   "max_open": 0,
                "n_lessons": 0,  "hostile": False},
    # Small but adversarial — the risk-based edge cases, not a scale test.
    "hostile": {"days": 20,  "decisions": (4, 8),   "max_open": 4,
                "n_lessons": 3,  "hostile": True},
}


def _repo_roots() -> list[str]:
    """Every checkout of this project that must be protected — this one, and
    the MAIN checkout when this one is a git worktree.

    Resolving the root from __file__ alone is not enough, and the failure is
    silent: run from .worktrees/qa-sweep, `__file__/..` is the worktree, so
    `<worktree>/.site` is guarded while the real published mirror at
    `<main>/.site` is wide open. That is not hypothetical — on 2026-08-11 a
    guard written that way accepted `--out <main>/.site` and wrote nine fixture
    files into the live GitHub Pages checkout on the first try.

    A worktree's `.git` is a FILE containing `gitdir: <main>/.git/worktrees/<n>`,
    so the main root is that path's third parent. No subprocess, no git
    dependency, and it degrades to "guard what we can see" if the layout is
    unfamiliar.
    """
    here = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
    roots = [here]
    dotgit = os.path.join(here, ".git")
    if os.path.isfile(dotgit):
        try:
            with open(dotgit) as f:
                head = f.read().strip()
            if head.startswith("gitdir:"):
                gitdir = head.split(":", 1)[1].strip()
                if not os.path.isabs(gitdir):
                    gitdir = os.path.join(here, gitdir)
                # <main>/.git/worktrees/<name> -> <main>
                main = os.path.realpath(
                    os.path.join(gitdir, os.pardir, os.pardir, os.pardir))
                if os.path.isdir(os.path.join(main, ".git")):
                    roots.append(main)
        except OSError:
            pass
    return roots


def _guard_not_live(out_dir: str):
    """Refuse to write anywhere near the real track record or a live tree.

    The live ledger is the one artifact in this project that cannot be
    rebuilt. A fixture generator pointed at it would destroy the forward
    record with synthetic noise and look like it worked.

    memory/ was the original guard. The repo ROOT is guarded too because this
    checkout IS the deployment (launchd reads src/main.py from the tree) and
    publish.out_dir is "." — so "write the fixture next to the code" and
    "overwrite the published dashboard" are the same command. .site/ is the
    published GitHub Pages mirror and publisher_data/ holds the subscriber DB.
    """
    target = os.path.realpath(out_dir)
    for repo in _repo_roots():
        forbidden = [
            ("the live memory/ directory", os.path.join(repo, "memory")),
            ("the published site mirror .site/", os.path.join(repo, ".site")),
            ("the live publisher_data/", os.path.join(repo, "publisher_data")),
            ("a repository root (this checkout is the deployment)", repo)]
        for label, path in forbidden:
            if target == path or target.startswith(path + os.sep):
                raise SystemExit(
                    f"REFUSING to write fixture data into {label}\n"
                    f"  target:    {target}\n"
                    f"  forbidden: {path}\n"
                    f"This would overwrite live state or a published artifact.")


def build_ledger(rng: random.Random, days: int, start: datetime,
                 knobs: dict | None = None) -> list[dict]:
    """~18 months of cycles at the live bot's observed rate (~56 decisions/day).

    Shapes mirror src/ledger.py exactly: decision / outcome / event /
    fill_quality, with the same keys the publisher and dashboard read.

    `knobs` is a PROFILES entry; the default reproduces the original behaviour
    so existing callers are unaffected.
    """
    knobs = knobs or PROFILES["full"]
    lo, hi = knobs["decisions"]
    max_open = knobs["max_open"]
    hostile = knobs["hostile"]
    symbols = (SYMBOLS + [HOSTILE_TEXT[0]]) if hostile else SYMBOLS

    records: list[dict] = []
    open_trades: list[dict] = []
    equity = 100_000.0
    n_closed = 0
    hostile_id_used = False

    # Inclusive of the anchor day. Stopping one short left the newest
    # cycle_complete on the day BEFORE the anchor, which src/health.py reads
    # as "cycle ran today but never completed" and /healthz answers 503 — so
    # the publisher's healthy path was unreachable from any fixture.
    for d in range(days + 1):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:                      # weekdays only, like the bot
            continue
        regime = rng.choice(REGIMES)

        # Morning read, logged before the cycle. The "| sources: " suffix is
        # what dashboard.py:1239 splits on for the news-sources line, and the
        # whole detail is what blog.py renders as the day's Morning read — so
        # a fixture without these leaves two user-visible regions permanently
        # blank and untestable.
        summary = (rng.choice(HOSTILE_TEXT) if hostile else
                   f"Futures {rng.choice(['firm', 'soft', 'mixed'])}; "
                   f"{regime} regime; breadth "
                   f"{rng.choice(['narrow', 'broad'])}.")
        records.append({
            "type": "event", "event": "market_context",
            "detail": f"{summary} | sources: " + ", ".join(NEWS_SOURCES),
            "ts": day.replace(hour=13, minute=15).isoformat(),
        })

        # --- exits first, mirroring reconcile-then-trade cycle order ---
        for t in list(open_trades):
            age = (day - datetime.fromisoformat(t["ts"])).days
            if age >= rng.randint(3, 25):
                exit_px = t["entry_price"] * (1 + rng.uniform(-0.09, 0.13))
                pnl = (exit_px - t["entry_price"]) * t["qty"]
                equity += pnl
                records.append({
                    "type": "outcome", "trade_id": t["trade_id"],
                    "exit_price": round(exit_px, 2), "pnl": round(pnl, 2),
                    "pnl_pct": round((exit_px / t["entry_price"] - 1) * 100, 3),
                    "result": "win" if pnl > 0 else "loss",
                    "exit_reason": rng.choice(
                        ["strategy_sell", "stop_loss", "take_profit"]),
                    "ts": day.replace(hour=19, minute=45).isoformat(),
                })
                open_trades.remove(t)
                n_closed += 1

        # --- the day's decisions: mostly holds, like the real ledger ---
        for i in range(rng.randint(lo, hi)):
            sym = rng.choice(symbols)
            strat = rng.choice(STRATEGIES)
            ts = day.replace(hour=19, minute=45,
                             second=rng.randint(0, 59)).isoformat()
            roll = rng.random()
            reason = (rng.choice(HOSTILE_TEXT) if hostile
                      else f"{strat} entry signal on {sym}")

            if roll < 0.72:                          # hold
                records.append({
                    "type": "decision", "trade_id": f"h{d:04d}{i:03d}",
                    "regime": regime, "strategy": strat, "symbol": sym,
                    "action": "hold", "strategy_reason": "no entry conditions",
                    "indicators": {"rsi2": round(rng.uniform(5, 95), 1)},
                    "llm_review": None, "executed": False, "detail": "",
                    "order": None, "entry_price": None, "qty": None,
                    "outcome": None, "ts": ts,
                })
                continue

            verdict = rng.choices(VERDICTS, weights=[46, 52, 2])[0]
            review = {
                "verdict": verdict,
                "scale": 1.0 if verdict == "approve" else rng.choice([.4, .5, .6, .7]),
                "confidence": rng.choice(["low", "medium", "high"]),
                "reasoning": (rng.choice(HOSTILE_TEXT) if hostile else
                              f"{sym} {strat} setup assessed against "
                              f"{regime} regime."),
                "bull_case": f"{sym} trend intact; volume supports continuation.",
                "bear_case": f"{sym} extended; {regime} regime argues caution.",
                # Stated win-probability. The ledger's llm_review carries the
                # coarse label above; judgments.jsonl carries this float, which
                # is what confidence_calibration() buckets.
                "stated_confidence": rng.choice([.55, .6, .65, .7, .75, .8, .85]),
            }
            # A zero/negative price is a division hazard the dashboard guards
            # against (dashboard.py:926,945,949) — prove the guard, don't
            # assume it.
            price = (rng.choice([0.0, -12.5, 0.01]) if hostile and roll > 0.90
                     else round(rng.uniform(45, 620), 2))

            if verdict == "veto" or roll > 0.93:      # blocked entry
                records.append({
                    "type": "decision", "trade_id": f"b{d:04d}{i:03d}",
                    "regime": regime, "strategy": strat, "symbol": sym,
                    "action": "buy", "strategy_reason": reason,
                    "indicators": {"rsi2": round(rng.uniform(2, 15), 1)},
                    "llm_review": review, "executed": False,
                    "detail": "LLM veto" if verdict == "veto"
                              else "risk rejection: max open positions reached (8)",
                    "order": None, "entry_price": None, "qty": None,
                    "outcome": None, "ts": ts,
                })
                continue

            if len(open_trades) >= max_open:
                continue
            qty = max(1, int(2000 / price)) if price > 0 else 0
            tid = f"t{d:04d}{i:03d}"
            # The FIRST executed trade, whichever day it lands on. Keying this
            # to a fixed day meant the id was never generated at all — days 0,
            # 2, 13, 15 and 19 had executions and day 1 did not — so the
            # journal-anchor criterion passed without ever seeing a hostile
            # id. A criterion that passes because the fixture never produced
            # the input is worse than no criterion.
            if hostile and not hostile_id_used:
                tid = HOSTILE_TRADE_ID
                hostile_id_used = True
            rec = {
                "type": "decision", "trade_id": tid, "regime": regime,
                "strategy": strat, "symbol": sym, "action": "buy",
                "strategy_reason": reason,
                "indicators": {"rsi2": round(rng.uniform(2, 15), 1)},
                "llm_review": review, "executed": True, "detail": "",
                "order": {"id": f"o-{tid}", "stop_price": round(price * .94, 2)},
                "entry_price": price, "qty": qty,
                "trade_plan": {"thesis": f"{strat} continuation",
                               "expected_hold_days": rng.randint(3, 20)},
                "outcome": None, "ts": ts,
            }
            records.append(rec)
            open_trades.append(rec)
            records.append({
                "type": "fill_quality", "trade_id": tid, "symbol": sym,
                "side": "buy", "signal_price": price,
                "filled_avg_price": round(price * (1 + rng.uniform(-.001, .003)), 2),
                "slippage_bps": round(rng.uniform(-8, 32), 2), "ts": ts,
            })

        records.append({
            "type": "event", "event": "cycle_complete",
            "detail": json.dumps({"equity": round(equity, 2),
                                  "n_positions": len(open_trades),
                                  "regime": regime, "sha": "f" * 40,
                                  "config_dirty": False, "behind": 0}),
            "ts": day.replace(hour=19, minute=50).isoformat(),
        })

        # A mark every Friday plus one on the final day. The old fixture wrote
        # exactly ONE mark, at the end of a window that ended weeks in the
        # past, so latest_position_mark() was always >24h old and the FRESH
        # branch of _mark_age_note (dashboard.py:876) could never render.
        if open_trades and (day.weekday() == 4 or d == days):
            records.append(_mark_event(open_trades,
                                       day.replace(hour=20, minute=30)))
    return records


def _mark_event(open_trades: list[dict], when: datetime) -> dict:
    """A broker valuation of the open book. Display only — never an input to a
    trading decision (invariant #4)."""
    return {
        "type": "event", "event": "positions_mark",
        "detail": json.dumps({
            t["symbol"]: {"qty": t["qty"], "avg_entry": t["entry_price"],
                          "market_value": round((t["entry_price"] or 0)
                                                * (t["qty"] or 0) * 1.02, 2),
                          "unrealized_pl": round((t["entry_price"] or 0)
                                                 * (t["qty"] or 0) * .02, 2)}
            for t in open_trades}),
        "ts": when.isoformat(),
    }


# --------------------------------------------------------------------------
# The stores the DASHBOARD reads. Until now qa_fixture touched these four
# files EMPTY, so every fixture-rendered dashboard had a blank lesson book, a
# blank judge-calibration line, no journal and no blog — the regions most
# likely to be wrong were the regions no fixture could reach.
#
# These are written as raw JSONL with explicit timestamps rather than through
# LessonStore/JudgmentStore/journal.add_entry, because all three stamp
# datetime.now() on append (lessons.py:35, judgments.py:28, journal.py:93) and
# that destroys byte-identical reruns. The honesty cost of hand-writing the
# shapes is paid back by tests/test_qa_fixture_shapes.py, which replays every
# file through the REAL production readers and fails if a shape drifts.
# --------------------------------------------------------------------------

def _decisions(records: list[dict]) -> list[dict]:
    return [r for r in records if r["type"] == "decision"]


def build_judgments(rng: random.Random, records: list[dict]) -> list[dict]:
    """One judgment per reviewed signal, one resolution per observable outcome.

    kind="llm" and kind="rails" are bucketed separately by calibration_metrics
    — mixing them would corrupt veto precision — so the rails rejections get
    their own kind here, exactly as src/main.py logs them.
    """
    out: list[dict] = []
    by_trade: dict[str, str] = {}
    n = 0
    for r in _decisions(records):
        review = r.get("llm_review")
        if not review:
            continue
        n += 1
        jid = f"jg-{n:08x}"
        by_trade[r["trade_id"]] = jid
        rails = str(r.get("detail") or "").startswith("risk rejection")
        out.append({
            "event": "judgment", "id": jid, "trade_id": r["trade_id"],
            "symbol": r["symbol"], "action": r["action"],
            "verdict": review["verdict"], "scale": review["scale"],
            "price_at_decision": r.get("entry_price") or 0.0,
            "regime": r["regime"], "strategy": r["strategy"],
            "kind": "rails" if rails else "llm",
            "executed": bool(r["executed"]), "reasoning": review["reasoning"],
            "stop_price": (r.get("order") or {}).get("stop_price"),
            "tp_price": None, "cited_lessons": [],
            "confidence": review.get("stated_confidence"),
            "ts": r["ts"],
        })

    # Resolutions. Executed trades resolve on their realized outcome; blocked
    # ones resolve as counterfactuals after the embargo horizon, which is the
    # only way veto precision ever becomes measurable.
    outcomes = {r["trade_id"]: r for r in records if r["type"] == "outcome"}
    for j in list(out):
        if j["event"] != "judgment":
            continue
        o = outcomes.get(j["trade_id"])
        if o:
            pnl_pct, reason, kind = o["pnl_pct"], o["exit_reason"], "realized"
        elif not j["executed"]:
            pnl_pct = round(rng.uniform(-11, 9), 3)
            reason, kind = "counterfactual_horizon", "counterfactual"
        else:
            continue                      # still open — genuinely unresolved
        out.append({
            "event": "resolution", "judgment_id": j["id"], "kind": kind,
            "pnl_pct": pnl_pct, "exit_reason": reason,
            "assessment": _assess(j["verdict"], j["executed"], pnl_pct),
            "ts": (o or j)["ts"],
        })
    return out


def _assess(verdict: str, executed: bool, pnl_pct: float) -> str:
    """Mirrors judgments.assess(). Duplicated deliberately: importing it would
    make the fixture agree with the code by construction, and the point of
    test_qa_fixture_shapes is to notice when they DISAGREE."""
    if executed:
        return "good_approve" if pnl_pct > 0 else "bad_approve"
    return "good_veto" if pnl_pct <= 0 else "bad_veto"


def build_lessons(rng: random.Random, records: list[dict],
                  n_lessons: int, hostile: bool) -> list[dict]:
    """The hypothesis book, covering every lifecycle state.

    >=16 live lessons in the `full` profile so _lessons_rows()'s live[:15]
    truncation is actually exercised — a cap that no fixture ever reaches is a
    cap no test can see.
    """
    out: list[dict] = []
    closed = [r for r in records if r["type"] == "outcome"]
    if not n_lessons:
        return out
    # 2/3 live (active+candidate), 1/3 terminal — enough of each that
    # render_markdown emits all three sections non-empty.
    plan = ([("active", i) for i in range(int(n_lessons * 0.62))]
            + [("candidate", i) for i in range(int(n_lessons * 0.2))]
            + [("refuted", i) for i in range(max(1, int(n_lessons * 0.1)))]
            + [("retired", i) for i in range(max(1, int(n_lessons * 0.08)))])
    for n, (status, _) in enumerate(plan):
        lid = f"ls-{n:08x}"
        src = closed[n % len(closed)]["trade_id"] if closed else "seed"
        hyp = (rng.choice(HOSTILE_TEXT) if hostile else
               f"{rng.choice(STRATEGIES)} entries in "
               f"{rng.choice(REGIMES)} underperform when RSI2 > "
               f"{rng.randint(60, 90)}")
        ts = records[n % len(records)]["ts"] if records else "2026-01-01T00:00:00+00:00"
        out.append({"event": "lesson_created", "ts": ts,
                    "lesson": {"id": lid, "hypothesis": hyp,
                               "scope": {"strategy": rng.choice(STRATEGIES),
                                         "regime": rng.choice(REGIMES)},
                               "source": src}})
        for k in range(rng.randint(2, 6)):
            rel = "supports" if (status != "refuted" or k % 2 == 0) \
                else "contradicts"
            tid = closed[(n + k) % len(closed)]["trade_id"] if closed else "seed"
            out.append({"event": "evidence", "ts": ts, "lesson_id": lid,
                        "trade_id": tid, "relation": rel,
                        "reason": f"{rel} on {tid}"})
        if status != "candidate":
            out.append({"event": "status_change", "ts": ts, "lesson_id": lid,
                        "from": "candidate", "to": status,
                        "reason": f"lifecycle -> {status}"})
    for r in closed:
        out.append({"event": "trade_evaluated", "ts": r["ts"],
                    "trade_id": r["trade_id"], "n_lessons": len(plan)})
    return out


def build_journal(rng: random.Random, records: list[dict],
                  hostile: bool) -> list[dict]:
    """One write-up per executed entry and per close — the live rate is ~1.75
    entries/day, which this reproduces."""
    out: list[dict] = []
    entries = [(r, "buy") for r in _decisions(records) if r["executed"]]
    entries += [(r, "close") for r in records if r["type"] == "outcome"]
    entries.sort(key=lambda p: p[0]["ts"])
    by_trade = {r["trade_id"]: r for r in _decisions(records)}
    for r, kind in entries:
        sym = r.get("symbol") or by_trade.get(r["trade_id"], {}).get("symbol", "")
        strat = r.get("strategy") or by_trade.get(r["trade_id"],
                                                  {}).get("strategy", "tsmom")
        text = (rng.choice(HOSTILE_TEXT) if hostile else
                f"{sym}: the {strat} signal fired against a "
                f"{by_trade.get(r['trade_id'], {}).get('regime', 'up/low')} "
                f"regime. The judge weighed continuation against extension and "
                f"sized accordingly; the stop sits below the entry structure "
                f"and the plan is mechanical from here. "
                + "Position reasoning continues. " * rng.randint(2, 6))
        out.append({
            "trade_id": r["trade_id"], "ts": r["ts"], "symbol": sym,
            "kind": kind,
            "title": f"{kind.upper()} {sym} — {strat}",
            "text": text,
        })
    return out


def build_posts(rng: random.Random, records: list[dict],
                hostile: bool) -> list[dict]:
    """The blog archive. Posts are archived BEFORE delivery is attempted, so
    every status the poster can produce appears here — src/blog.py:9-23 records
    the 11-day outage caused by filtering these on status == 'posted'."""
    out: list[dict] = []
    days: dict[str, list] = {}
    for r in records:
        days.setdefault(r["ts"][:10], []).append(r)
    for i, (day, recs) in enumerate(sorted(days.items())):
        for slot, hour in enumerate((13, 16, 20)):
            status = ["posted", "x_disabled", "failed"][(i + slot) % 3]
            link = None
            if slot == 0:
                # Link the first record of the day that HAS a trade id — the
                # day's first record is the market_context event, which has none.
                tid = next((r["trade_id"] for r in recs if r.get("trade_id")),
                           None)
                link = (HOSTILE_LINK if hostile else
                        f"https://qa.{DOMAIN}/journal.html#{tid}"
                        if tid else None)
            text = (rng.choice(HOSTILE_TEXT) if hostile else
                    f"{['Plan', 'Midday', 'Review'][slot]} for {day}: "
                    f"{len(recs)} records, regime held.")
            out.append({"ts": f"{day}T{hour:02d}:05:00+00:00", "text": text,
                        "link": link, "status": status})
    return out


def build_spy_bars(rng: random.Random, days: int,
                   start: datetime) -> list[dict]:
    """A benchmark series. Without it monthly_scorecard reports spy_ret_pct
    None for every month, so the ✅ beat / ❌ trailed column — the published
    measurement of the owner's stated goal — renders '—' forever."""
    bars, close = [], 480.0
    for d in range(days + 1):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        close = round(close * (1 + rng.uniform(-0.018, 0.020)), 2)
        bars.append({"ts": day.replace(hour=20, minute=0).isoformat(),
                     "open": round(close * 0.998, 2),
                     "high": round(close * 1.006, 2),
                     "low": round(close * 0.993, 2),
                     "close": close,
                     "volume": rng.randint(40_000_000, 120_000_000)})
    return bars


def build_subscribers(db_path: str, rng: random.Random) -> dict:
    """Every role the app can serve, so no tier is tested by extrapolation."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from publisher.subscribers import SubscriberDB

    store = SubscriberDB(db_path)
    roles = {
        "free": [f"free{i}@{DOMAIN}" for i in range(1, 4)],
        "paid": [f"paid{i}@{DOMAIN}" for i in range(1, 4)],
        "unsubscribed": [f"gone1@{DOMAIN}"],
        "expired": [f"expired1@{DOMAIN}"],
    }
    for email in sum(roles.values(), []):
        store.upsert_subscriber(email)
    for email in roles["paid"]:
        store.grant(email, "paid", source="qa-fixture",
                    expires=datetime.now(timezone.utc) + timedelta(days=30))
    for email in roles["expired"]:
        store.grant(email, "paid", source="qa-fixture",
                    expires=datetime.now(timezone.utc) - timedelta(days=1))
    for email in roles["unsubscribed"]:
        store.unsubscribe(email)
    return roles


def _write_jsonl(path: str, rows: list[dict]):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True, help="fixture directory (NOT memory/)")
    p.add_argument("--profile", choices=sorted(PROFILES), default="full",
                   help="which corner of the state space to build")
    p.add_argument("--days", type=int, default=None,
                   help="override the profile's window length")
    p.add_argument("--anchor", default=None, metavar="YYYY-MM-DD",
                   help="last day of the window (default: today UTC). The "
                        "window ENDS here, so the newest mark is fresh; a "
                        "fixture that ends in the past pins every staleness "
                        "note in its stale branch.")
    p.add_argument("--seed", type=int, default=20260725)
    p.add_argument("--summary", action="store_true", help="describe, do not write")
    args = p.parse_args()

    _guard_not_live(args.out)
    knobs = PROFILES[args.profile]
    days = args.days if args.days is not None else knobs["days"]
    anchor = (datetime.fromisoformat(args.anchor).replace(tzinfo=timezone.utc)
              if args.anchor else
              datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                 microsecond=0))
    start = anchor - timedelta(days=days)

    rng = random.Random(args.seed)
    records = build_ledger(rng, days, start, knobs)

    n_dec = sum(1 for r in records if r["type"] == "decision")
    n_exec = sum(1 for r in records if r["type"] == "decision" and r["executed"])
    n_closed = sum(1 for r in records if r["type"] == "outcome")
    span = ((datetime.fromisoformat(records[-1]["ts"])
             - datetime.fromisoformat(records[0]["ts"])).days) if records else 0

    judgments = build_judgments(rng, records)
    lessons = build_lessons(rng, records, knobs["n_lessons"], knobs["hostile"])
    entries = build_journal(rng, records, knobs["hostile"])
    posts = build_posts(rng, records, knobs["hostile"])
    spy = build_spy_bars(rng, days, start)

    print(f"fixture[{args.profile}]: {len(records):,} records | {n_dec:,} "
          f"decisions | {n_exec:,} executed | {n_closed} closed | {span} days")
    print(f"  judgments {len(judgments):,} · lessons {len(lessons):,} · "
          f"journal {len(entries):,} · posts {len(posts):,} · "
          f"spy bars {len(spy):,}")
    print(f"  revenue-gate thresholds: 30 closed / 90 days -> "
          f"{'OPEN' if n_closed >= 30 and span >= 90 else 'CLOSED'}")
    if args.summary:
        return 0

    os.makedirs(args.out, exist_ok=True)
    led = os.path.join(args.out, "ledger.jsonl")
    _write_jsonl(led, records)
    _write_jsonl(os.path.join(args.out, "judgments.jsonl"), judgments)
    _write_jsonl(os.path.join(args.out, "lessons.jsonl"), lessons)
    # journal.jsonl matters beyond the dashboard: content.feed() reads it for
    # the PAID tier, so a fixture without one leaves the paid path reading the
    # operator's REAL memory/journal.jsonl. Read-only, so invariant #9 holds —
    # but real write-ups have no business flowing through QA.
    _write_jsonl(os.path.join(args.out, "journal.jsonl"), entries)
    _write_jsonl(os.path.join(args.out, "posts.jsonl"), posts)
    open(os.path.join(args.out, "postexit.jsonl"), "a").close()
    with open(os.path.join(args.out, "spy_bars.json"), "w") as f:
        json.dump(spy, f)

    # learnings.md is GENERATED from the lesson store by the real renderer —
    # never hand-written (CLAUDE.md invariant 6). Rendering it through
    # lessons.render_markdown() is what keeps that true for the fixture too.
    import lessons as lessons_mod
    states = lessons_mod.LessonStore(
        os.path.join(args.out, "lessons.jsonl")).replay()
    lessons_mod.render_markdown(states, os.path.join(args.out, "learnings.md"))

    pub_dir = os.path.join(args.out, "publisher_data")
    os.makedirs(pub_dir, exist_ok=True)
    # pub.db, NOT subscribers.db — publisher/app.py opens <data_dir>/pub.db.
    # SubscriberDB creates whatever path it is handed, so a fixture on a
    # different filename silently hands every tool an empty database.
    roles = build_subscribers(os.path.join(pub_dir, "pub.db"), rng)

    print(f"  wrote {args.out}/ (ledger, judgments, lessons, journal, posts, "
          f"learnings.md, spy_bars.json)")
    print("  subscribers: " + ", ".join(f"{k}={len(v)}" for k, v in roles.items()))
    print(f"  all emails @{DOMAIN} (RFC 2606 — undeliverable by construction)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
