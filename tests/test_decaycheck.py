"""The decay monitor's wiring: alert-only, read-only, and honest exit codes."""
import json

import pytest

import decaycheck
import randombaseline as rb


def _decision(tid, symbol, ts, strategy="tsmom"):
    return {"type": "decision", "trade_id": tid, "symbol": symbol, "ts": ts,
            "action": "buy", "strategy": strategy, "executed": True}


def _outcome(tid, pnl_pct, ts):
    return {"type": "outcome", "trade_id": tid, "pnl_pct": pnl_pct, "ts": ts}


def _ledger(tmp_path, records):
    p = tmp_path / "ledger.jsonl"
    with open(p, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return str(p)


def _cfg(path):
    return {"memory": {"ledger_path": path},
            "backtest": {"slippage_bps": 5},
            "strategy": {"timeframe": "1Day"}}


# ------------------------------------------------- structural: alert-only

def test_monitor_cannot_halt_trading():
    """Invariant #2 held by construction, not by argument.

    The alert-only decision is only meaningful if the module has no path to the
    halt machinery. Asserted over the PARSED module, not its text: the docstring
    names `risk.engage_halt` precisely to say it is not called, and a substring
    search cannot tell an explanation from a call. Walking the AST can.
    """
    import ast
    tree = ast.parse(open(decaycheck.__file__).read())

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "risk" not in imported, "monitor must not import the risk rails"

    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            called.add(f.attr if isinstance(f, ast.Attribute)
                       else getattr(f, "id", ""))
    for forbidden in ("engage_halt", "check_halt"):
        assert forbidden not in called, f"monitor must never call {forbidden}"


def test_monitor_never_writes_to_the_ledger(tmp_path):
    """It reads through the zero-write reader; the file must be untouched."""
    path = _ledger(tmp_path, [_decision("a", "SPY", "2026-01-01T21:00:00+00:00"),
                              _outcome("a", 1.0, "2026-01-05T21:00:00+00:00")])
    before = open(path).read()
    decaycheck.run(_cfg(path), strategy=None, min_trades=20, samples=10,
                   lookback=50, offline_bars={})
    assert open(path).read() == before


# ------------------------------------------------------------ exit codes

def test_insufficient_data_exits_zero_and_does_not_alert(tmp_path, capsys):
    path = _ledger(tmp_path, [_decision("a", "SPY", "2026-01-01T21:00:00+00:00"),
                              _outcome("a", 1.0, "2026-01-05T21:00:00+00:00")])
    cfg_file = tmp_path / "config.yaml"
    import yaml
    cfg_file.write_text(yaml.safe_dump(_cfg(path)))
    rc = decaycheck.main(["--config", str(cfg_file), "--samples", "10"])
    out = capsys.readouterr().out
    assert rc == 0
    assert rb.INSUFFICIENT_DATA in out


def test_unreadable_config_exits_two_not_zero(tmp_path, capsys):
    """A monitor that failed to run must never be mistaken for a clean pass."""
    rc = decaycheck.main(["--config", str(tmp_path / "nope.yaml")])
    assert rc == 2
    assert "cannot read" in capsys.readouterr().err


def test_run_failure_exits_two(tmp_path, monkeypatch, capsys):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(_cfg(str(tmp_path / "ledger.jsonl"))))
    monkeypatch.setattr(decaycheck, "run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = decaycheck.main(["--config", str(cfg_file)])
    assert rc == 2
    assert "run failed" in capsys.readouterr().err


def test_alert_verdict_exits_one(tmp_path, monkeypatch, capsys):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(_cfg(str(tmp_path / "ledger.jsonl"))))
    bad = rb.Assessment(verdict=rb.WORSE_THAN_RANDOM, n_trades=30,
                        min_trades=20, actual_mean_pct=-2.0, null_mean_pct=1.0,
                        percentile=1.0, n_samples=100)
    monkeypatch.setattr(decaycheck, "run", lambda *a, **k: (bad, {"strategy": "all"}))
    rc = decaycheck.main(["--config", str(cfg_file)])
    assert rc == 1


def test_alert_is_only_sent_when_flag_is_passed(tmp_path, monkeypatch):
    """Default is print-only; --alert opts in to delivery."""
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(_cfg(str(tmp_path / "ledger.jsonl"))))
    bad = rb.Assessment(verdict=rb.WORSE_THAN_RANDOM, n_trades=30,
                        min_trades=20, actual_mean_pct=-2.0, null_mean_pct=1.0,
                        percentile=1.0, n_samples=100)
    monkeypatch.setattr(decaycheck, "run", lambda *a, **k: (bad, {"strategy": "all"}))

    sent = []
    import alerting
    monkeypatch.setattr(alerting, "send", lambda t, m: sent.append((t, m)))

    decaycheck.main(["--config", str(cfg_file)])
    assert sent == [], "must not alert without --alert"

    decaycheck.main(["--config", str(cfg_file), "--alert"])
    assert len(sent) == 1


def test_alert_delivery_failure_does_not_crash_the_monitor(tmp_path, monkeypatch,
                                                           capsys):
    import yaml
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(_cfg(str(tmp_path / "ledger.jsonl"))))
    bad = rb.Assessment(verdict=rb.WORSE_THAN_RANDOM, n_trades=30,
                        min_trades=20, actual_mean_pct=-2.0, null_mean_pct=1.0,
                        percentile=1.0, n_samples=100)
    monkeypatch.setattr(decaycheck, "run", lambda *a, **k: (bad, {"strategy": "all"}))
    import alerting
    monkeypatch.setattr(alerting, "send",
                        lambda t, m: (_ for _ in ()).throw(OSError("no webhook")))
    rc = decaycheck.main(["--config", str(cfg_file), "--alert"])
    assert rc == 1, "verdict still reported even when delivery fails"
    assert "alert delivery failed" in capsys.readouterr().err


# ------------------------------------------------------------- json output

def test_json_output_is_machine_readable(tmp_path, capsys):
    import yaml
    path = _ledger(tmp_path, [])
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.safe_dump(_cfg(path)))
    decaycheck.main(["--config", str(cfg_file), "--json", "--samples", "10"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == rb.INSUFFICIENT_DATA
    assert payload["alert"] is False
    assert payload["n_trades"] == 0


def test_no_bars_needed_when_there_are_no_trades(tmp_path):
    """Must not attempt a network fetch just to report INSUFFICIENT_DATA."""
    path = _ledger(tmp_path, [])
    a, meta = decaycheck.run(_cfg(path), strategy=None, min_trades=20,
                             samples=10, lookback=50)
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert meta["symbols"] == []


def test_thin_data_never_touches_the_network(tmp_path, monkeypatch):
    """Below min_trades the verdict is already known, so no bars are fetched.

    Without the short-circuit a network blip would surface as "could not run"
    (exit 2) when the honest, already-determined answer is INSUFFICIENT_DATA.
    """
    recs = []
    for i in range(5):                       # 5 trades, min_trades=20
        recs.append(_decision(f"t{i}", "SPY", "2026-01-01T21:00:00+00:00"))
        recs.append(_outcome(f"t{i}", 1.0, "2026-01-05T21:00:00+00:00"))
    path = _ledger(tmp_path, recs)

    def _boom(*a, **k):
        raise AssertionError("fetch_bars must not be called on thin data")

    monkeypatch.setattr(decaycheck, "fetch_bars", _boom)
    a, meta = decaycheck.run(_cfg(path), strategy=None, min_trades=20,
                             samples=10, lookback=50)
    assert a.verdict == rb.INSUFFICIENT_DATA
    assert a.n_trades == 5
