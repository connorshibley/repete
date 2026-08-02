#!/bin/sh
# The operator's stop button. Blocks NEW ENTRIES; does NOT sell anything.
#
#   ./scripts/halt.sh "broker returning bad fills"   engage
#   ./scripts/halt.sh --status                       is it on, and why
#   ./scripts/halt.sh --clear                        resume trading
#
# A thin wrapper on scripts/halt.py so the thing you have to remember at 3am is
# one short command, not an interpreter path and a filename. cd's to the repo
# root because HALT is a RELATIVE path — a halt engaged from the wrong working
# directory is a stop button wired to nothing.
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/halt.py "$@"
