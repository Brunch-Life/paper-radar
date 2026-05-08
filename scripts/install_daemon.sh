#!/bin/bash
# Install paper-radar as a launchd user agent.
# Effect: paper-radar FastAPI server starts at user login on port 7878.
#
# Idempotent: safe to re-run after editing the plist.
set -euo pipefail

PLIST_SRC="$HOME/.claude/skills/paper-radar/launchd/com.chenyinuo.paper-radar.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.chenyinuo.paper-radar.plist"
LABEL="com.chenyinuo.paper-radar"

if [[ ! -f "$PLIST_SRC" ]]; then
  echo "❌ source plist not found: $PLIST_SRC" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
mkdir -p "$HOME/Library/Logs"

# Unload existing (if any)
if launchctl list | grep -q "$LABEL"; then
  echo "→ stopping existing $LABEL"
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || \
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# Copy + load
cp "$PLIST_SRC" "$PLIST_DST"
echo "→ copied plist to $PLIST_DST"

launchctl bootstrap "gui/$UID" "$PLIST_DST"
launchctl enable "gui/$UID/$LABEL"
launchctl kickstart -k "gui/$UID/$LABEL"

sleep 1

if launchctl list | grep -q "$LABEL"; then
  echo "✅ paper-radar daemon running."
  echo "   logs: ~/Library/Logs/paper-radar.{out,err}.log"
  echo "   url:  http://127.0.0.1:7878"
  echo ""
  echo "Health check:"
  curl -sf http://127.0.0.1:7878/healthz | python3 -m json.tool || \
    echo "   (server not responding yet — wait a few seconds and retry)"
else
  echo "⚠️  daemon not visible in launchctl list — check"
  echo "   ~/Library/Logs/paper-radar.err.log"
fi
