"""Someone is named, and something would actually reach them.

The audit's Phase 4 marked the kill switch PARTIAL for this. The mechanism is
real and has been drilled — there is a genuine halt-and-clear exercise in the
live ledger — but `docs/incident_response.md` had a severity table, a six-step
process, and timeline and postmortem templates with **no name, no contact and
no escalation target anywhere in it**. Fifteen runbooks say "alert" without
saying who receives one; one refers to "a second operator" who is nowhere
defined.

Separately, `alerting.channel()` — written specifically to answer "would an
alert reach anyone?" *before* an incident rather than during one — was called
from nowhere in `src/`, `scripts/` or `publisher/`. Correct, tested, unused.
So the only moment you could discover the channel reached nobody was mid
incident, via a notification you were not going to receive.
"""
import os

import pytest
import yaml

import alerting
import preflight

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config.yaml")
DOC = os.path.join(ROOT, "docs", "incident_response.md")


@pytest.fixture
def shipped():
    with open(CONFIG) as f:
        return yaml.safe_load(f)


def test_an_incident_has_a_named_owner(shipped):
    owner = str((shipped.get("ops") or {}).get("incident_owner") or "").strip()
    assert owner, (
        "ops.incident_owner is blank — every runbook says 'alert' and none "
        "says who receives one")


def test_the_owner_in_config_matches_the_runbook(shipped):
    """Two copies of a name is how they drift. This is the guard that makes
    the doc and the config one fact rather than two."""
    owner = str(shipped["ops"]["incident_owner"]).strip()
    with open(DOC) as f:
        doc = f.read()
    assert "## Owner" in doc, "incident_response.md must name an owner"
    assert owner in doc, (
        f"config names {owner!r} as incident owner but "
        f"docs/incident_response.md does not mention them")


def test_channel_reports_without_sending(monkeypatch):
    """It must never probe. A health check that fired a real alert to test
    alerting would page the operator every time anything asked."""
    sent = []
    monkeypatch.setattr(alerting, "_post_json",
                        lambda *a, **k: sent.append(1) or True)
    monkeypatch.setattr(alerting, "_macos_banner",
                        lambda *a, **k: sent.append(1) or True)
    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://example.invalid/hook")
    assert alerting.channel() == "webhook"
    assert not sent, "channel() must answer the question without asking it"


def test_channel_is_honest_about_a_headless_host(monkeypatch):
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    assert alerting.channel() == "log-only"


def test_preflight_refuses_when_nobody_is_named_and_nothing_delivers(
        cfg, monkeypatch):
    """The genuinely unsafe combination, and only that one.

    A named owner with no webhook is a laptop. An unnamed owner with a working
    webhook is a documentation gap. Both alone are survivable. Neither —
    nothing delivers AND nobody is named — is the state deploy/SECRETS.md
    warns about: the failure is written to a log file and nobody is told.
    """
    monkeypatch.delenv(alerting.WEBHOOK_ENV, raising=False)
    monkeypatch.setattr("sys.platform", "linux")

    cfg.setdefault("ops", {})["incident_owner"] = ""
    fails = preflight.run(cfg)
    assert any("nobody would be told" in f for f in fails), fails

    cfg["ops"]["incident_owner"] = "Connor Shibley"
    assert not any("nobody would be told" in f for f in preflight.run(cfg))


def test_a_working_channel_alone_is_enough(cfg, monkeypatch):
    """No name but a real webhook must not block a start — an alert reaches
    someone, which is the property that matters."""
    monkeypatch.setenv(alerting.WEBHOOK_ENV, "https://example.invalid/hook")
    cfg.setdefault("ops", {})["incident_owner"] = ""
    assert not any("nobody would be told" in f for f in preflight.run(cfg))
