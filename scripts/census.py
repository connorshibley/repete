#!/usr/bin/env python3
"""Where does the capital go? — `python scripts/census.py`

Across four periods this bot sits ~91% in cash in three of them (§37, §38,
§39). 101 trades across 500 symbols over seven years is roughly fourteen a
year. At that exposure a genuine edge would be invisible, so **"no edge found"
and "no edge expressible" are currently indistinguishable** — and all ten EDGE
rejections inherit that ambiguity.

This answers the prior question: of every buy signal the strategies emitted,
how many became trades, and what stopped the rest.

It is a MEASUREMENT and produces no verdict. It cannot support or reject any
claim, and nothing here changes a config value or loosens a rail — if a rail
turns out to be strangling deployment, that fix is a separate pre-registered
decision. Loosening a risk rail because a diagnostic suggested it is exactly
the unfalsifiable move this whole programme exists to prevent.

Runs the SHIPPED config, unmodified. §35, §37 and §38 each applied a shared
sizing overlay (risk_per_trade_pct 2.0, max_position_pct 2.5) so their arms
would differ only by strategy; production ships 8.0 and 10.0. Only §39 measured
the real thing, so three of the four rows in the ALREADY-SEEN OBSERVATION
described a quarter-sized bot. This re-measures all four the same way.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backtest as bt                                       # noqa: E402

SNAPSHOTS = [
    ("2000-2006", "data/snapshots/bars_wide_2000-01-01_2006-12-31.json.gz"),
    ("2007-2013", "data/snapshots/bars_wide_2007-01-01_2013-12-31.json.gz"),
    ("2014-2019", "data/snapshots/bars_wide_2014-01-01_2019-12-31.json.gz"),
    ("2022-2026", "data/snapshots/bars_wide_2022-01-01_2026-07-24.json.gz"),
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--top", type=int, default=8,
                   help="block reasons to list per period")
    args = p.parse_args()

    cfg = bt.load_config()
    print("SHIPPED config, unmodified: "
          f"risk_per_trade_pct={cfg['risk'].get('risk_per_trade_pct')} "
          f"max_position_pct={cfg['risk'].get('max_position_pct')} "
          f"max_portfolio_heat_pct={cfg['risk'].get('max_portfolio_heat_pct')} "
          f"max_open_positions={cfg['risk'].get('max_open_positions')}\n")

    for label, path in SNAPSHOTS:
        if not os.path.exists(path):
            print(f"{label}: snapshot missing ({path}) — skipped\n")
            continue
        t0 = time.monotonic()
        bars = bt.load_bars_file(path)
        r = bt.simulate_ensemble(bars, cfg, args.cash)
        c = r.census
        secs = time.monotonic() - t0

        print(f"=== {label} — {len(bars)} symbols ({secs:.0f}s) ===")
        print(f"  return {r.total_return_pct:+.2f}%  "
              f"B&H {r.buy_hold_return_pct:+.2f}%  PF {r.profit_factor:.3f}")
        print(f"  deployment: mean {r.avg_deployment_pct:.2f}%  "
              f"median {r.deployment_median_pct:.2f}%  "
              f"max {r.deployment_max_pct:.2f}%  "
              f"FULLY IN CASH on {r.deployment_zero_pct:.1f}% of bars")

        sig, ex = c["signals"], c["executed"]
        rate = (100 * ex / sig) if sig else 0.0
        print(f"  signals {sig:,}  ->  executed {ex:,}  ({rate:.2f}%)")
        # Cross-check against the trade count the gates already recorded: if
        # these disagree the census is measuring something else.
        if ex != r.n_trades:
            print(f"  !! executed {ex} != n_trades {r.n_trades}",
                  file=sys.stderr)
        for reason, n in sorted(c["blocked"].items(), key=lambda kv: -kv[1])[:args.top]:
            print(f"      {reason:<22} {n:>8,}  ({100 * n / sig:5.2f}%)")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
