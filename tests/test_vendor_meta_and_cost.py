"""Which model actually served the judge, and what it cost.

The audit (2026-08-21) found zero inference-cost tracking and no record of
which model judged which trade. llm_client.complete() threw the vendor's
Message away after extracting text — and it has a silent fallback retry, so
the moment `llm.fallback_model` is set, every calibration silently mixes two
models with no way to tell.

Two rules pinned here: the SERVED id is recorded (not the alias requested),
and an unpriced model costs None, never 0.0.
"""
import sys
import types

import pytest

import llm
import llm_client
from ledger import Ledger


class _Sig:
    action, symbol, reason, indicators = "buy", "SPY", "momentum", {}


def _cfg(**over):
    base = {"enabled": True, "model": "claude-sonnet-5", "max_tokens": 500,
            "pricing": {"claude-sonnet-5-20260601": {"input_per_mtok": 2.0,
                                                     "output_per_mtok": 10.0}}}
    base.update(over)
    return {"llm": base}


def _stub(monkeypatch, served="claude-sonnet-5-20260601", fail_first=False,
          text='{"verdict":"downsize","scale":0.5,"confidence":0.6,"reasoning":"r"}',
          usage=True):
    """A vendor whose Message carries a served model id and usage."""
    calls = []

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw["model"])
            if fail_first and len(calls) == 1:
                raise RuntimeError("overloaded")
            msg = types.SimpleNamespace(
                id="msg_01", model=served, stop_reason="end_turn",
                content=[types.SimpleNamespace(type="text", text=text)])
            msg.usage = (types.SimpleNamespace(input_tokens=1200, output_tokens=80,
                                               cache_read_input_tokens=None,
                                               cache_creation_input_tokens=None)
                         if usage else None)
            return msg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-" + "x" * 80)
    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=lambda **_: types.SimpleNamespace(
                            messages=FakeMessages())))
    return calls


def test_the_served_model_is_recorded_not_the_alias(tmp_path, monkeypatch):
    """Request "claude-sonnet-5"; the vendor serves a dated snapshot. The
    ledger must hold what was SERVED — that is the only id that pins a
    calibration to a model."""
    _stub(monkeypatch, served="claude-sonnet-5-20260601")
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, llm.review_signal(_Sig(), "c", _cfg()),
                     executed=True)
    row = led.all_records()[0]
    assert row["vendor_model"] == "claude-sonnet-5-20260601"
    assert row["vendor_fell_back"] is False
    assert row["input_tokens"] == 1200 and row["output_tokens"] == 80
    assert row["cost_usd"] == pytest.approx((1200 * 2.0 + 80 * 10.0) / 1e6)
    # vendor_model and model_version are DIFFERENT fields: one is the LLM the
    # vendor ran, the other is modelver.py's rulebook fingerprint. Both keys
    # present, and they never alias each other.
    assert "vendor_model" in row
    assert row["vendor_model"] != row.get("model_version")


def test_a_fallback_retry_is_visible_in_the_ledger(tmp_path, monkeypatch):
    """THE CASE THE FIELD EXISTS FOR. Primary fails, fallback serves. Before
    2026-08-22 this was a log.warning and nothing else."""
    calls = _stub(monkeypatch, served="claude-haiku-4-5-20251001", fail_first=True)
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.log_decision("SPY", "buy", "m", {},
                     llm.review_signal(_Sig(), "c", _cfg(fallback_model="claude-haiku-4-5")),
                     executed=True)
    row = led.all_records()[0]
    assert calls == ["claude-sonnet-5", "claude-haiku-4-5"]
    assert row["vendor_fell_back"] is True
    assert row["vendor_model"] == "claude-haiku-4-5-20251001"


def test_an_unpriced_model_costs_none_not_zero(tmp_path, monkeypatch):
    """Zero reads as "free". None reads as "we do not know". A model missing
    from llm.pricing is the second, and a cost report that summed it as 0
    would understate with a straight face."""
    _stub(monkeypatch, served="claude-mystery-9")
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, llm.review_signal(_Sig(), "c", _cfg()),
                     executed=True)
    row = led.all_records()[0]
    assert row["vendor_model"] == "claude-mystery-9"
    assert row["cost_usd"] is None
    assert "cost_usd" in row


