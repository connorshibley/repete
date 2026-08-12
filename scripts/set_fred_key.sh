#!/bin/sh
# Put the FRED API key into .env without it passing through anything else.
#
#   ./scripts/set_fred_key.sh
#
# Same reasoning as set_fmp_key.sh, set_alert_webhook.sh and repete2's
# set_oanda_creds.sh, and the same three leaks avoided: shell history, an
# assistant's context, a loose umask. `read -r` with echo off, a 600 file, and
# nothing prints the value — including on failure. Every message below
# describes the key rather than quoting it.
set -eu
cd "${AGENT_ROOT:-$(dirname "$0")/..}"
ROOT="$(pwd)"

ENV_FILE="${FRED_ENV_FILE:-$ROOT/.env}"
[ -f "$ENV_FILE" ] || { [ -f "$ROOT/.env.example" ] && cp "$ROOT/.env.example" "$ENV_FILE"; }
[ -f "$ENV_FILE" ] || { printf 'FAILED: no .env and no .env.example.\n'; exit 3; }
chmod 600 "$ENV_FILE"

printf '\n'
printf 'FRED API key -> %s\n' "$ENV_FILE"
printf 'Free key: https://fred.stlouisfed.org/docs/api/api_key.html (no card)\n'
printf 'The key is not echoed and is not written to your shell history.\n\n'
printf 'Key: '

# EXIT restores echo however we leave; INT/TERM/HUP additionally abort. Without
# this, Ctrl+C at the prompt leaves the terminal silently swallowing every
# keystroke afterwards.
trap 'stty echo 2>/dev/null || true' EXIT
trap 'stty echo 2>/dev/null || true; printf "\naborted\n"; exit 130' INT TERM HUP
stty -echo 2>/dev/null || true
read -r KEY
stty echo 2>/dev/null || true
trap - EXIT INT TERM HUP
printf '\n'

# ---- validate: refuse only what CANNOT be a key -----------------------------
# FRED keys are 32 lowercase hex characters. That is a tighter shape than FMP's,
# so the check can be stricter without risking a false refusal on a real key.
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
if [ "$LEN" -ne 32 ]; then
  printf 'REFUSED: %s characters; a FRED key is 32. Nothing written.\n' "$LEN"
  printf 'Did the paste get truncated or doubled?\n'
  exit 2
fi
case "$KEY" in
  *[!0-9a-f]*)
    printf 'REFUSED: a FRED key is lowercase hex only. Nothing written.\n'
    exit 2 ;;
esac
printf 'Accepted a %s-character key.\n' "$LEN"

# ---- write, exactly one line -----------------------------------------------
# awk replaces in place and collapses duplicates. Setting a key by hand once
# left repete1 with FIVE ANTHROPIC_API_KEY lines; python-dotenv takes the last,
# so it ran on two keys concatenated while preflight reported all clear.
TMP="$ENV_FILE.tmp.$$"
awk -v val="$KEY" '
  /^FRED_API_KEY=/ { if (!done) { print "FRED_API_KEY=" val; done=1 } ; next }
  { print }
  END { if (!done) print "FRED_API_KEY=" val }
' "$ENV_FILE" > "$TMP"
mv "$TMP" "$ENV_FILE"
chmod 600 "$ENV_FILE"

N=$(grep -c '^FRED_API_KEY=' "$ENV_FILE" || true)
if [ "$N" != "1" ]; then
  printf 'FAILED: %s now has %s FRED_API_KEY lines, expected 1.\n' "$ENV_FILE" "$N"
  exit 3
fi
printf 'wrote %s\n' "$ENV_FILE"

# ---- prove it, with ONE call -----------------------------------------------
# A write that "succeeded" against a key the API rejects is the failure mode
# this whole script exists to remove.
printf '\nVerifying with one live call...\n'
STATUS=$("$ROOT/.venv/bin/python" -c "
import os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.join('$ROOT', 'src'))
from dotenv import load_dotenv
load_dotenv(os.path.join('$ROOT', '.env'))
key = os.environ.get('FRED_API_KEY', '').strip()
if not key:
    print('empty-on-disk'); raise SystemExit
url = ('https://api.stlouisfed.org/fred/series?series_id=GDPC1'
       '&file_type=json&api_key=' + key)
try:
    with urllib.request.urlopen(url, timeout=20) as r:
        body = r.read(400).decode('utf-8', 'replace')
    print('ok' if r.status == 200 and '\"seriess\"' in body else 'unexpected-body')
except urllib.error.HTTPError as e:
    print('http-%d' % e.code)
except Exception as e:
    print('error-%s' % type(e).__name__)
")

case "$STATUS" in
  ok)
    printf 'Verified: the key works.\n'
    printf '\nNext: ./scripts/probe_alfred_vintages.py\n' ;;
  http-400|http-401|http-403)
    printf 'FAILED: FRED rejected the key (%s). It is written but not valid.\n' "$STATUS"
    printf 'Note FRED answers 400 for a bad key, not 401.\n'
    exit 4 ;;
  empty-on-disk)
    printf 'FAILED: the key did not survive to disk.\n'; exit 4 ;;
  *)
    printf 'FAILED: could not verify (%s).\n' "$STATUS"
    printf 'The key is written; re-run the probe once the network is back.\n'
    exit 4 ;;
esac
