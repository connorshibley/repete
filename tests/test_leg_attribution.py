"""Per-leg attribution is already in the record — verified, not built.

The 130/30 design asked for "attribution by leg, from the first fill", on the
grounds that "without this the short leg cannot be evaluated at all — it just
blends into the book". That was written before Phase 2-C-bis, which taught the
ledger the full buy/sell/short/cover vocabulary. So the question this file
answers is whether a NEW stored field is needed, and the answer is no:

  * FILLS carry it directly. `main.record_fill_quality` passes `rec["action"]`
    into `log_fill_quality`'s `side`, verbatim — all four values, not collapsed
    to the broker's BUY/SELL.
  * OUTCOMES carry it by JOIN. An outcome record holds no action, but
    `ledger.closed_trades()` merges every outcome with its entry decision, and
    that decision carries `action` and `strategy`.

Adding a stored `leg` would be a second source of truth for something the
record already holds losslessly, and the two would eventually disagree — this
repo has already paid for that once, in the hand-copied gate tally Phase 2 had
to consolidate.

What is NOT claimed here: that every CONSUMER splits the legs. Two do not, and
both are Phase 3's problem rather than this file's — see the design doc's
validation plan, amended with the specifics.
"""
import main
from ledger import Ledger


class _FillBroker:
    def __init__(self, fill):
        self._fill = fill

    def get_order(self, order_id):
        return {"id": order_id, "filled_avg_price": self._fill}


def _ledger(tmp_path):
    return Ledger(str(tmp_path / "ledger.jsonl"))


def _side_recorded(tmp_path, action):
    led = _ledger(tmp_path)
    led.log_decision("SPY", action, "scripted", {}, None, executed=True,
                     order={"id": "o-1"}, entry_price=100.0, qty=10)
    main.record_fill_quality(_FillBroker(101.0), led)
    return [r for r in led.all_records() if r["type"] == "fill_quality"][0]["side"]


# ---------------------------------------------------------------------------
# Fills — the action reaches the record verbatim, all four values.
# ---------------------------------------------------------------------------

def test_a_short_fill_records_short_not_sell(tmp_path):
    """The broker collapses this to OrderSide.SELL; the RECORD must not. If it
    did, a short entry and a long exit would be indistinguishable afterwards
    and the leg really would blend into the book."""
    assert _side_recorded(tmp_path, "short") == "short"


def test_a_cover_fill_records_cover_not_buy(tmp_path):
    assert _side_recorded(tmp_path, "cover") == "cover"


def test_a_buy_fill_still_records_buy(tmp_path):
    """The twins. Nothing shorts, so these two are what production writes."""
    assert _side_recorded(tmp_path, "buy") == "buy"


def test_a_sell_fill_still_records_sell(tmp_path):
    assert _side_recorded(tmp_path, "sell") == "sell"


# ---------------------------------------------------------------------------
# Outcomes — carried by the existing join, not by a new field.
# ---------------------------------------------------------------------------

def _closed(tmp_path, action, strategy):
    led = _ledger(tmp_path)
    tid = led.log_decision("SPY", action, "scripted", {}, None, executed=True,
                           entry_price=100.0, qty=10, strategy=strategy)
    led.close_trade(tid, exit_price=90.0, pnl=100.0, pnl_pct=10.0,
                    exit_reason="take_profit")
    return led.closed_trades()[0]


def test_a_closed_short_reports_its_leg_and_its_strategy(tmp_path):
    """Everything a per-leg breakdown needs, on the merged row: WHICH leg
    (action) and WHICH strategy produced it. The 130/30 design confines shorts
    to xsmom, so the pair also proves the confinement after the fact rather
    than assuming it."""
    row = _closed(tmp_path, "short", "xsmom")
    assert row["action"] == "short"
    assert row["strategy"] == "xsmom"
    assert row["pnl_pct"] == 10.0


def test_a_closed_long_reports_its_leg_the_same_way(tmp_path):
    row = _closed(tmp_path, "buy", "meanrev")
    assert row["action"] == "buy"
    assert row["strategy"] == "meanrev"


def test_the_outcome_record_itself_carries_no_action(tmp_path):
    """States the shape rather than implying the outcome row is self-contained.
    Any consumer reading raw outcome records — and several do — must join to
    the decision to know the leg. That is exactly the gap the design doc now
    names for the two consumers that do not."""
    led = _ledger(tmp_path)
    tid = led.log_decision("SPY", "short", "scripted", {}, None, executed=True,
                           entry_price=100.0, qty=10)
    led.close_trade(tid, exit_price=90.0, pnl=100.0, pnl_pct=10.0,
                    exit_reason="take_profit")
    outcome = [r for r in led.all_records() if r["type"] == "outcome"][0]
    assert "action" not in outcome
