"""Phase 4 (2026-08-06): the broker times out, retries reads, and never
retries a write.

WHAT THIS FILE IS DEFENDING, in one sentence: on 2026-08-05 a single cycle
spent 246 seconds — 52.5s, 89.7s and 103.7s — waiting on three dead sockets,
because `alpaca-py` sets no timeout anywhere and `requests` defaults to waiting
forever. All three ended in `RemoteDisconnected`, which is connection-level, so
the SDK's own 429/504 retry never looked at them.

The most important test here is `test_a_black_hole_server_does_not_hang_us`.
Everything else checks plumbing; that one measures the property.
"""
import ast
import inspect
import pathlib
import socket
import threading
import time

import pytest
import requests

import broker as broker_mod
from broker import (RetryBudget, install_timeout, retryable_read,
                    _backoff_sec)


# --------------------------------------------------------------------------
# The SDK-shape guard. These are the tests that turn "we patched a private
# attribute" from a hope into a check.
# --------------------------------------------------------------------------

def _clients():
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.trading.client import TradingClient
    return (TradingClient("k", "s", paper=True),
            StockHistoricalDataClient("k", "s"))


@pytest.mark.parametrize("index", [0, 1])
def test_both_alpaca_clients_still_hold_a_requests_session(index):
    """If alpaca-py ever renames or retypes `_session`, this goes red on the
    upgrade PR instead of every broker call silently losing its timeout."""
    client = _clients()[index]
    assert isinstance(getattr(client, "_session", None), requests.Session)


@pytest.mark.parametrize("index", [0, 1])
def test_install_timeout_refuses_a_client_it_does_not_recognise(index):
    client = _clients()[index]
    client._session = object()
    with pytest.raises(RuntimeError, match="no requests.Session"):
        install_timeout(client, 10.0)


def test_a_request_with_no_timeout_gets_ours():
    client = _clients()[0]
    seen = {}

    class _Recorder(requests.Session):
        def request(self, *args, **kwargs):   # noqa: D102
            seen.update(kwargs)
            return "sentinel"

    client._session = _Recorder()
    install_timeout(client, 10.0, connect_timeout=5.0)
    assert client._session.request("GET", "http://x") == "sentinel"
    assert seen["timeout"] == (5.0, 10.0)


def test_an_explicit_timeout_is_never_overridden():
    client = _clients()[0]
    seen = {}

    class _Recorder(requests.Session):
        def request(self, *args, **kwargs):   # noqa: D102
            seen.update(kwargs)

    client._session = _Recorder()
    install_timeout(client, 10.0)
    client._session.request("GET", "http://x", timeout=1.0)
    assert seen["timeout"] == 1.0


def test_installing_twice_does_not_stack_wrappers():
    client = _clients()[0]
    install_timeout(client, 10.0)
    first = client._session.request
    install_timeout(client, 10.0)
    assert client._session.request is first


def test_the_connect_timeout_never_exceeds_the_read_timeout():
    """A 1-second config must not leave a 5-second connect timeout behind it."""
    client = _clients()[0]
    install_timeout(client, 1.0, connect_timeout=5.0)
    assert client._session._repete_timeout == (1.0, 1.0)


# --------------------------------------------------------------------------
# The decisive one: measure the property against a real socket.
# --------------------------------------------------------------------------

