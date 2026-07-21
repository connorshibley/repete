import pytest

import judgments as jm
from judgments import JudgmentStore


@pytest.fixture
def store(tmp_path):
    return JudgmentStore(str(tmp_path / "judgments.jsonl"))


def _resolved(store, verdict, executed, pnl_pct, kind="llm", scale=1.0):
    jid = store.log_judgment("t1", "SPY", "buy", verdict, scale, 100.0,
                             "up/low", kind, executed)
    store.log_resolution(jid, "counterfactual" if not executed else "realized",
                         pnl_pct, "stop_loss",
                         jm.assess(verdict, executed, pnl_pct))
    return jid


def test_replay_joins_resolution(store):
    jid = _resolved(store, "veto", False, -3.0)
    j = store.replay()[jid]
    assert j["resolution"]["assessment"] == "good_veto"
    assert store.unresolved() == []


def test_unresolved_listed(store):
    store.log_judgment("t2", "SPY", "buy", "veto", 1.0, 100.0, None, "llm", False)
    assert len(store.unresolved()) == 1


def test_assess_labels():
    assert jm.assess("veto", False, -2.0) == "good_veto"
    assert jm.assess("veto", False, 5.0) == "bad_veto"
    assert jm.assess("approve", True, 3.0) == "good_approve"
    assert jm.assess("downsize", True, -1.0) == "bad_approve"


def test_downsize_value():
    # downsized to half: avoided the other half of a $-100 loss -> +$100 saved
    j = {"scale": 0.5}
    assert jm.downsize_value_usd(j, -100.0) == pytest.approx(100.0)
    assert jm.downsize_value_usd(j, 100.0) == pytest.approx(-100.0)  # cost of caution
    assert jm.downsize_value_usd({"scale": 1.0}, -100.0) == 0.0


def test_metrics_and_min_n(store):
    for _ in range(5):
        _resolved(store, "veto", False, -1.0)   # 5 good vetoes
    _resolved(store, "approve", True, 2.0)      # 1 approve: below min_n
    m = jm.calibration_metrics(store.replay(), min_n=5)
    assert m["veto_precision"] == 1.0 and m["n_vetoes_resolved"] == 5
    assert m["approve_accuracy"] is None        # n=1 < min_n
    assert m["n_resolved"] == 6


def test_rails_bucketed_separately(store):
    for _ in range(5):
        _resolved(store, "veto", False, -1.0, kind="rails")
    m = jm.calibration_metrics(store.replay(), min_n=5)
    assert m["n_resolved"] == 0                 # llm bucket empty
    assert m["rails_block_precision"] == 1.0


def test_calibration_line_noise_framing(store):
    for _ in range(6):
        _resolved(store, "veto", False, -1.0)
    line = jm.calibration_line(jm.calibration_metrics(store.replay()))
    assert "veto precision 100% (n=6)" in line
    assert "treat as noise" in line             # < 30 resolved


def test_calibration_line_empty(store):
    line = jm.calibration_line(jm.calibration_metrics(store.replay()))
    assert "no resolved judgments yet" in line


# ---- stated-confidence calibration (2026-07-21) ----

def test_confidence_persisted_and_bucketed(tmp_path):
    from judgments import (JudgmentStore, confidence_calibration,
                           confidence_calibration_lines)
    js = JudgmentStore(str(tmp_path / "j.jsonl"))
    jid_win = js.log_judgment("t1", "SPY", "buy", "approve", 1.0, 100.0,
                              None, kind="llm", executed=True, confidence=0.65)
    jid_loss = js.log_judgment("t2", "QQQ", "buy", "approve", 1.0, 100.0,
                               None, kind="llm", executed=True, confidence=0.62)
    js.log_judgment("t3", "DIA", "buy", "approve", 1.0, 100.0,
                    None, kind="llm", executed=True)  # no confidence -> ignored
    js.log_resolution(jid_win, "realized", 5.0, "strategy_sell", "good_approve")
    js.log_resolution(jid_loss, "realized", -3.0, "stop_loss", "bad_approve")

    cal = confidence_calibration(js.replay())
    assert list(cal) == ["0.6-0.69"]
    b = cal["0.6-0.69"]
    assert b["n"] == 2 and b["won"] == 1 and abs(b["stated"] - 0.635) < 1e-9

    lines = confidence_calibration_lines(js.replay())
    assert any("too few to read" in ln for ln in lines)  # n=2 < min_n honesty


def test_confidence_calibration_empty(tmp_path):
    from judgments import JudgmentStore, confidence_calibration_lines
    js = JudgmentStore(str(tmp_path / "j.jsonl"))
    lines = confidence_calibration_lines(js.replay())
    assert len(lines) == 1 and "no resolved trades" in lines[0]
