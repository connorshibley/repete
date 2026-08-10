"""Divergence #20 — the re-entry cooldown must be keyed the same way in live and
in the simulator. Closed 2026-08-10.

`risk.cooldown_days_for(cfg, name)` is PER-STRATEGY: `reentry_cooldown.strategies`
lists which strategies the rule applies to, and §9 adopted it for meanrev while
explicitly REJECTING it for tsmom. `backtest.simulate_ensemble` keyed its
`last_exit` map by `(strategy, symbol)` to match. `main.py` keyed it by SYMBOL
ALONE, and read it that way too.

So in live, meanrev's exit of AAPL suppressed a tsmom entry in AAPL — under a
rule §9 measured and rejected FOR tsmom — while the simulator scored tsmom as
free to enter. The two answered different questions and neither said so.

WHY IT WAS INVISIBLE. `strategies: [meanrev]` is the shipped scope, and with
exactly one scoped strategy the two keyings cannot disagree: the only lookups
that reach `cooldown_blocked` are meanrev's own. It was a bug waiting on a
config change, and the config change would have been made on the strength of a
gate run under the simulator's rule — evidence describing a bot that was not
the one running.

The register's rule is that "fixed in code" is not closed. This file is what
closes it.
"""
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")
import risk                                                   # noqa: E402


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _cfg(scoped=("meanrev",), days=5):
    return {"risk": {"reentry_cooldown": {"days": days,
                                          "strategies": list(scoped)}}}


def _live_last_exit(closed, cfg, default_owner="ma_crossover"):
    """The live builder from `main.py`, restated so the KEY SHAPE is testable
    without standing up a broker, a ledger and a full cycle.

    Restating it is the weakness of this test and it is named rather than
    hidden: `test_the_live_source_really_uses_this_key` below reads main.py and
    fails if the real code stops matching."""
    out = {}
    if (cfg["risk"].get("reentry_cooldown") or {}).get("days"):
        for t in closed:
            ets = t.get("exit_ts")
            key = (t.get("strategy") or default_owner, t["symbol"])
            if ets and ets > out.get(key, ""):
                out[key] = ets
    return out


def test_one_strategys_exit_does_not_block_ANOTHERS_entry():
    """The defect itself. meanrev exits AAPL today; tsmom must still be free to
    enter it, because §9 measured the cooldown for meanrev and REJECTED it for
    tsmom (maxDD worsened). Under the old symbol-only key this was blocked."""
    closed = [{"symbol": "AAPL", "strategy": "meanrev", "exit_ts": _iso(1)}]
    cfg = _cfg(scoped=("meanrev", "tsmom"))
    last_exit = _live_last_exit(closed, cfg)
    now = datetime.now(timezone.utc).isoformat()

    blocked = risk.cooldown_blocked(last_exit.get(("meanrev", "AAPL")), now,
                                    risk.cooldown_days_for(cfg, "meanrev"))
    free = risk.cooldown_blocked(last_exit.get(("tsmom", "AAPL")), now,
                                 risk.cooldown_days_for(cfg, "tsmom"))
    assert blocked is True
    assert free is False


def test_the_same_strategy_IS_still_blocked():
    """The paired half. A cooldown that never fires would pass the test above
    while removing the rail §9 adopted."""
    closed = [{"symbol": "AAPL", "strategy": "meanrev", "exit_ts": _iso(1)}]
    cfg = _cfg()
    last_exit = _live_last_exit(closed, cfg)
    assert risk.cooldown_blocked(
        last_exit.get(("meanrev", "AAPL")),
        datetime.now(timezone.utc).isoformat(),
        risk.cooldown_days_for(cfg, "meanrev")) is True


def test_an_untagged_legacy_row_lands_under_the_DEFAULT_OWNER():
    """Ledger rows written before the strategy tag existed carry no owner.
    Dropping them would silently shorten the cooldown on the oldest positions —
    the ones most likely to be re-entered."""
    closed = [{"symbol": "AAPL", "exit_ts": _iso(1)}]
    last_exit = _live_last_exit(closed, _cfg())
    assert ("ma_crossover", "AAPL") in last_exit
    assert "AAPL" not in last_exit          # never the bare symbol


def test_the_most_recent_exit_per_pair_wins():
    """The max is taken per (strategy, symbol), not per symbol. Two strategies
    exiting the same name at different times must each carry their own clock."""
    closed = [{"symbol": "AAPL", "strategy": "meanrev", "exit_ts": _iso(30)},
              {"symbol": "AAPL", "strategy": "meanrev", "exit_ts": _iso(1)},
              {"symbol": "AAPL", "strategy": "tsmom", "exit_ts": _iso(60)}]
    last_exit = _live_last_exit(closed, _cfg())
    assert last_exit[("meanrev", "AAPL")] == max(
        t["exit_ts"] for t in closed if t["strategy"] == "meanrev")
    assert last_exit[("tsmom", "AAPL")] < last_exit[("meanrev", "AAPL")]


# ------------------------------------------------- the two sources must agree

def test_the_live_source_really_uses_this_key():
    """Reading the source is crude, and it is the only thing that ties the
    restated builder above to the code that ships. Without it this file would
    keep passing after somebody reverted main.py."""
    with open("src/main.py") as f:
        src = f.read()
    assert "last_exit.get((name, symbol))" in src
    assert "last_exit.get(symbol)" not in src
    assert "last_exit[symbol]" not in src


def test_the_simulator_uses_the_SAME_key():
    """The other side of the divergence. If the simulator ever moves to a
    symbol-only key the gap reopens in the mirror direction, and the gates would
    then be the ones describing a bot that does not exist."""
    with open("src/backtest.py") as f:
        src = f.read()
    assert "last_exit.get((name, sym))" in src


def test_the_divergence_register_records_it_as_closed():
    """§17's rule, applied to itself: "fixed in code" is not closed. A fix with
    no register entry is a fix the next reader cannot find."""
    with open("docs/divergences.md") as f:
        doc = f.read()
    assert "## #20" in doc
    assert "test_cooldown_key_matches_sim.py" in doc
