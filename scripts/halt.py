#!/usr/bin/env python3
"""The operator's stop button: block new entries, now.
`./scripts/halt.sh "why"`   engage    (also: --status, --clear)

Why this exists (2026-08-02)
----------------------------
The HALT file has always been the bot's own emergency brake — `risk.check_halt`
is consulted once per cycle in `_bootstrap_cycle` AND again per order inside
`pre_trade_checks`, so engaging it stops the very next order, not merely the
next cycle. But nothing let a HUMAN pull it. `docs/runbooks.md` documented HALT
only as something the bot does TO the operator, never something the operator
does to the bot, and the only written instruction anywhere was "delete this
file to re-enable" — the second half of a procedure whose first half did not
exist.

So the actual 3am answer to "stop it, now" was: remember the filename, remember
it is relative to the repo root, create it by hand, hope you are in the right
directory. That is not a kill switch; that is trivia.

WHAT THIS DOES AND DOES NOT DO
------------------------------
It BLOCKS NEW ENTRIES. It does NOT sell anything. Open positions keep running,
and their broker-side bracket legs keep protecting them independently of this
process.

BE PRECISE ABOUT WHAT STILL PROTECTS THEM (corrected 2026-08-02). This used to
say positions "keep running to their normal stops and exits", which overstates
it: while HALT is engaged the cycle returns at src/main.py:596 before a single
signal is evaluated, so the agent evaluates no exits at all. What protects an
open position is the bracket sitting AT THE BROKER — the stop and take-profit
legs submitted when the position was opened. They fill whether or not this
process ever runs again, which is the whole reason brackets are mandatory.

The practical consequence: HALT is safe to leave on over a weekend, and is NOT
a substitute for deciding what to do with an open book. Nothing will manage
those positions but the broker's own legs until you clear it.

That is the owner's choice (2026-08-02), and it is the conservative one: a
forced liquidation crystallises every open loss at whatever the screen says in
the worst minute of the day, which is exactly the minute someone reaches for a
kill switch. Blocking entries stops the bleeding from getting WIDER without
converting paper losses into realised ones. If flattening is ever wanted it
should be a separate, deliberately-named command — not a surprise inside this
one.

The distinction is stated in the alert and on stdout every time, because a
kill switch that is misunderstood is worse than none: an operator who believes
this flattened the book will not go looking for the exposure that is still
open.

Reuses `risk.engage_halt`. The file format, the path and the log line have
exactly one definition; a second copy here is how `max_order_value_usd: 0`
came to mean two different things in two files for a day (§29).
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

# HALT_FILE is a RELATIVE path, resolved against the process's cwd. Every
# caller of check_halt() runs from the repo root, so this script must too — a
# HALT engaged into some other directory is a stop button wired to nothing.
os.chdir(ROOT)

import risk                                                  # noqa: E402


def _cfg() -> dict:
    import yaml
    with open("config.yaml") as f:
        return yaml.safe_load(f) or {}


def _load_env() -> None:
    """launchd/cron give a bare environment; the alert webhook lives in .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:                                      # tests run without it
        pass


def status() -> int:
    if not risk.check_halt():
        print("HALT is NOT engaged — the bot may open new positions.")
        return 0
    print("HALT IS ENGAGED. No new entries will be opened.")
    print("Open positions are UNAFFECTED and still running.\n")
    try:
        with open(risk.HALT_FILE) as f:
            print(f.read().rstrip())
    except OSError as e:                                     # noqa: BLE001
        print(f"  (could not read {risk.HALT_FILE}: {e})")
    print("\nResume with: ./scripts/halt.sh --clear")
    return 0


def engage(reason: str) -> int:
    already = risk.check_halt()
    if already:
        print("HALT was ALREADY engaged — leaving the existing reason intact.\n")
        return status()

    risk.engage_halt(f"MANUAL — {reason}")

    # Ledger and alert are best-effort and deliberately AFTER the file write.
    # The stop must not depend on a disk write to memory/ or on a webhook being
    # reachable; a kill switch that fails because the alerting service is down
    # is a kill switch that fails exactly when things are already going wrong.
    try:
        from ledger import Ledger
        cfg = _cfg()
        Ledger(cfg["memory"]["ledger_path"]).log_event(
            "halt_engaged", f"manual halt by operator: {reason}")
    except Exception as e:                                   # noqa: BLE001
        print(f"  warn: could not ledger the halt ({e}) — the HALT itself is set",
              file=sys.stderr)

    _load_env()
    try:
        import alerting
        alerting.send(
            "Repete: MANUAL HALT engaged",
            f"New entries are blocked.\n"
            f"Open positions are NOT closed and are still running.\n\n"
            f"Reason: {reason}")
    except Exception as e:                                   # noqa: BLE001
        print(f"  warn: alert delivery failed ({e}) — the HALT itself is set",
              file=sys.stderr)

    print("HALT ENGAGED. No new entries will be opened.")
    print("Open positions are NOT closed. While HALT is on the cycle does not")
    print("run at all, so the agent evaluates NO exits — the broker-side")
    print("bracket legs are what protect open positions until you clear it.")
    print("\nResume with: ./scripts/halt.sh --clear")
    return 0


def clear() -> int:
    if not risk.check_halt():
        print("HALT is not engaged — nothing to clear.")
        return 0
    try:
        with open(risk.HALT_FILE) as f:
            was = f.read().rstrip()
    except OSError:                                          # noqa: BLE001
        was = "(unreadable)"
    os.remove(risk.HALT_FILE)

    try:
        from ledger import Ledger
        cfg = _cfg()
        Ledger(cfg["memory"]["ledger_path"]).log_event(
            "halt_cleared", "manual halt cleared by operator")
    except Exception as e:                                   # noqa: BLE001
        print(f"  warn: could not ledger the clear ({e})", file=sys.stderr)

    _load_env()
    try:
        import alerting
        alerting.send("Repete: HALT cleared",
                      "Trading re-enabled by operator. New entries may open "
                      "on the next cycle.")
    except Exception as e:                                   # noqa: BLE001
        print(f"  warn: alert delivery failed ({e})", file=sys.stderr)

    print("HALT cleared. New entries may open on the next cycle.\n")
    print("It said:")
    print(was)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("reason", nargs="*",
                   help="why you are halting — recorded in the file and the alert")
    p.add_argument("--status", action="store_true",
                   help="report whether HALT is engaged, and why")
    p.add_argument("--clear", action="store_true",
                   help="remove HALT and re-enable new entries")
    args = p.parse_args(argv)

    if args.status and args.clear:
        print("--status and --clear are mutually exclusive.", file=sys.stderr)
        return 2
    if args.status:
        return status()
    if args.clear:
        return clear()

    reason = " ".join(args.reason).strip()
    if not reason:
        # A halt with no stated reason is one nobody can safely clear later:
        # the next operator cannot tell a deliberate stop from a stray file.
        print("Refusing to halt without a reason.\n\n"
              '  ./scripts/halt.sh "broker returning bad fills"\n'
              "  ./scripts/halt.sh --status\n"
              "  ./scripts/halt.sh --clear\n", file=sys.stderr)
        return 2
    return engage(reason)


if __name__ == "__main__":
    sys.exit(main())
