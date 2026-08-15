"""Shared fixtures. All tests run offline: no Broker/anthropic/tweepy clients
are ever constructed, LLM review is disabled, and X posting stays dry_run.
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

# --------------------------------------------------------------------------
# The EDGE register.
#
# Shared because K has exactly ONE definition and two guards read it:
# `test_skills_are_current.py` pins the tally stated in `.claude/skills/`, and
# `test_doc_counts.py` pins the tally stated in README/GLOSSARY/docs. A second
# copy of this derivation would be the same drift both of them exist to catch,
# so it lives here next to `make_bars` rather than in either caller.
#
# Absolute paths, unlike the CWD-relative ones in test_skills_are_current.py:
# a helper two modules deep should not care where pytest was invoked from.
# --------------------------------------------------------------------------

_ROOT = os.path.join(os.path.dirname(__file__), "..")
REGISTRATIONS = os.path.join(_ROOT, "research", "registrations.jsonl")
VERDICTS = os.path.join(_ROOT, "research", "verdicts.jsonl")


def _register_rows(path: str) -> list[dict]:
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def edge_families() -> dict[int, list[str]]:
    """EDGE spec ids grouped by the K they were registered against.

    K is NOT the number of EDGE rows in the register, and asserting that it is
    would be wrong in two directions at once:

    * **Down**: a claim is registered once per *arm*. §43, §44, §50 and §72 are
      four period arms each, all sharing one `bonferroni_k`, because they are
      one hypothesis scored four ways — not four chances at a false positive.
      22 EDGE rows are 8 spends.
    * **Up**: `bonferroni_k` was already 8 on the first row in this file (s35,
      2026-07-28), whose own `prior` reads *"EDGE claims stand at 0 for 7
      entering this test"*. Seven spends predate the register.

    So K is `max(bonferroni_k)` — the operator that matches what the skills
    actually say about it, *"it only goes up"*. Neither a row count (22) nor a
    distinct-value count (8) would have caught the drift these guards exist for.
    """
    fams: dict[int, list[str]] = collections.defaultdict(list)
    for row in _register_rows(REGISTRATIONS):
        spec = row.get("spec", {})
        if spec.get("claim") == "EDGE":
            fams[int(spec["bonferroni_k"])].append(row["id"])
    return dict(fams)


def live_bonferroni_k() -> int:
    """The K every tally in the repo must agree with.

    Asserts rather than letting `max()` raise ValueError on an empty dict: a
    renamed key or a restructured row would otherwise turn every caller into a
    confusing crash instead of one sentence naming the real problem. A guard
    that fails by crashing tells you nothing about the thing it guards.
    """
    fams = edge_families()
    assert fams, (
        f"no rows in {REGISTRATIONS} parse as EDGE claims carrying a "
        f"`bonferroni_k` — every tally guard that calls this is now measuring "
        f"NOTHING and passing. Fix the parse; do not delete the guard.")
    return max(fams)


def edge_pass_ceiling() -> int:
    """The most EDGE claims that could honestly be called passes.

    An upper bound, never an equality, because whether a *family* passed is not
    derivable from the store. §44 (K=13) had three of four arms pass and is
    REJECTED — its pre-registered reading rule was a conjunction, "one clause
    failure in one period sinks the whole claim". §72 (K=16) also had three of
    four and is CONFIRMED, because §68 had frozen a 3-of-4 confirmation rule for
    it beforehand. Both rules bind because they were written before the runs;
    neither exists as a field a test could read.

    So: a family with no passing arm under any reading rule did not pass. The
    true count may be below this (it is — 2 against a ceiling of 3) and can
    never be above it. That is the overclaim direction, which is the one this
    project guards hardest.
    """
    latest = {v["id"]: v for v in _register_rows(VERDICTS)}
    return sum(any(latest.get(sid, {}).get("passed") for sid in ids)
               for ids in edge_families().values())


def edge_k_floor() -> int:
    """The smallest K the register has ever carried — 8.

    Used to tell a BUDGET from a PERIOD COUNT. "1 pass in 15" (claims) and
    "1 pass in 4" (periods, `backtest_candidates.md:3714`) are the same
    sentence shape, and no amount of regex separates them. K has never been
    below 8 and only goes up, so a denominator under the floor is not the
    budget.

    Known blind spot, stated rather than hidden: a doc claiming "1 pass in 7"
    would be wrong and would be ignored here. The presence test on the docs
    that OWN the tally covers that from the other side — they must state the
    real one, so an understatement cannot hide in them.
    """
    return min(edge_families())


# --------------------------------------------------------------------------
# How this repo writes a tally.
#
# Shared for the same reason the derivation above is: `test_skills_are_current`
# and `test_doc_counts` must not drift apart on what counts as *stating* the
# budget, or the weaker of the two silently becomes the standard.
# --------------------------------------------------------------------------

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                 "fifteen": 15, "sixteen": 16, "seventeen": 17,
                 "eighteen": 18, "nineteen": 19, "twenty": 20}

_WORD_RE = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)


def digitize(text: str) -> str:
    """Spelled-out numbers to digits, so ONE pattern set reads both forms.

    `README.md` stated the tally entirely in words ("One EDGE claim in fifteen
    has passed") and a digit-only guard saw nothing there at all.

    Safe only because every pattern below is anchored on the tally IDIOM
    rather than on a bare number: "fifteen minutes" becomes "15 minutes" and
    still matches nothing, while "Fifteen pre-registered EDGE attempts"
    becomes "15 pre-registered EDGE attempts" and matches.
    """
    return _WORD_RE.sub(lambda m: str(_NUMBER_WORDS[m.group(1).lower()]), text)


# Shapes that state BOTH halves — "2 passes in 16", "One EDGE claim in
# fifteen", "the tally is unchanged at 1-in-15". Denominator is floor-filtered.
_TALLY_RE = re.compile(
    r"\b(?P<m1>\d+) pass(?:es)? in (?P<n1>\d+)\b"
    r"|\b(?P<m2>\d+) EDGE claims? in (?P<n2>\d+)\b"
    r"|EDGE tally[^.]{0,60}?\b(?P<m3>\d+)-in-(?P<n3>\d+)\b")

# Shapes that state K alone.
#
# `K stays N` is house style (21 uses in knowledge/backtest_candidates.md) and
# was the ONLY phrasing `bot-research-recall` used — its absence from the first
# version of this pattern is why the one skill still asserting K=15 passed the
# guard green for as long as the guard existed.
#
# `pre-registered` is REQUIRED in the last alternation, and that is the whole
# point of it: "Fifteen EDGE claims were rejected" counts REJECTIONS (14 today,
# and 14 at K=15 too — it was already wrong when written), which is a different
# quantity from the budget. Dropping the qualifier silently conflates them.
_K_ONLY_RE = re.compile(
    r"\bK is (?:now )?(?P<a>\d+)\b"
    r"|\bK\s*=\s*(?P<b>\d+)\b"
    r"|\bK (?:stays|remains|stands at) (?P<c>\d+)\b"
    r"|\bin (?P<d>\d+) claims\b"
    r"|\b(?P<e>\d+) pre-registered EDGE (?:attempts|claims)\b")

# A tally stated as a UNIQUENESS claim, with no number in it to compare.
# Two skills carried this after §72 made it false, and one was the frontmatter
# `description:` of a file whose body had already been corrected.
#
# Must NOT match "the only three-of-three pass" (no `EDGE` before `pass`) or
# "the first of only 2 passes in 16 EDGE claims" (`only` is followed by a
# count, never by `EDGE pass`).
UNIQUE_PASS_RE = re.compile(
    r"(?:only|single|sole)\s+(?:\w+\s+){0,2}EDGE\s+pass", re.I)


def stated_tallies(text: str) -> tuple[set[int], set[int]]:
    """(K values stated, pass counts stated) — digitized, floor-filtered."""
    t = digitize(text)
    floor = edge_k_floor()
    ks, ms = set(), set()
    for match in _TALLY_RE.finditer(t):
        for mg, ng in (("m1", "n1"), ("m2", "n2"), ("m3", "n3")):
            if match.group(ng) is None:
                continue
            n = int(match.group(ng))
            if n >= floor:            # else it is periods, not claims
                ks.add(n)
                ms.add(int(match.group(mg)))
    for match in _K_ONLY_RE.finditer(t):
        for g in ("a", "b", "c", "d", "e"):
            if match.group(g) is not None:
                ks.add(int(match.group(g)))
    return ks, ms


@pytest.fixture(autouse=True)
def _fake_broker_env(monkeypatch):
    """Preflight requires broker keys in env; tests never construct a real
    Broker (it's always monkeypatched), so dummies keep the suite offline."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")


@pytest.fixture(autouse=True)
def _no_real_alerts(monkeypatch):
    """No test may deliver an alert to a human or to an external service.

    `alerting.send()` already refuses under `PYTEST_CURRENT_TEST`; this stubs the
    two DELIVERY primitives as well, so a path that bypasses `send()` — a direct
    `_macos_banner` call, a future channel, a module that grows its own poster —
    still cannot reach the screen or the network. Two independent layers, because
    on 2026-08-02 the suite delivered eight real notifications to the owner's
    phone and one layer is exactly what was missing.

    These raise rather than no-op: a test that tries to alert is a test with a
    bug in it, and silently swallowing the attempt would hide the next one.

    `tests/test_alerts_are_silent_in_tests.py` proves the guard;
    `tests/test_alerting.py` opts out of the ENV half so real channel selection
    stays covered — a suppression that quietly stops delivery being tested is
    how this comes back.
    """
    import alerting

    def _no_banner(title, message):
        raise AssertionError(
            f"a test tried to deliver a desktop notification: {title!r}. "
            f"Stub it — see conftest._no_real_alerts.")

    def _no_post(*a, **k):
        raise AssertionError(
            "a test tried to POST to an alert webhook. Stub it — see "
            "conftest._no_real_alerts.")

    monkeypatch.setattr(alerting, "_macos_banner", _no_banner)
    monkeypatch.setattr(alerting, "_post_json", _no_post)


@pytest.fixture(autouse=True)
def _no_real_offhost_backups(monkeypatch, tmp_path_factory):
    """No test may write into the owner's real iCloud Drive.

    `scripts/backup.sh` mirrors every archive off-host, resolving iCloud by
    default. `tests/test_backup_restore.py` runs that script for real against an
    AGENT_ROOT fixture, so without this the suite would deposit a fixture
    archive into `~/…/CloudDocs/trading-agent-backups` on every run and then
    prune the owner's genuine archives to the newest 30 alongside them.

    Same shape as `_no_real_alerts`: the destination is redirected for the whole
    suite, and a test that wants to exercise the mirror sets the variable
    itself. Pointing it at a tmp dir rather than "" keeps the mirror path
    EXERCISED — disabling it outright would leave the feature untested, which is
    how a backup control quietly stops being one.
    """
    monkeypatch.setenv("REPETE_OFFHOST_DIR",
                       str(tmp_path_factory.mktemp("offhost")))


@pytest.fixture
def cfg(tmp_path):
    """Minimal dict config mirroring config.yaml, network layers disabled.

    Every writable store defaults to a pytest tmp path so no test can ever
    touch the real memory/ files OR the real public artifacts, even without an
    explicit override.

    `publish.out_dir` is part of that promise and was missing until 2026-07-28.
    Until then the claim above was true of memory/ and false of the HTML:
    dashboard/blog/journal render defaulted to a CWD-relative constant, so
    tests/test_backfill_posts.py overwrote the repo-root blog.html and
    journal.html with fixtures whenever pytest ran from the repo root."""
    return {
        "publish": {"out_dir": str(tmp_path / "site")},
        "mode": "paper",
        "symbols": ["SPY"],
        "strategy": {
            "name": "ma_crossover",
            "fast_period": 3,
            "slow_period": 5,
            "timeframe": "1Day",
            "lookback_bars": 100,
        },
        "risk": {
            "risk_per_trade_pct": 1.0,
            "max_position_pct": 10.0,
            "max_order_value_usd": 2000,
            "max_trades_per_day": 2,
            "daily_loss_limit_pct": 3.0,
            "max_open_positions": 5,
            "max_bar_age_days": 0,  # fixture bars carry fixed Jan-2026 ts;
                                    # the freshness guard is tested explicitly
            "min_holding_days": 2,
            "brackets": {
                "enabled": True,
                "atr_period": 14,
                "stop_atr_mult": 2.0,
                "take_profit_atr_mult": 3.0,
                "time_in_force": "gtc",
            },
        },
        "llm": {"enabled": False, "model": "claude-sonnet-4-5", "max_tokens": 1000},
        "x_posting": {"enabled": True, "dry_run": True, "post_style": "recap",
                      "hashtags": "#papertrading #tradingbot", "disclose_paper": True,
                      "posts_log_path": str(tmp_path / "posts.jsonl"),
                      "journal_path": str(tmp_path / "journal.jsonl")},
        "memory": {
            "ledger_path": str(tmp_path / "ledger.jsonl"),
            "learnings_path": str(tmp_path / "learnings.md"),
            "negative_example_quota": 0.20,
            "review_lookback_trades": 25,
        },
        "learning": {
            "lessons_path": str(tmp_path / "lessons.jsonl"),
            "judgments_path": str(tmp_path / "judgments.jsonl"),
            "promotion_min_supports": 3,
            "promotion_support_ratio": 2.0,
            "refutation_min_evidence": 3,
            "staleness_days": 45,
            "counterfactual_extra_days": 5,
            "counterfactual_max_per_run": 10,
            "evaluator_batch_size": 5,
            "max_llm_calls_per_cycle": 2,
            "top_k_lessons": 8,
            "max_context_chars": 4000,
            "regime": {"sma_period": 50, "vol_period": 20,
                       "vol_low": 0.15, "vol_high": 0.25},
        },
        "backtest": {
            "slippage_bps": 5,
            "fee_per_trade_usd": 0.0,
            "walk_forward_split": 0.7,
            "trials_path": str(tmp_path / "backtest_trials.jsonl"),
        },
    }


@pytest.fixture
def account():
    return {"equity": 100_000.0, "cash": 100_000.0,
            "last_equity": 100_000.0, "buying_power": 100_000.0}


def make_bars(closes, start_day=1):
    """Build daily bar dicts from a list of closes (high=low=close unless spread)."""
    bars = []
    for i, c in enumerate(closes):
        day = start_day + i
        bars.append({
            "ts": f"2026-01-{day:02d}T21:00:00+00:00",
            "open": c, "high": c, "low": c, "close": c, "volume": 1000,
        })
    return bars


@pytest.fixture
def bars_up():
    """CLAUDE.md crossover fixture: SMA3 crosses above SMA5 on the last bar."""
    return make_bars([10] * 6 + [9, 9, 9, 20])
