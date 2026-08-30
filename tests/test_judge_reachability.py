"""A dead judge endpoint must be LOUD — and must never stop the cycle.

WHY THIS EXISTS. Between 2026-08-21 and -28 the judge was unreachable and
nothing said so: the heartbeat stayed fresh, cycles completed, health counted
the degradations only after each cycle had already refused its entries, and
the ledger accumulated 53 refusals before a human read the output. #152 moved
the judge onto the box, which swapped the unmonitored vendor for an
unmonitored container.

llm_client.probe() is the cheap pre-question ("is anything listening?").
Four consumers: host_warnings (the 09:35 ledger record), health.py (the
5-minute docker healthcheck), watchdog.check() (the thing that pages), and
main.run_cycle (the only one that fires BEFORE entries are refused).

THE CONSTRAINT WORTH MORE THAN THE FEATURE: a judge outage must never become
a preflight.run() failure or any other cycle-stopper. run() fails closed —
main.py treats any entry as fatal — and a stopped cycle abandons the open
book. The outage this monitors blocked entries; a monitor that blocks exits
too would be strictly worse than no monitor.
"""
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import llm_client  # noqa: E402
import preflight   # noqa: E402
import watchdog    # noqa: E402


def _local(**over):
    c = {"llm": {"enabled": True, "provider": "local",
                 "base_url": "http://127.0.0.1:9/v1",   # port 9: nothing listens
                 "model": "qwen3.8-27b", "timeout_seconds": 30,
                 "on_unavailable": "block"}}
    c["llm"].update(over)
    return c


def _keyed():
    return {"llm": {"enabled": True, "provider": "anthropic",
                    "model": "claude-opus-5"}}


# --- the probe itself -------------------------------------------------------

def test_dead_endpoint_returns_a_reason():
    reason = llm_client.probe(_local(), timeout=0.5)
    assert reason and "unreachable" in reason


def test_live_endpoint_returns_none():
    """A one-shot HTTP server standing in for vLLM's /models."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.recv(1024)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        assert llm_client.probe(
            _local(base_url=f"http://127.0.0.1:{port}/v1"), timeout=2.0) is None
    finally:
        srv.close()


def test_keyed_provider_is_not_probed():
    """None for a keyed provider means NOT APPLICABLE, not healthy. Probing
    Anthropic would cost tokens and its failures already degrade per call —
    but more importantly, no socket must be opened at all."""
    calls = []
    import urllib.request
    orig = urllib.request.urlopen

    def spy(*a, **k):
        calls.append(a)
        return orig(*a, **k)

    urllib.request.urlopen = spy
    try:
        assert llm_client.probe(_keyed()) is None
        assert calls == [], "probe opened a connection for a keyed provider"
    finally:
        urllib.request.urlopen = orig


def test_probe_never_raises_on_garbage_config():
    """Instrumentation that can take down what it monitors is the worse bug.
    A missing base_url raises inside base_url(); the probe must swallow it
    into a reason, not propagate."""
    for cfg in (_local(base_url=None), _local(base_url="not a url"),
                _local(base_url="http://"), {"llm": {"enabled": True,
                                                     "provider": "local"}}):
        reason = llm_client.probe(cfg, timeout=0.5)
        assert reason is None or isinstance(reason, str)
        if cfg["llm"].get("base_url") is None:
            assert reason, "no endpoint at all must not read as reachable"


# --- the constraint: never a cycle-stopper ---------------------------------

def test_a_dead_judge_is_not_a_preflight_failure(monkeypatch, tmp_path):
    """THE test this file exists for. preflight.run() fails closed; if the
    probe ever migrates into it, a dead vLLM stops the whole cycle including
    exits — abandoning the open book to fix a monitoring gap."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load(open(root / "config.yaml"))
    cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"   # dead
    cfg["memory"] = {**cfg.get("memory", {}),
                     "ledger_path": str(tmp_path / "ledger.jsonl")}
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    fails = preflight.run(cfg)
    assert not any("unreachable" in f or "probe" in f for f in fails), (
        f"a dead judge endpoint reached preflight.run(): {fails}")


