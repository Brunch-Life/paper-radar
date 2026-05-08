#!/usr/bin/env bash
# One-time setup: create venv inside the skill dir and install Python deps.
# Idempotent — safe to re-run.

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"

if [[ ! -d "$VENV" ]]; then
  echo "[setup] Creating venv at $VENV"
  python3 -m venv "$VENV"
fi

echo "[setup] Installing/updating dependencies"
"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install -r "$SKILL_DIR/requirements.txt" --quiet

echo "[setup] Done."
echo "  Aggregator: $SKILL_DIR/scripts/aggregate.py"
echo "  Reference data: $SKILL_DIR/data/"
echo "  Outputs go to: $SKILL_DIR/digests/"
