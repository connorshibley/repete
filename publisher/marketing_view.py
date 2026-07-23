"""Marketing / landing page (Phase E, 2026-07-22).

Deliberately DESCRIPTIVE, not promotional. PRODUCT.md's publisher's-exemption
rules bind every word here:

  - impersonal — it never tells a reader what THEY should buy;
  - no guarantees, no projected returns, no testimonials;
  - any performance claim ships with sample size and methodology — so with the
    current track record this page makes NO performance claim at all. The only
    numbers shown are the gate counters (closed trades / days of history), and
    they are shown as reasons subscriptions are still CLOSED, not as results;
  - the paper-trading / not-advice disclaimer renders on the page.

Read-only against agent state (invariant #9): the counters come from the
ReadOnlyLedger through gates.revenue_gate.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import dashboard as dash  # noqa: E402 — CSS + escaping helpers only
from publisher.gates import DISCLAIMER  # noqa: E402

_esc = dash._esc

_HOW = [
    ("Rules decide, not vibes",
     "Entries and exits come from deterministic strategies (moving-average "
     "crossover, time-series and cross-sectional momentum, mean reversion). "
     "A strategy may only go live after passing a walk-forward backtest gate."),
    ("The model judges, it never trades",
     "A language model reviews each candidate trade and may approve, shrink, "
     "or veto it. It can never invent a trade, enlarge one, or place an "
     "order. Deterministic risk rails run after it, in code it cannot reach."),
    ("Hard limits that bind",
     "Position sizing, exposure caps, a per-day trade limit, a correlation "
     "cap, a total portfolio-risk cap, a stale-data guard, a second price "
     "vendor cross-check, and a daily-loss kill switch that flattens and "
     "halts."),
    ("Every decision is written down",
     "Each cycle appends to an append-only ledger — including the trades NOT "
     "taken and why. Outcomes are only recorded after a position closes, so "
     "nothing learns from a result it could not have seen at the time."),
]

_TIERS = [
    ("Free", ["The running track record and equity curve",
              "Decisions, delayed by one day",
              "The judge's reasoning withheld",
              "Monthly scorecard"]),
    ("Paid", ["Same-day decisions",
              "The judge's full reasoning, bull/bear debate, and confidence",
              "Per-trade journal write-ups",
              "Everything in Free"]),
]


def render(gate_open: bool, gate_reasons: list | None = None) -> str:
    how = "".join(
        f"<div class=card style='display:block;text-align:left;max-width:340px'>"
        f"<div class=v style='font-size:15px'>{_esc(t)}</div>"
        f"<p class=small>{_esc(b)}</p></div>" for t, b in _HOW)

    tiers = "".join(
        f"<div class=card style='display:block;text-align:left;max-width:340px'>"
        f"<div class=v style='font-size:15px'>{_esc(name)}</div>"
        + "<ul class=small>" + "".join(f"<li>{_esc(i)}</li>" for i in items)
        + "</ul></div>" for name, items in _TIERS)

    if gate_open:
        cta = ("<p><a class=x href='/dashboard'>Open your dashboard</a> · "
               "<a class=x href='/billing/checkout'>Subscribe</a></p>")
    else:
        reasons = "".join(f"<li>{_esc(r)}</li>" for r in (gate_reasons or []))
        cta = ("<h2>Subscriptions are not open yet</h2>"
               "<p class=small>We hold ourselves to gates before charging "
               "anyone. They are checked in code, and they are not met yet:</p>"
               f"<ul class=small>{reasons}</ul>"
               "<p class=small>Until they are, the feed is free and there is "
               "nothing to buy. <a class=x href='/feed'>Read the free feed</a>"
               " · <a class=x href='/status'>system status</a></p>")

    return (
        "<!doctype html><html><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Repete — an autonomous paper-trading robot, in public</title>"
        f"<style>{dash.CSS}</style></head><body><div class=wrap>"
        "<h1>Repete — an autonomous paper-trading robot, working in public</h1>"
        "<p class=small>Repete trades a simulated $100,000 account on a swing "
        "timeframe and publishes every decision it makes, including the ones "
        "it declines. No real money is traded.</p>"
        "<h2>How it works</h2>"
        f"<div class=cards>{how}</div>"
        "<h2>What a subscription would include</h2>"
        f"<div class=cards>{tiers}</div>"
        "<h2>What this is not</h2>"
        "<p class=small>This is a publication, not an advisory service and not "
        "a money manager. It never sees or touches your brokerage account. It "
        "is impersonal by design: it publishes what one robot did, on a "
        "schedule, to every reader alike — it will never tell you what to buy, "
        "and it cannot take your situation into account. There are no "
        "performance guarantees, no projected returns, and no testimonials "
        "here. Any figure published elsewhere on this site carries its sample "
        "size and how it was measured.</p>"
        f"{cta}"
        "<p class=small><a class=x href='/legal/tos'>terms</a> · "
        "<a class=x href='/legal/privacy'>privacy</a> · "
        "<a class=x href='/legal/risk'>risk disclosure</a> · "
        "<a class=x href='/status'>status</a></p>"
        f"<hr><p class=small>{_esc(DISCLAIMER)}</p>"
        "</div></body></html>")
