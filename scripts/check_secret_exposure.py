#!/usr/bin/env python3
"""Are the live secrets sitting in a chat transcript?
`python scripts/check_secret_exposure.py`

Why this exists (2026-07-28)
----------------------------
The owner was asked to rotate the Alpaca key, and pushed back — reasonably —
with "why would I rotate it AGAIN". Nobody could answer, because nothing on
disk recorded whether a rotation had ever happened. docs/secrets_rotation.md
shipped a rotation log built for exactly this and it held one placeholder row.

So the question got re-derived from a half-remembered note every session, and
got it wrong in both directions: the Anthropic key was clean and was asked for
anyway, while the Alpaca pair really was exposed and had never been rotated.

This script answers the question mechanically instead. It reads `.env`, then
searches the Claude session transcripts for each live VALUE and reports how
many transcripts contain it. A hit means that exact secret is sitting in
plaintext in a file on this machine, which is precisely the condition rotation
is for.

It never prints a secret
------------------------
Names and counts only. `tests/test_secret_exposure_checker.py` asserts that no
value from `.env` appears anywhere in stdout — because a tool that finds leaked
secrets by printing them would be the leak. The same reason preflight reports
the *shape* of a malformed key rather than echoing it.

Read-only: opens files, writes nothing, changes nothing. Exit code is 0 when
nothing is exposed and 1 when something is, so it can run from CI or cron.

What a hit does NOT mean
------------------------
A transcript is a local file. A hit is not evidence the secret left the
machine — it is evidence that a local plaintext copy exists. Rotating makes
that copy worthless, which is the whole point. Scrubbing the transcript is
NOT the fix: it destroys an audit record and leaves the value in every backup.
"""
import glob
import os
import sys

TRANSCRIPTS = "~/.claude/projects/*/*.jsonl"

# Only things that are actually credentials. A URL like ALERT_WEBHOOK_URL is a
# credential too (anyone holding it can post to the topic), so it is included;
# LIVE_TRADING_CONFIRMED is a flag and is not.
WATCHED = (
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "ANTHROPIC_API_KEY",
    "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
    "ALERT_WEBHOOK_URL", "HEARTBEAT_PING_URL",
    "PUBLISHER_SESSION_SECRET", "RESEND_API_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET",
    "FMP_API_KEY",
)

# Below this length a "secret" is more likely a placeholder, and short strings
# collide with ordinary prose — a 6-character value would match half the corpus
# and report a scary number that means nothing.
MIN_LEN = 12


def load_env(path: str = ".env") -> dict:
    """name -> value for the watched credentials that actually have one.

    Empty values are dropped, not reported as clean: an empty variable cannot
    leak, and calling it "0 hits" would imply it had been checked. Absent and
    empty are the same fact here, and both mean 'nothing to look for'.
    """
    out = {}
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k in WATCHED and v:
            out[k] = v
    return out


def scan(values: dict, pattern: str = TRANSCRIPTS) -> dict:
    """name -> (transcripts containing it, total occurrences).

    Reads each transcript once and tests every value against it, rather than
    re-reading the corpus per key — these files reach tens of megabytes.
    """
    hits = {k: [0, 0] for k in values}
    targets = [(k, v) for k, v in values.items() if len(v) >= MIN_LEN]
    for path in glob.glob(os.path.expanduser(pattern)):
        try:
            text = open(path, errors="ignore").read()
        except OSError:
            continue
        for name, value in targets:
            n = text.count(value)
            if n:
                hits[name][0] += 1
                hits[name][1] += n
    return {k: tuple(v) for k, v in hits.items()}


def report(values: dict, hits: dict) -> int:
    exposed = {k: hits[k] for k in hits if hits[k][0]}
    print(f"{'CREDENTIAL':<26} {'LEN':>4}  {'FILES':>5} {'HITS':>5}  STATUS")
    for name in sorted(values):
        files, occ = hits.get(name, (0, 0))
        if len(values[name]) < MIN_LEN:
            status = "skipped — too short to search reliably"
        elif files:
            status = "EXPOSED — rotate this"
        else:
            status = "clean"
        print(f"{name:<26} {len(values[name]):>4}  {files:>5} {occ:>5}  {status}")

    print()
    if not exposed:
        print("No live secret appears in any session transcript.")
        return 0
    print(f"{len(exposed)} credential(s) present in plaintext on this machine.")
    print("Rotate them in the vendor console, update .env, and record the date")
    print("in docs/secrets_rotation.md. Do NOT edit the transcripts — rotation")
    print("makes the copy worthless; scrubbing destroys an audit record and")
    print("leaves the value in every backup.")
    return 1


def main() -> int:
    values = load_env()
    if not values:
        print("No credentials found in .env — nothing to check.")
        print("(Run this from the repo root, where .env lives.)")
        return 0
    return report(values, scan(values))


if __name__ == "__main__":
    sys.exit(main())
