#!/bin/sh
# Render the launchd plist templates for THIS checkout and install them.
#
# The committed scripts/com.trading-agent.*.plist carry a {{AGENT_ROOT}}
# placeholder instead of a hard-coded path — the repo can move (or be cloned
# fresh) without shipping a stale absolute path that makes every scheduled job
# silently no-op. This script substitutes the real location of THIS checkout,
# validates each rendered plist, and installs them to ~/Library/LaunchAgents.
#
#   sh scripts/install_launchd.sh          # render + validate + install (unloaded)
#   sh scripts/install_launchd.sh --load   # also bootstrap/kickstart them
#
# The jobs run scripts/run_*.sh, which invoke .venv/bin/python — create the
# venv first (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt).
# Times in the plists are host wall-clock (ET assumed); the container scheduler
# (scripts/scheduler.py) is timezone-correct via ZoneInfo if you deploy that way.
set -eu

AGENT_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
DEST="$HOME/Library/LaunchAgents"

case "$AGENT_ROOT" in
  *'&'*|*'\'*)
    echo "ERROR: repo path contains '&' or '\\', which this installer cannot" >&2
    echo "       safely substitute. Move the checkout to a simpler path." >&2
    exit 1 ;;
esac

mkdir -p "$DEST" "$AGENT_ROOT/logs"
chmod +x "$AGENT_ROOT"/scripts/run_*.sh 2>/dev/null || true

installed=0
for tmpl in "$AGENT_ROOT"/scripts/com.trading-agent.*.plist; do
  name=$(basename "$tmpl")
  out="$DEST/$name"
  # awk gsub replacement is literal enough for filesystem paths (the '&'/'\'
  # cases are rejected above).
  awk -v root="$AGENT_ROOT" \
    '{gsub(/\{\{AGENT_ROOT\}\}/, root); print}' "$tmpl" > "$out"
  if ! plutil -lint "$out" >/dev/null; then
    echo "ERROR: $out failed plutil -lint" >&2
    exit 1
  fi
  echo "installed $name"
  installed=$((installed + 1))
done
echo "$installed plists rendered to $DEST"

if [ "${1:-}" = "--load" ]; then
  uid=$(id -u)
  for tmpl in "$AGENT_ROOT"/scripts/com.trading-agent.*.plist; do
    name=$(basename "$tmpl")
    label=${name%.plist}
    launchctl bootout "gui/$uid/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$uid" "$DEST/$name"
    echo "loaded $label"
  done
else
  echo "To load: sh scripts/install_launchd.sh --load"
  echo "  (or: launchctl bootstrap gui/\$(id -u) $DEST/com.trading-agent.*.plist)"
fi
