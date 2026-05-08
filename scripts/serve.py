#!/usr/bin/env python3
"""Run the paper-radar FastAPI server on http://127.0.0.1:7878.

Used both for manual ad-hoc runs and as the launchd entry point.
"""
import os
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

import uvicorn
from paper_radar.api import app

PORT = int(os.environ.get("PAPER_RADAR_PORT", 7878))
HOST = os.environ.get("PAPER_RADAR_HOST", "127.0.0.1")


def main():
    print(f"[paper-radar] serving on http://{HOST}:{PORT}", file=sys.stderr)
    print(f"[paper-radar] frontend: {SKILL_ROOT / 'frontend'}", file=sys.stderr)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