def test_missing_usage_yields_none_not_zero(monkeypatch):
    _stub(monkeypatch, usage=False)
    text, meta = llm_client.complete_detailed(_cfg(), "s", "u", 10)
    assert meta["input_tokens"] is None and meta["output_tokens"] is None
    assert llm_client.estimate_cost_usd(_cfg(), meta) is None


def test_estimate_reads_the_price_table_not_code():
    meta = {"model": "claude-sonnet-5-20260601", "input_tokens": 1_000_000,
            "output_tokens": 0}
    assert llm_client.estimate_cost_usd(_cfg(), meta) == pytest.approx(2.0)
    assert llm_client.estimate_cost_usd({"llm": {}}, meta) is None


def test_the_shipped_price_table_is_dated_and_covers_the_shipped_models():
    """The table must carry a verified-on date and price every model
    config.yaml actually names, or the first live row is unpriced."""
    import os, re
    import yaml
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = yaml.safe_load(open(os.path.join(root, "config.yaml")))
    src = open(os.path.join(root, "config.yaml")).read()
    pricing = cfg["llm"]["pricing"]
    assert re.search(r"VERIFIED \d{4}-\d{2}-\d{2}", src), "price table needs a verified-on date"
    # The requirement is that the judge's cost is KNOWN, not that it appears in
    # this table. Under a self-hosted provider it is known exactly and is not a
    # vendor line item — adding a $0 row here would both break the >0 invariant
    # below and dress a local model up as a priced vendor one. Asserted through
    # estimate_cost_usd so "unpriced" cannot hide behind a provider switch.
    if llm_client.needs_key(cfg):
        assert cfg["llm"]["model"] in pricing, "the judge's own model must be priced"
    else:
        priced = llm_client.estimate_cost_usd(
            cfg, {"model": cfg["llm"]["model"], "input_tokens": 1000,
                  "output_tokens": 1000})
        assert priced == 0.0, (
            f"the shipped judge is self-hosted but its cost came back "
            f"{priced!r} — a live row would be unpriced, not free")
    for m, row in pricing.items():
        assert row["input_per_mtok"] > 0 and row["output_per_mtok"] > 0, m


def test_complete_stays_a_thin_wrapper(monkeypatch):
    """Five callers in llm.py use complete(); only review_signal moved. The
    wrapper must return exactly the text complete_detailed does."""
    _stub(monkeypatch, text="hello")
    assert llm_client.complete(_cfg(), "s", "u", 10) == "hello"
    t, m = llm_client.complete_detailed(_cfg(), "s", "u", 10)
    assert t == "hello" and m["model"] == "claude-sonnet-5-20260601"


def test_unjudged_rows_carry_null_vendor_fields(tmp_path):
    led = Ledger(str(tmp_path / "l.jsonl"))
    led.log_decision("SPY", "buy", "m", {}, {"verdict": "approve", "scale": 1.0,
                                              "_prompt": None}, executed=True)
    row = led.all_records()[0]
    for k in ("vendor_model", "vendor_fell_back", "input_tokens", "output_tokens", "cost_usd"):
        assert k in row and row[k] is None


def test_the_cost_report_refuses_to_extrapolate(tmp_path):
    """On a ledger with no vendor meta it must print None, not $0.00, and
    must not invent an average for pre-schema rows."""
    import json, subprocess
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(json.dumps({"type": "decision", "ts": "2026-07-20T00:00:00+00:00",
                                  "llm_review": {"verdict": "approve"}}) + "\n")
    root = __import__("os").path.dirname(__import__("os").path.dirname(
        __import__("os").path.abspath(__file__)))
    out = subprocess.run([sys.executable, f"{root}/scripts/inference_cost_report.py",
                          "--ledger", str(ledger)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "Total: None" in out.stdout
    assert "$0.00" not in out.stdout
    assert "NOT estimated" in out.stdout