@pytest.fixture
def black_hole():
    """A server that completes the TCP handshake and then says nothing ever.

    This is the 2026-08-05 shape reproduced locally: the connection is fine,
    the peer is gone. Without a read timeout the client waits until the OS
    gives up, which is where the 90 and 104 second gaps came from.
    """
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    held = []
    stop = threading.Event()

    def accept_and_ignore():
        sock.settimeout(0.25)
        while not stop.is_set():
            try:
                held.append(sock.accept()[0])
            except (OSError, socket.timeout):
                continue

    thread = threading.Thread(target=accept_and_ignore, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    stop.set()
    thread.join(timeout=2)
    for conn in held:
        conn.close()
    sock.close()


def _call_in_thread(client, deadline):
    """Run `get_account()` off-thread and report whether it ENDED in time.

    Off-thread on purpose. A straight call would HANG rather than fail when the
    timeout is missing — which is precisely the mutation this test exists to
    catch — and a hanging test in CI is worse than no test: it burns the job's
    15-minute budget and reports nothing. Measured: with the timeout injection
    removed, the direct-call version of this test ran past 40 minutes.

    The thread is a daemon and is deliberately not joined on failure. It is
    blocked on a socket nothing will ever write to, so there is nothing to wait
    for; pytest exits and the OS reaps it.
    """
    finished = threading.Event()

    def call():
        try:
            client.get_account()
        except Exception:       # noqa: BLE001 — only that it ENDED matters
            pass
        finished.set()

    threading.Thread(target=call, daemon=True).start()
    return finished.wait(timeout=deadline)


def test_a_black_hole_server_does_not_hang_us(black_hole):
    """A real alpaca-py client, pointed at a real socket that never answers,
    must give up in about the configured timeout rather than never.

    `url_override` is a PUBLIC parameter of TradingClient, so nothing about
    this test depends on the private attribute the patch itself uses.
    """
    from alpaca.trading.client import TradingClient
    client = TradingClient("k", "s", paper=True, url_override=black_hole)
    install_timeout(client, 1.0, connect_timeout=1.0)

    started = time.monotonic()
    # Generous deadline on purpose: the SDK retries its own 429/504 path three
    # times with a 3-second wait, and CI runners are noisy. What is being
    # measured is "bounded", not "1.0s" — unpatched, this does not return.
    ended = _call_in_thread(client, deadline=20)
    assert ended, "20s and still waiting — the timeout is not being applied"
    assert time.monotonic() - started < 20


def test_without_the_patch_the_same_call_does_not_return_quickly(black_hole):
    """The control for the test above. If an unpatched client ALSO gave up
    inside the deadline, the test above would be proving nothing about the
    patch and everything about the fixture."""
    from alpaca.trading.client import TradingClient
    client = TradingClient("k", "s", paper=True, url_override=black_hole)
    assert getattr(client._session, "_repete_timeout", None) is None

    assert not _call_in_thread(client, deadline=5), \
        "an unpatched client answered within 5s — this fixture no longer hangs"


# --------------------------------------------------------------------------
# Retry behaviour
# --------------------------------------------------------------------------

class _Flaky:
    """Minimal stand-in carrying the two attributes the decorator reads."""

    def __init__(self, failures, attempts=2, budget=60.0):
        self.remaining_failures = failures
        self.calls = 0
        self._retry_attempts = attempts
        self._retry_budget = RetryBudget(budget)

    @retryable_read
    def read(self):
        self.calls += 1
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise requests.exceptions.ConnectionError("boom")
        return "ok"


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    monkeypatch.setattr(broker_mod.time, "sleep", lambda _s: None)


def test_a_transient_failure_is_retried_and_succeeds():
    flaky = _Flaky(failures=1)
    assert flaky.read() == "ok"
    assert flaky.calls == 2


def test_it_gives_up_after_the_configured_attempts():
    flaky = _Flaky(failures=99, attempts=2)
    with pytest.raises(requests.exceptions.ConnectionError):
        flaky.read()
    assert flaky.calls == 3, "2 EXTRA attempts means 3 calls, not 2"


def test_zero_attempts_means_no_retry_at_all():
    flaky = _Flaky(failures=1, attempts=0)
    with pytest.raises(requests.exceptions.ConnectionError):
        flaky.read()
    assert flaky.calls == 1


def test_an_api_error_is_never_retried():
    """An APIError means the request ARRIVED and was refused. Retrying asks the
    same question and is told no again — and on an order path it is how a
    duplicate gets born."""
    from alpaca.common.exceptions import APIError

    class _Refused:
        _retry_attempts = 2
        _retry_budget = RetryBudget(60.0)

        def __init__(self):
            self.calls = 0

        @retryable_read
        def read(self):
            self.calls += 1
            raise APIError("refused")

    refused = _Refused()
    with pytest.raises(APIError):
        refused.read()
    assert refused.calls == 1


def test_the_budget_stops_retrying_once_it_is_spent():
    flaky = _Flaky(failures=99, attempts=2, budget=0.4)
    with pytest.raises(requests.exceptions.ConnectionError):
        flaky.read()
    # 1 real call + 1 retry (which charges 0.5s of backoff, spending the 0.4s
    # budget) and then no more.
    assert flaky.calls == 2


def test_the_budget_is_shared_across_calls_not_reset_per_call():
    """The whole reason the budget exists: ~40 symbols fetched one at a time
    must not each get a fresh allowance."""
    flaky = _Flaky(failures=99, attempts=2, budget=1.0)
    for _ in range(5):
        with pytest.raises(requests.exceptions.ConnectionError):
            flaky.read()
    assert flaky._retry_budget.remaining() == 0
    assert flaky.calls < 5 * 3, "each call got its own budget"


def test_a_zero_budget_disables_retrying():
    flaky = _Flaky(failures=99, attempts=2, budget=0)
    with pytest.raises(requests.exceptions.ConnectionError):
        flaky.read()
    assert flaky.calls == 1


def test_backoff_grows_and_is_jittered():
    first = [_backoff_sec(0) for _ in range(50)]
    second = [_backoff_sec(1) for _ in range(50)]
    assert min(first) >= 0.5 and max(first) <= 0.75
    assert min(second) >= 1.5 and max(second) <= 2.25
    assert len(set(first)) > 1, "no jitter — every retry fires in lockstep"


# --------------------------------------------------------------------------
# The write exclusion, enforced structurally.
# --------------------------------------------------------------------------

RETRYING_READS = {"account", "positions", "bars", "latest_price", "get_order",
                  "closed_orders", "open_stop_orders", "market_open"}

# `last_price` is a read but is deliberately UNdecorated: it delegates to
# `bars`, which retries, and decorating both would nest the loops (3 x 3 = 9)
# while each layer believed it allowed three.
PLAIN_READS = {"last_price"}

WRITES = {"market_order", "bracket_market_order", "replace_stop",
          "cancel_open_orders", "flatten_all"}


def _broker_methods():
    source = pathlib.Path(inspect.getfile(broker_mod)).read_text()
    tree = ast.parse(source)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Broker")
    return {n.name: n for n in cls.body
            if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}


def test_no_write_method_is_ever_retried():
    """Structural, not documentary. Alpaca rejects a duplicate
    client_order_id, and the caller would then have to tell 'already placed'
    from 'really failed' — a classifier whose bug doubles a position."""
    methods = _broker_methods()
    for name in sorted(WRITES):
        assert name in methods, f"{name} vanished from Broker"
        decorators = [d.id for d in methods[name].decorator_list
                      if isinstance(d, ast.Name)]
        assert "retryable_read" not in decorators, (
            f"{name} SUBMITS OR CANCELS ORDERS and is decorated for retry")


def test_every_read_that_should_retry_does():
    methods = _broker_methods()
    for name in sorted(RETRYING_READS):
        decorators = [d.id for d in methods[name].decorator_list
                      if isinstance(d, ast.Name)]
        assert "retryable_read" in decorators, f"{name} lost its retry"


def test_the_classification_names_every_public_broker_method():
    """The guard that makes the two tests above keep working. A method added
    later fails HERE until somebody decides whether it is safe to retry —
    rather than defaulting into whichever bucket silence implies."""
    classified = RETRYING_READS | PLAIN_READS | WRITES
    actual = set(_broker_methods())
    assert actual == classified, (
        f"unclassified Broker methods: {sorted(actual - classified)}; "
        f"stale entries: {sorted(classified - actual)}")
