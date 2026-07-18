"""2026-07-18 research-driven upgrades: judge scoreboard, tiered lesson
staleness, vol-targeted sizing, down-regime exposure cap, measured-slippage
calibration, equal-weight baseline, learning-model routing. All offline."""
from datetime import datetime, timezone

import pytest

import backtest
import lessons
import review
import risk
from judgments import JudgmentStore, recent_outcomes_block
from conftest import make_bars


# ---- judge scoreboard ----

def _resolved_store(tmp_path, n=3):
    st = JudgmentStore(str(tmp_path / "j.jsonl"))
    for i in range(n):
        jid = st.log_judgment(f"t{i}", "NVDA", "buy", "veto", 0.0, 100.0,
                              "down/low", kind="llm", executed=False,
                              strategy="meanrev")
        st.log_resolution(jid, "counterfactual", -2.5, "stop_before_tp",
                          "good_veto")
    # one unresolved — must not appear
    st.log_judgment("tx", "SPY", "buy", "approve", 1.0, 500.0, "up/low",
                    kind="llm", executed=True)
    return st


def test_scoreboard_lists_resolved_only(tmp_path):
    st = _resolved_store(tmp_path, n=3)
    block = recent_outcomes_block(st.replay(), n=20)
    assert block.count("GOOD_VETO") == 3
    assert "-2.5%" in block and "counterfactual" in block
    assert "SPY" not in block                    # unresolved excluded
    assert "what you ruled" in block


def test_scoreboard_empty_when_nothing_resolved(tmp_path):
    st = JudgmentStore(str(tmp_path / "j.jsonl"))
    st.log_judgment("t1", "SPY", "buy", "approve", 1.0, 500.0, None,
                    kind="llm", executed=True)
    assert recent_outcomes_block(st.replay()) == ""


def test_scoreboard_caps_at_n(tmp_path):
    st = _resolved_store(tmp_path, n=25)
    block = recent_outcomes_block(st.replay(), n=20)
    assert block.count("GOOD_VETO") == 20


# ---- tiered lesson staleness ----

TIERS = {"symbol": 21, "strategy": 90, "regime": 180}


def test_staleness_tiers_by_scope():
    lcfg = {"staleness_days": 45, "staleness_tiers": TIERS}
    assert lessons.staleness_days_for({"symbols": ["NVDA"]}, lcfg) == 21
    assert lessons.staleness_days_for({"direction": "buy"}, lcfg) == 90
    assert lessons.staleness_days_for({"strategy": "tsmom"}, lcfg) == 90
    assert lessons.staleness_days_for({"regime": "down/low"}, lcfg) == 180
    assert lessons.staleness_days_for({}, lcfg) == 45          # global scope
    # symbols beats regime when both present (most specific wins)
    assert lessons.staleness_days_for(
        {"symbols": ["SPY"], "regime": "up/low"}, lcfg) == 21


def test_staleness_flat_fallback_without_tiers():
    lcfg = {"staleness_days": 45}
    assert lessons.staleness_days_for({"symbols": ["NVDA"]}, lcfg) == 45


def test_lifecycle_uses_tiered_staleness(tmp_path):
    lcfg = {"staleness_days": 45, "staleness_tiers": TIERS,
            "promotion_min_supports": 3, "promotion_support_ratio": 2.0,
            "refutation_min_evidence": 3}
    store = lessons.LessonStore(str(tmp_path / "l.jsonl"))
    store.add_lesson("symbol-scoped goes stale fast", {"symbols": ["NVDA"]},
                     source="t1")
    states = store.replay()
    lid = next(iter(states))
    states[lid]["created_ts"] = "2026-06-01T00:00:00+00:00"   # 30 days ago
    states[lid]["last_evidence_ts"] = "2026-06-01T00:00:00+00:00"
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)
    moves = lessons.apply_transitions(states, now, lcfg)
    # 30d > symbol tier (21d) -> retired, even though < flat 45d
    assert any(m[2] == "retired" and "21d" in m[3] for m in moves)


# ---- vol-targeted sizing ----

def _vcfg(**kw):
    base = {"enabled": True, "annual_vol": 0.20, "period": 20,
            "min_scale": 0.5, "max_scale": 1.5}
    base.update(kw)
    return base


def test_vol_scale_disabled_or_thin_is_one():
    assert risk.vol_scale(make_bars([100] * 30), _vcfg(enabled=False)) == 1.0
    assert risk.vol_scale(None, _vcfg()) == 1.0
    assert risk.vol_scale(make_bars([100, 101]), _vcfg()) == 1.0  # too thin


