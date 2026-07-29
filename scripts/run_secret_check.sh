#!/bin/zsh
# Weekly secret-exposure check — invoked by launchd
# (com.trading-agent.secretcheck), Saturdays alongside the restore drill.
#
# docs/secrets_rotation.md:56 says "do not answer from memory, run the check".
# Until 2026-07-29 (W4-5) the check ran in NO job at all — no cron, no plist,
# no CI — so the instruction pointed at something nobody was running.
#
# It lives here and not in CI on purpose: the script reads `.env` and the local
# Claude transcripts, neither of which exists on a CI runner, where it would
# return 0 unconditionally and read as coverage it does not provide. See the
# comment block in .github/workflows/ci.yml.
#
# Exit 1 means a live secret is sitting in plaintext ON THIS MACHINE. That is
# information, not an emergency: rotating makes the local copy worthless.
# Never scrub the transcript — it destroys an audit record and leaves the value
# in every backup.
cd "$(dirname "$0")/.." || exit 1
echo "=== secret check $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> logs/cron.log
.venv/bin/python scripts/check_secret_exposure.py >> logs/cron.log 2>&1
