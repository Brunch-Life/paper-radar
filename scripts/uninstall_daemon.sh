#!/bin/bash
# Uninstall paper-radar launchd agent.
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.chenyinuo.paper-radar.plist"
LABEL="com.chenyinuo.paper-radar"

if launchctl list | grep -q "$LABEL"; then
  echo "→ stopping $LABEL"
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || \
    launchctl unload "$PLIST" 2>/dev/null || true
fi

if [[ -f "$PLIST" ]]; then
  rm "$PLIST"
  echo "→ removed $PLIST"
fi

echo "✅ paper-radar daemon uninstalled."
