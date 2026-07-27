"""Commands in the incident runbooks must actually work.

Why this file exists
--------------------
`docs/runbooks.md` told an operator to diagnose a kill-switch trip with

    grep '"event": "halt"' memory/ledger.jsonl

The code has never written an event called `halt`. It writes `kill_switch`
when the daily-loss limit fires and `halted_cycle_skipped` for each cycle
that then refuses to run. The documented command returns **nothing**, which
during an incident reads as "the kill switch never fired" — the most
dangerous possible wrong answer, given the runbook's whole purpose is
deciding whether it is safe to delete the HALT file and resume trading.

This is the same failure the SLO doc carried until 2026-07-26: a documented
check that was never a check. A runbook is a check on the operator's
understanding, and it can rot exactly like a comment.

The test greps the runbooks for ledger event names and asserts each one is
actually emitted somewhere in `src/`.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = [os.path.join(ROOT, "docs", "runbooks.md"),
        os.path.join(ROOT, "docs", "incident_response.md"),
        os.path.join(ROOT, "HEARTBEAT.md")]


def _emitted_events() -> set:
    """Every event name the source actually logs."""
    names = set()
    src = os.path.join(ROOT, "src")
    for f in os.listdir(src):
        if not f.endswith(".py"):
            continue
        body = open(os.path.join(src, f)).read()
        names |= set(re.findall(r'log_event\(\s*[\'"]([a-z_]+)[\'"]', body))
        names |= set(re.findall(r'log_event\(\s*f?[\'"]([a-z_]+)\{', body))
    return names


def _documented_events() -> dict:
    """Event names quoted in a `"event": "..."` shape inside the runbooks."""
    found = {}
    for path in DOCS:
        if not os.path.exists(path):
            continue
        body = open(path).read()
        for m in re.finditer(r'"event":\s*"([a-z_]+)"', body):
            found.setdefault(m.group(1), []).append(os.path.basename(path))
    return found


def test_every_event_named_in_a_runbook_is_really_emitted():
    """The assertion that would have caught the `halt` bug."""
    emitted = _emitted_events()
    documented = _documented_events()
    assert documented, "no event names found in the runbooks — check the regex"
    bogus = {n: where for n, where in documented.items() if n not in emitted}
    assert not bogus, (
        "runbooks name events the code never writes — an operator running "
        f"these greps gets zero results and draws the wrong conclusion: {bogus}")


def test_the_kill_switch_events_are_documented_by_their_real_names():
    """Named specifically, because this is the one where a wrong answer means
    resuming trading after a loss the operator never actually looked at."""
    documented = set(_documented_events())
    assert "kill_switch" in documented or any(
        d.startswith("kill_switch") for d in documented), (
        "the runbook must tell the operator to look for kill_switch")
    assert "halted_cycle_skipped" in documented, (
        "the runbook must also cover the skipped-cycle records")
    assert "halt" not in documented, (
        "'halt' is not an event this system emits — that grep returns nothing")


def test_heartbeat_doc_job_count_matches_the_shipped_plists():
    """HEARTBEAT.md told the operator to expect three jobs. There are eight.
    Someone checking a real alert would see five 'extra' jobs and have no way
    to know whether that was correct."""
    scripts = os.path.join(ROOT, "scripts")
    plists = [f for f in os.listdir(scripts)
              if f.startswith("com.trading-agent.") and f.endswith(".plist")]
    body = open(os.path.join(ROOT, "HEARTBEAT.md")).read()
    words = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
             8: "eight", 9: "nine", 10: "ten"}
    n = len(plists)
    assert (str(n) in body or words.get(n, "~") in body), (
        f"{n} launchd plists ship, but HEARTBEAT.md does not say so — "
        f"an operator cannot tell a missing job from an expected one")
