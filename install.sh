#!/usr/bin/env bash
# =====================================================================
# paper-radar installer
# Run from anywhere:
#   curl -fsSL https://raw.githubusercontent.com/Brunch-Life/paper-radar/main/install.sh | bash
# Or from a clone:
#   bash install.sh
#
# What this does:
#   1. Detects Python 3.11+ (or installs via uv if available)
#   2. Creates .venv with all deps (fastapi/mcp/arxiv/...)
#   3. Registers the FastAPI server as a launchd daemon (macOS)
#   4. Registers paper-radar MCP in both Claude Code and Claude Desktop
#   5. Tells you how to open the UI / pin to Dock
#
# Idempotent: re-run any time to update.
# Uninstall: bash scripts/uninstall_daemon.sh && rm -rf <this dir>/.venv
# =====================================================================
set -euo pipefail

# ---- Paths ---------------------------------------------------------
# Self-locating: resolve to the directory containing this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$SCRIPT_DIR"
PY="$SKILL_DIR/.venv/bin/python"

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }
red() { printf "\033[31m%s\033[0m\n" "$*" >&2; }

cyan "📚 paper-radar installer"
echo "  installing to: $SKILL_DIR"
echo

# ---- Step 1: detect Python ----------------------------------------
PYTHON_BIN=""
if command -v uv &>/dev/null; then
  echo "✓ found uv → will use it for venv + Python install"
  USE_UV=1
elif command -v python3.11 &>/dev/null; then
  PYTHON_BIN="$(command -v python3.11)"
  echo "✓ found python3.11 at $PYTHON_BIN"
  USE_UV=0
elif command -v python3.12 &>/dev/null; then
  PYTHON_BIN="$(command -v python3.12)"
  echo "✓ found python3.12 at $PYTHON_BIN"
  USE_UV=0
else
  red "❌ Need Python 3.11+ OR uv. Install one of:"
  red "    brew install uv               (recommended)"
  red "    brew install python@3.11"
  exit 1
fi

# ---- Step 2: create venv + install deps ---------------------------
cd "$SKILL_DIR"

if [[ ! -d .venv ]]; then
  echo "→ creating .venv"
  if [[ $USE_UV -eq 1 ]]; then
    uv venv .venv --python 3.11
  else
    "$PYTHON_BIN" -m venv .venv
  fi
fi

echo "→ installing/updating Python deps"
if [[ $USE_UV -eq 1 ]]; then
  uv pip install --python "$PY" \
    fastapi uvicorn mcp arxiv feedparser requests Scweet 2>&1 | tail -3
else
  "$PY" -m pip install --upgrade pip 2>&1 | tail -1
  "$PY" -m pip install \
    fastapi uvicorn mcp arxiv feedparser requests Scweet 2>&1 | tail -3
fi

# Verify imports
"$PY" -c "import fastapi, uvicorn, mcp, arxiv, feedparser, requests" \
  && green "✓ deps installed and importable"

# ---- Step 3: data dir ---------------------------------------------
if [[ ! -f data/author_boost.json ]]; then
  yellow "⚠️  data/author_boost.json not found — Stage-1 author boost will be disabled."
  yellow "    Copy data/author_boost.json.example and edit, OR run scripts/refresh_watchlist.sh."
fi

# ---- Step 4: launchd daemon (macOS only) --------------------------
if [[ "$(uname -s)" == "Darwin" ]]; then
  if [[ "${SKIP_DAEMON:-0}" == "1" ]]; then
    yellow "⚠ SKIP_DAEMON=1 — not registering launchd."
  else
    echo "→ registering launchd daemon (use SKIP_DAEMON=1 to skip)"
    bash "$SKILL_DIR/scripts/install_daemon.sh" 2>&1 | tail -6 || \
      yellow "(daemon install had issues — see above; you can still run scripts/serve.py manually)"
  fi
else
  yellow "⚠ Not on macOS — daemon install skipped. See docs/SERVER.md for systemd / cron setup."
fi

# ---- Step 5: register MCP server ----------------------------------
if command -v claude &>/dev/null; then
  echo "→ registering paper-radar in Claude Code MCP registry"
  claude mcp remove paper-radar 2>/dev/null || true
  claude mcp add -s user paper-radar "$PY" "$SKILL_DIR/scripts/mcp_server.py" 2>&1 | tail -2
else
  yellow "⚠ \`claude\` CLI not found — skipping Claude Code MCP registration."
fi

# Claude Desktop SDK config (separate from claude mcp add)
CLAUDE_CFG="$HOME/Library/Application Support/Claude/claude_desktop_config.json"
if [[ -f "$CLAUDE_CFG" && "$(uname -s)" == "Darwin" ]]; then
  echo "→ wiring paper-radar into Claude Desktop config"
  "$PY" - "$CLAUDE_CFG" "$PY" "$SKILL_DIR/scripts/mcp_server.py" <<'PY'
import json, sys, pathlib
cfg_path = pathlib.Path(sys.argv[1])
py_bin   = sys.argv[2]
mcp_path = sys.argv[3]
data = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
data.setdefault("mcpServers", {})["paper-radar"] = {
    "command": py_bin, "args": [mcp_path]
}
cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
print("✓ Claude Desktop config updated")
PY
fi

# ---- Step 6: post-install summary ---------------------------------
echo
green "==========================================="
green "✅ paper-radar installed"
green "==========================================="
echo
echo "🌐 UI:    http://127.0.0.1:7878"
echo "🔌 MCP:   paper-radar (registered for Claude Code + Claude Desktop)"
echo "📁 data:  ${PAPER_RADAR_DIGESTS_DIR:-$HOME/code/paper_reading_walkstream/digests/}"
echo
echo "Next steps:"
echo "  1. (optional) Edit data/author_boost.json (use the .example as template)"
echo "  2. Open http://127.0.0.1:7878 in Safari"
echo "  3. Safari → File → Add to Dock (macOS 14+) → pin Dock icon"
echo "  4. Run aggregator manually for testing:"
echo "       $PY scripts/aggregate.py --date \$(date +%F) --days 2"
echo "  5. Or invoke skill in Claude Code: 扫论文"
echo
echo "Logs:    ~/Library/Logs/paper-radar.{out,err}.log"
echo "Uninstall: bash scripts/uninstall_daemon.sh"
echo
