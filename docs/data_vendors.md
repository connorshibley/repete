# Survivorship-free price data — what it costs, and what it would buy

**Nothing here has been purchased. This is a priced decision for the owner**,
the same treatment §46 gave the FMP paid plan: establish what the money would
buy, establish how the claim would be checked, and stop.

## Why this document exists

Every EDGE claim in this repository was scored on survivor-selected data.
`scripts/build_snapshot.py` sources its universe from `index_constituents()`,
which reads **current** Wikipedia membership, so the companies that failed are
absent from all nine snapshots in `data/snapshots/` by construction.

§48 sized the inflation at **+130.06pp**. §51 showed it was large enough to
explain this project's only EDGE pass, at **+200.28pp**. §52 froze the EDGE
budget on that finding, and named its own exit: *"Lifts the freeze: a source
that passes `probe_delisted_coverage.py`."*

§56 found a free route that satisfies the letter of that requirement — a
universe of fifteen index and sector ETFs with nothing missing because nothing
ever left. It is honest and it is **not the live book.** A verdict there is a
verdict about sector rotation; the bot trades 38 individual large caps. Closing
that gap is what these vendors would sell.

## The candidates

| vendor | price | delisted history | point-in-time membership | notes |
|---|---|---|---|---|
| **[Norgate Data — Platinum](https://norgatedata.com/stockmarketpackages.php)** | **USD 630 / 12 mo**<br>USD 346.50 / 6 mo | yes, back to 1990 | yes | Subscription-only, purpose-built for this. The closest fit to the 38-name universe. |
| [Sharadar SEP](https://data.nasdaq.com/databases/SEP) (Nasdaq Data Link) | per licence — professional use must be bought through Nasdaq Data Link | yes | via the companion tickers table | Two products to buy and join, not one. |
| [AlgoSeek US Equities](https://www.quantconnect.com/data/algoseek-us-equities) via QuantConnect | bundled with the platform | yes — SIP CTA/UTP since 1998, ~27,500 securities | yes | Broadest coverage on the list, but arrives coupled to a platform this repo does not use. |
| [Polygon.io](https://apicostcalc.com/polygon.html) | USD 29–199 / mo | **thin — not recommended** | no | [QuantRocket's survivorship primer](https://www.quantrocket.com/blog/survivorship-bias/) is explicit that Polygon is the wrong tool for this. Cheapest and least suitable. |

## The acceptance test is already written

Do not buy on a marketing page. `scripts/probe_delisted_coverage.py` was built
to be pointed at a new fetcher — its own docstring says so:

> REUSABLE. This is the acceptance test for any candidate vendor — Norgate,
> Sharadar, Polygon, CRSP. Point `--source` at a new fetcher and it answers yes
> or no on the same two questions.

Both questions are fatal and the second is the one that matters:

1. **Coverage** — does the source return usable history for a delisted name at
   all? If not, the losers are simply missing, which *is* the bias.
2. **Ticker reuse** — for a delisted name that DOES return bars, do those bars
   cover the period **before** the delisting? SBNY is the worked example:
   yfinance returns 475 bars for a bank seized in March 2023, all of them
   starting August 2024, still labelled "Signature Bank". A builder that only
   asked "did we get bars?" would splice in a successor and produce a dataset
   that never observes the collapse while carrying the name of what collapsed.
   That is worse than the bias it claims to fix.

So the purchase sequence is: **trial or free tier first → run the probe →
commit its output → buy only on a PASS.** Any vendor that cannot be probed
before payment should be treated as failing.

## What a PASS would actually license

Less than it sounds. It would permit registering EDGE claims on a
survivorship-free version of the **live** universe — which is the one question
this project has never been able to ask. It would not:

- change any of the fifteen existing rejections. §41 set the precedent that a
  data or simulator finding does not retroactively re-score past sections;
- close divergences #13–#16, #18 or #19, none of which is about survivorship;
- move the live evidence, which is calendar-bound at ten closed trades and
  realized −$339.16.

And §57 is the reason to be sober about the upside. On a universe where
survivorship provably does not exist, the incumbent ensemble **failed to beat
SPY in four periods out of four**. Buying honest data for the 38-name book buys
a trustworthy answer. On the evidence so far, the answer it returns is unlikely
to be a flattering one — which is exactly why it is worth having, and exactly
why it should not be bought in the expectation of a different result.

## Recommendation

**Norgate Platinum at USD 630/year is the one to buy if any is bought**, on
coverage-to-price and on being a direct fit rather than a platform commitment.
Six months at USD 346.50 is enough to run the probe and one registered
replication, which is the whole question.

**But the honest sequencing is to spend nothing yet.** §56's free universe is
already certified and §57 has already used it to produce a decisive, clean
result. The case for paying arrives when there is a specific claim about the
38-name universe that is worth settling — and Phase 11's strategies do not exist
yet. **Buy the data when there is a claim waiting for it, not before.**
