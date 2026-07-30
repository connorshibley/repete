#!/bin/sh
# Put ALERT_WEBHOOK_URL into every bot's .env, then PROVE an alert arrives.
#
#   ./scripts/set_alert_webhook.sh
#
# Why a script instead of editing .env by hand
# --------------------------------------------
# The URL is a credential. An ntfy topic is not access-controlled: anyone
# holding it can read every alert this bot ever raises AND publish fake ones.
# `scripts/check_secret_exposure.py` already treats it as a secret. So the same
# three leaks the OANDA script exists to prevent apply here — shell history, an
# assistant's context, a loose umask — and are avoided the same way: `read -r`
# with echo off, a 600 file, and nothing ever printing the value, including on
# failure.
#
# It also does the thing the last four alerting bugs were all about: it does
# not claim success. The last thing it does is send a REAL alert and require
# `alerting.send()` to answer "webhook". "desktop" and "log-only" both mean the
# webhook did not work, and both look like success if you only read the exit
# code of the write.
#
# Duplicate-line guard: setting a key by hand once produced FIVE
# ANTHROPIC_API_KEY lines in repete1's .env; python-dotenv takes the last, so
# the agent ran with two keys concatenated while preflight reported all clear.
# The awk below replaces in place and collapses duplicates, and the count is
# asserted afterwards.
set -eu
cd "${AGENT_ROOT:-$(dirname "$0")/..}"
ROOT="$(pwd)"

# All three bots share one channel; `alerting.SOURCE` is what tells them apart.
TARGETS="$ROOT $HOME/bots/repete1 $HOME/bots/repete2"

printf '\n'
printf 'Alert webhook -> the .env of every bot that has one.\n'
printf 'The URL is not echoed and is not written to your shell history.\n\n'
printf 'ntfy: https://ntfy.sh/<your-topic>\n'
printf 'URL: '

stty -echo 2>/dev/null || true
read -r URL
stty echo 2>/dev/null || true
printf '\n'

# ---- validate ---------------------------------------------------------------
# Refuse only what CANNOT work. Every message describes the value instead of
# quoting it, so a mistake here does not put the secret on screen.
if [ -z "$URL" ]; then
  printf 'REFUSED: empty. Nothing written.\n'
  exit 2
fi

case "$URL" in
  https://*) ;;
  http://*)
    printf 'REFUSED: plain http. The URL is a credential and every alert body\n'
    printf 'would cross the network in clear text. Use https. Nothing written.\n'
    exit 2 ;;
  *)
    printf 'REFUSED: not a URL — expected it to start with https://. Found a\n'
    printf '%s-character value. Nothing written.\n' \
           "$(printf '%s' "$URL" | wc -c | tr -d ' ')"
    exit 2 ;;
esac

HOST=$(printf '%s' "$URL" | sed -E 's#^https://([^/]+).*#\1#')
PATH_PART=$(printf '%s' "$URL" | sed -E 's#^https://[^/]+##')

if [ "$HOST" = "ntfy.sh" ]; then
  TOPIC=$(printf '%s' "$PATH_PART" | sed 's#^/##; s#/$##')
  if [ -z "$TOPIC" ]; then
    printf 'REFUSED: an ntfy URL with no topic. Expected\n'
    printf 'https://ntfy.sh/<topic>. Nothing written.\n'
    exit 2
  fi
  case "$TOPIC" in
    */*)
      printf 'REFUSED: the ntfy path has more than one segment; a topic is a\n'
      printf 'single name. Nothing written.\n'
      exit 2 ;;
  esac
  # A guessable topic is a public inbox. This is the whole access control ntfy
  # has, so a short one is not a style preference — it is the security model.
  if [ "$(printf '%s' "$TOPIC" | wc -c | tr -d ' ')" -lt 13 ]; then
    printf 'REFUSED: that topic is under 12 characters, so it is guessable —\n'
    printf 'and an ntfy topic is the ONLY thing protecting the channel. Anyone\n'
    printf 'who guesses it reads every alert and can publish fake ones.\n\n'
    printf 'Generate one:  echo "repete-alerts-$(openssl rand -hex 6)"\n'
    printf '\nNothing written.\n'
    exit 2
  fi
  printf 'Recognised: ntfy.sh, topic %s characters.\n' \
         "$(printf '%s' "$TOPIC" | wc -c | tr -d ' ')"
else
  printf 'Recognised: %s (generic receiver).\n' "$HOST"
fi

# ---- write ------------------------------------------------------------------
WROTE=""
for T in $TARGETS; do
  [ -d "$T" ] || { printf 'skip  %s (no such directory)\n' "$T"; continue; }
  ENV_FILE="$T/.env"
  if [ ! -f "$ENV_FILE" ]; then
    [ -f "$T/.env.example" ] || { printf 'skip  %s (no .env)\n' "$T"; continue; }
    cp "$T/.env.example" "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
  TMP="$ENV_FILE.tmp.$$"
  awk -v val="$URL" '
    /^ALERT_WEBHOOK_URL=/ { if (!done) { print "ALERT_WEBHOOK_URL=" val; done=1 } ; next }
    { print }
    END { if (!done) print "ALERT_WEBHOOK_URL=" val }
  ' "$ENV_FILE" > "$TMP"
  mv "$TMP" "$ENV_FILE"
  chmod 600 "$ENV_FILE"

  N=$(grep -c '^ALERT_WEBHOOK_URL=' "$ENV_FILE" || true)
  if [ "$N" != "1" ]; then
    printf 'FAILED: %s now has %s ALERT_WEBHOOK_URL lines, expected 1.\n' \
           "$ENV_FILE" "$N"
    exit 3
  fi
  printf 'wrote %s\n' "$ENV_FILE"
  WROTE="$WROTE $T"
done

[ -n "$WROTE" ] || { printf '\nFAILED: nothing was written.\n'; exit 3; }

# ---- prove it ---------------------------------------------------------------
# The only check that counts. A write that "succeeded" into a URL that rejects
# the body is exactly the failure this whole change exists to remove.
printf '\nSending one real alert. Watch your phone.\n'
CHANNEL=$("$ROOT/.venv/bin/python" -c "
import sys
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')
import alerting
print(alerting.send('Repete: alert setup',
                    'If you are reading this on your phone, alerting works.'))
")

printf 'channel: %s\n' "$CHANNEL"
if [ "$CHANNEL" != "webhook" ]; then
  printf '\nFAILED: the alert did NOT go through the webhook.\n'
  printf '"%s" means it fell back to a channel that reaches nobody off this\n' "$CHANNEL"
  printf 'laptop. The URL is written but is not delivering — check the topic\n'
  printf 'in the app matches, and that the phone is subscribed.\n'
  exit 4
fi

printf '\nDelivered. If the notification did not appear, the app is not\n'
printf 'subscribed to that topic — the server accepted it either way.\n'
