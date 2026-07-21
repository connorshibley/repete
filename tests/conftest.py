"""Shared fixtures. All tests run offline: no Broker/anthropic/tweepy clients
are ever constructed, LLM review is disabled, and X posting stays dry_run.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest


@pytest.fixture(autouse=True)
def _fake_broker_env(monkeypatch):
    """Preflight requires broker keys in env; tests never construct a real
    Broker (it's always monkeypatched), so dummies keep the suite offline."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")


@pytest.fixture
def cfg(tmp_path):
    """Minimal dict config mirroring config.yaml, network layers disabled.

    Every writable store defaults to a pytest tmp path so no test can ever
    touch the real memory/ files, even without an explicit override."""
    return {
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
