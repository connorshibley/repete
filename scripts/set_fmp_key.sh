#!/bin/sh
# Put the FMP API key into .env without it passing through anything else.
#
#   ./scripts/set_fmp_key.sh
#
# Same reasoning as set_alert_webhook.sh and repete2's set_oanda_creds.sh, and
# the same three leaks avoided: shell history, an assistant's context, a loose
# umask. `read -r` with echo off, a 600 file, and nothing prints the value —
# including on failure. Every message below describes the key rather than
# quoting it.
#
# 2026-07-30 produced BOTH failure modes this guards against, hours apart: an
# OANDA token pasted into a chat, and an ntfy topic that leaked via a screenshot
# of the command used to read it back. Storage was never the weak point; the
# handoff was. So this script also never prints the key back.
set -eu
cd "${AGENT_ROOT:-$(dirname "$0")/..}"
ROOT="$(pwd)"

ENV_FILE="${FMP_ENV_FILE:-$ROOT/.env}"
[ -f "$ENV_FILE" ] || { [ -f "$ROOT/.env.example" ] && cp "$ROOT/.env.example" "$ENV_FILE"; }
[ -f "$ENV_FILE" ] || { printf 'FAILED: no .env and no .env.example.\n'; exit 3; }
chmod 600 "$ENV_FILE"

printf '\n'
printf 'FMP API key -> %s\n' "$ENV_FILE"
printf 'Free key: https://financialmodelingprep.com/developer/docs (no card)\n'
printf 'The key is not echoed and is not written to your shell history.\n\n'
printf 'Key: '

# EXIT restores echo however we leave; INT/TERM/HUP additionally abort. Without
# this, Ctrl+C at the prompt leaves the terminal silently swallowing every
# keystroke afterwards — which happened to the owner earlier today.
trap 'stty echo 2>/dev/null || true' EXIT
trap 'stty echo 2>/dev/null || true; printf "\naborted\n"; exit 130' INT TERM HUP
stty -echo 2>/dev/null || true
read -r KEY
stty echo 2>/dev/null || true
trap - EXIT INT TERM HUP
printf '\n'

# ---- validate: refuse only what CANNOT be a key -----------------------------
LEN=$(printf '%s' "$KEY" | wc -c | tr -d ' ')
if [ -z "$KEY" ]; then
  printf 'REFUSED: empty. Nothing written.\n'
  exit 2
fi
case "$KEY" in
  http*)
    printf 'REFUSED: that is a URL, not an API key. Nothing written.\n'
    exit 2 ;;
esac
if [ "$LEN" -lt 20 ]; then
  printf 'REFUSED: %s characters, too short to be an FMP key. Nothing written.\n' "$LEN"
  printf 'Did the paste get truncated?\n'
  exit 2
fi
# The doubled-paste failure that got past repete2's checks this morning.
HALF=$(printf '%s' "$KEY" | cut -c1-$((LEN / 2)))
if [ "$KEY" = "$HALF$HALF" ]; then
  printf 'REFUSED: the value is the same string twice — a doubled paste.\n'
  printf 'Nothing written.\n'
  exit 2
fi
printf 'Accepted a %s-character key.\n' "$LEN"

# ---- write, exactly one line -----------------------------------------------
# awk replaces in place and collapses duplicates. Setting a key by hand once
# left repete1 with FIVE ANTHROPIC_API_KEY lines; python-dotenv takes the last,
# so it ran on two keys concatenated while preflight reported all clear.
TMP="$ENV_FILE.tmp.$$"
awk -v val="$KEY" '
  /^FMP_API_KEY=/ { if (!done) { print "FMP_API_KEY=" val; done=1 } ; next }
  { print }
  END { if (!done) print "FMP_API_KEY=" val }
' "$ENV_FILE" > "$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"

N=$(grep -c '^FMP_API_KEY=' "$ENV_FILE" || true)
if [ "$N" != "1" ]; then
  printf 'FAILED: %s now has %s FMP_API_KEY lines, expected 1.\n' "$ENV_FILE" "$N"
  exit 3
fi
printf 'wrote %s\n' "$ENV_FILE"

# ---- prove it, with ONE call -----------------------------------------------
# A write that "succeeded" against a key the API rejects is the failure mode
# this whole script exists to remove. One call, so the 250/day free budget is
# essentially untouched.
printf '\nVerifying with one live call...\n'
STATUS=$("$ROOT/.venv/bin/python" -c "
import os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.join('$ROOT', 'src'))
from dotenv import load_dotenv
load_dotenv(os.path.join('$ROOT', '.env'))
key = os.environ.get('FMP_API_KEY', '').strip()
if not key:
    print('empty-on-disk'); raise SystemExit
url = ('https://financialmodelingprep.com/api/v3/quote-short/AAPL?apikey=' + key)
try:
    with urllib.request.urlopen(url, timeout=20) as r:
        body = r.read(400).decode('utf-8', 'replace')
    print('ok' if r.status == 200 and '\"symbol\"' in body else 'unexpected-body')
except urllib.error.HTTPError as e:
    print('http-%d' % e.code)
except Exception as e:
    print('error-%s' % type(e).__name__)
")

case "$STATUS" in
  ok)
    printf 'Verified: the key works and 1 of your 250 daily calls was used.\n'
    printf '\nNext: ./scripts/probe_fmp_lookahead.py\n' ;;
  http-401|http-403)
    printf 'FAILED: FMP rejected the key (%s). It is written but not valid.\n' "$STATUS"
    printf 'Check you copied the whole key from the dashboard.\n'
    exit 4 ;;
  empty-on-disk)
    printf 'FAILED: the key did not survive to disk.\n'; exit 4 ;;
  *)
    printf 'FAILED: could not verify (%s).\n' "$STATUS"
    printf 'The key is written; re-run the probe once the network is back.\n'
    exit 4 ;;
esac
