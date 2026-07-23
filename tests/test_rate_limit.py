"""Phase D: publisher rate limiting — token buckets + the 429 middleware."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient

from ledger import Ledger
from publisher import config as pconfig
from publisher.app import app
from publisher.ratelimit import Limiter, TokenBucket
from publisher.readonly import ReadOnlyLedger
from publisher.subscribers import SubscriberDB


# ---- bucket unit tests (deterministic via explicit `now`) ----

def test_bucket_burst_then_deny():
    b = TokenBucket(per_minute=60, burst=3)
    t = 1000.0
    assert all(b.allow("ip", t) for _ in range(3))
    assert b.allow("ip", t) is False        # burst exhausted, no time passed


def test_bucket_refills_over_time():
    b = TokenBucket(per_minute=60, burst=2)   # 1 token/second
    t = 1000.0
    assert b.allow("ip", t) and b.allow("ip", t)
    assert b.allow("ip", t) is False
    assert b.allow("ip", t + 1.1) is True     # ~1 token refilled


def test_buckets_are_per_key():
    b = TokenBucket(per_minute=60, burst=1)
    t = 1000.0
    assert b.allow("1.1.1.1", t) is True
    assert b.allow("1.1.1.1", t) is False
    assert b.allow("2.2.2.2", t) is True      # independent bucket


def test_client_key_ignores_xff_without_trust():
    from publisher.ratelimit import client_key
    # XFF is attacker-controlled without a trusted proxy — never key on it.
    assert client_key("10.0.0.1", "1.2.3.4", trust_proxy=False) == "10.0.0.1"
    assert client_key(None, None, trust_proxy=False) == "unknown"


def test_client_key_uses_last_xff_hop_when_trusted():
    from publisher.ratelimit import client_key
    assert client_key("10.0.0.1", "1.2.3.4", trust_proxy=True) == "1.2.3.4"
    # multi-hop: the last entry is what the trusted proxy observed
    assert client_key("10.0.0.1", "9.9.9.9, 1.2.3.4", trust_proxy=True) \
        == "1.2.3.4"
    # trusted but header absent -> fall back to the direct peer
    assert client_key("10.0.0.1", None, trust_proxy=True) == "10.0.0.1"


def test_limiter_routes_and_disable():
    rl = {"enabled": True, "auth_per_minute": 1, "auth_burst": 1,
          "billing_per_minute": 60, "billing_burst": 2,
          "global_per_minute": 600, "global_burst": 100}
    lim = Limiter(rl)
    assert lim.check("/auth/request-link", "ip") is True
    assert lim.check("/auth/request-link", "ip") is False   # auth bucket dry
    assert lim.check("/feed", "ip") is True                 # global still fine
    off = Limiter({**rl, "enabled": False})
    assert all(off.check("/auth/request-link", "ip") for _ in range(50))


# ---- endpoint behavior through the middleware ----

@pytest.fixture()
def pub_client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLISHER_SESSION_SECRET", "test-secret-key")
    (tmp_path / "config.yaml").write_text(json.dumps({
        "mode": "paper",
        "memory": {"ledger_path": "memory/ledger.jsonl"},
        "publisher": {"rate_limit": {"auth_per_minute": 1, "auth_burst": 2}},
    }))
    Ledger(str(tmp_path / "memory" / "ledger.jsonl")).log_event(
        "cycle_complete", '{"equity": 100000.0}')
    app.state.cfg = pconfig.load(str(tmp_path))
    app.state.db = SubscriberDB(str(tmp_path / "publisher_data" / "pub.db"))
    app.state.ledger = ReadOnlyLedger("memory/ledger.jsonl")
    app.state.secret = b"test-secret-key"
    yield TestClient(app)
    for attr in ("cfg", "db", "ledger", "secret", "limiter"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_request_link_bursts_to_429(pub_client):
    codes = [pub_client.post("/auth/request-link",
                             json={"email": "a@b.co"}).status_code
             for _ in range(4)]
    assert codes[:2] == [200, 200]          # burst allowance
    assert 429 in codes[2:]                 # then limited
    r = pub_client.post("/auth/request-link", json={"email": "a@b.co"})
    assert r.status_code == 429
    assert "rate limited" in r.json()["detail"]


def test_other_routes_not_starved_by_auth_bucket(pub_client):
    for _ in range(5):
        pub_client.post("/auth/request-link", json={"email": "a@b.co"})
    assert pub_client.get("/").status_code == 200   # global bucket is generous