def test_vol_scale_shrinks_in_high_vol_and_clamps():
    # wild alternating ±10% days -> realized vol >> 20% target -> min clamp
    closes = [100 * (1.1 if i % 2 else 0.9) ** 1 for i in range(1, 26)]
    wild = make_bars(closes)
    assert risk.vol_scale(wild, _vcfg()) == 0.5
    # nearly flat series -> tiny realized vol -> max clamp
    calm = make_bars([100 + 0.01 * i for i in range(26)])
    assert risk.vol_scale(calm, _vcfg()) == 1.5


def test_size_order_scales_with_vol_target(cfg, account):
    cfg["risk"]["vol_target"] = _vcfg()
    calm = make_bars([100 + 0.01 * i for i in range(26)])
    base = risk.size_order(account, 100.0, cfg)               # no bars -> 1.0
    scaled = risk.size_order(account, 100.0, cfg, bars=calm)  # 1.5x
    assert base == 10 and scaled == 15


def test_vol_scale_scoped_to_listed_strategies():
    calm = make_bars([100 + 0.01 * i for i in range(26)])
    vcfg = _vcfg(strategies=["meanrev"])
    assert risk.vol_scale(calm, vcfg, strategy="meanrev") == 1.5
    assert risk.vol_scale(calm, vcfg, strategy="tsmom") == 1.0   # not scoped
    assert risk.vol_scale(calm, vcfg, strategy=None) == 1.0
    assert risk.vol_scale(calm, _vcfg(), strategy="tsmom") == 1.5  # no list = all


def test_size_order_unchanged_when_candidate_disabled(cfg, account):
    calm = make_bars([100 + 0.01 * i for i in range(26)])
    assert (risk.size_order(account, 100.0, cfg, bars=calm)
            == risk.size_order(account, 100.0, cfg))


# ---- down-regime exposure cap ----

def test_exposure_cap_blocks_buy_in_down_regime(cfg, account):
    cfg["risk"]["regime_exposure"] = {"enabled": True,
                                      "down_max_gross_pct": 5}
    positions = {"QQQ": {"qty": 10, "market_value": 4_500.0}}
    with pytest.raises(risk.RiskRejection, match="down-regime exposure cap"):
        risk.pure_checks("buy", "SPY", 10, 100.0, account, positions, cfg,
                         regime_label="down/low")
    # same order fine in an up regime, and when the cap is disabled
    risk.pure_checks("buy", "SPY", 10, 100.0, account, positions, cfg,
                     regime_label="up/low")
    cfg["risk"]["regime_exposure"]["enabled"] = False
    risk.pure_checks("buy", "SPY", 10, 100.0, account, positions, cfg,
                     regime_label="down/low")


# ---- measured-slippage calibration ----

def _fq(bps):
    return {"type": "fill_quality", "slippage_bps": bps}


def test_measured_slippage_needs_min_clean_fills():
    assert backtest.measured_slippage_bps([_fq(5)] * 9) is None
    assert backtest.measured_slippage_bps([_fq(5)] * 10) == 5


def test_measured_slippage_excludes_artifacts_and_floors_at_zero():
    # stale-signal-price era artifacts (>|100| bps) must not poison the model
    rows = [_fq(1138.7)] * 6 + [_fq(4)] * 11
    assert backtest.measured_slippage_bps(rows) == 4
    # a negative median (fills better than signal) models as zero cost, not credit
    assert backtest.measured_slippage_bps([_fq(-3)] * 11) == 0.0


# ---- equal-weight universe baseline (pure part) ----

def test_equal_weight_pct():
    bars = {"A": make_bars([100, 110]),   # +10%
            "B": make_bars([100, 90]),    # -10%
            "C": make_bars([50])}         # too thin -> skipped
    assert review.equal_weight_pct(bars) == 0.0
    assert review.equal_weight_pct({}) is None


# ---- learning-model routing ----

def test_learning_calls_use_learning_model(cfg, monkeypatch):
    import llm
    cfg["llm"]["enabled"] = True
    cfg["learning"]["model"] = "claude-fable-5"
    seen = {}

    def fake_json_call(cfg_, max_tokens, system, user, model=None):
        seen["model"] = model
        return None
    monkeypatch.setattr(llm, "_json_call", fake_json_call)
    llm.generate_lesson_structured({"symbol": "SPY"}, "ctx", cfg)
    assert seen["model"] == "claude-fable-5"
    llm.propose_merge([], cfg)
    assert seen["model"] == "claude-fable-5"