def test_preflight_run_stays_pure_no_network(monkeypatch):
    """run() is pure by contract — no network — and the last time that was
    broken CI convicted it in one commit. Enforced here by making any socket
    open explode."""
    def boom(*a, **k):
        raise AssertionError("preflight.run() opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    import yaml
    root = Path(__file__).resolve().parents[1]
    cfg = yaml.safe_load(open(root / "config.yaml"))
    preflight.run(cfg)   # must complete without touching the network


# --- the consumers ----------------------------------------------------------

def test_host_warnings_surfaces_a_dead_judge(monkeypatch):
    monkeypatch.setattr(llm_client, "probe", lambda cfg, timeout=3.0:
                        "judge endpoint unreachable: test")
    warns = preflight.host_warnings(_local())
    assert any("unreachable" in w for w in warns)
    assert any("exits are unaffected" in w for w in warns), (
        "the warning must say what is and is not affected — an operator "
        "reading it at 09:35 decides whether to intervene based on that")


def test_host_warnings_quiet_when_judge_answers(monkeypatch):
    monkeypatch.setattr(llm_client, "probe", lambda cfg, timeout=3.0: None)
    assert not any("unreachable" in w
                   for w in preflight.host_warnings(_local()))


def test_host_warnings_quiet_when_llm_disabled(monkeypatch):
    """Trading on rules alone is a legitimate configured choice; warning about
    an endpoint the config does not claim to have would train operators to
    ignore the channel."""
    def explode(cfg, timeout=3.0):
        raise AssertionError("probed a disabled judge")
    monkeypatch.setattr(llm_client, "probe", explode)
    cfg = _local()
    cfg["llm"]["enabled"] = False
    preflight.host_warnings(cfg)   # must not probe at all


def test_health_reports_a_dead_judge(monkeypatch, tmp_path):
    import health
    monkeypatch.setattr(llm_client, "probe", lambda cfg, timeout=3.0:
                        "judge endpoint unreachable: test")
    cfg = _local()
    cfg["memory"] = {"ledger_path": str(tmp_path / "ledger.jsonl")}
    s = health.status(cfg=cfg)
    assert s["judge_unreachable"]
    assert any("unreachable" in p for p in s["problems"])
    assert s["healthy"] is False


def test_health_judge_field_is_none_when_reachable(monkeypatch, tmp_path):
    import health
    monkeypatch.setattr(llm_client, "probe", lambda cfg, timeout=3.0: None)
    cfg = _local()
    cfg["memory"] = {"ledger_path": str(tmp_path / "ledger.jsonl")}
    s = health.status(cfg=cfg)
    assert s["judge_unreachable"] is None
    assert not any("unreachable" in p for p in s["problems"])


def test_watchdog_pages_on_a_dead_judge(monkeypatch, tmp_path):
    """The watchdog is the consumer that actually calls alerting.send() —
    health flips a docker status and host_warnings writes a ledger row, but
    only this one reaches a human."""
    monkeypatch.setattr(llm_client, "probe", lambda cfg, timeout=3.0:
                        "judge endpoint unreachable: test")
    problems = watchdog.check(heartbeat_path=str(tmp_path / "none"),
                              halt_path=str(tmp_path / "none2"),
                              records=[], cfg=_local())
    assert any("unreachable" in p for p in problems)


def test_watchdog_without_cfg_does_not_probe(monkeypatch, tmp_path):
    """check() predates the cfg parameter and its existing callers/tests rely
    on it doing no network I/O when cfg is omitted."""
    def explode(cfg, timeout=3.0):
        raise AssertionError("watchdog probed with no cfg")
    monkeypatch.setattr(llm_client, "probe", explode)
    watchdog.check(heartbeat_path=str(tmp_path / "none"),
                   halt_path=str(tmp_path / "none2"), records=[])


# --- main.py alerts BEFORE judging ------------------------------------------

def test_run_cycle_alerts_before_any_signal_is_judged():
    """Order asserted structurally: in main.py source, the probe/alert block
    must precede the first review_signal call. A post-hoc alert is the
    watchdog's job; this one exists to fire first."""
    src = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text()
    probe_at = src.find("llm_client.probe(cfg)")
    judge_at = src.find("llm.review_signal(")
    assert probe_at != -1, "main.py no longer probes the judge"
    assert judge_at != -1
    assert probe_at < judge_at, (
        "the judge probe in main.py runs after signals are judged — it "
        "can then only confirm the refusals it was meant to pre-empt")
    block = src[probe_at:probe_at + 1200]
    assert "alerting" in block and "judge_unreachable" in block
