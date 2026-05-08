#!/usr/bin/env python3
"""
Notify orchestrator: try Feishu first, then email fallback if Feishu fails.

Used by the daily scheduled task (and manual invocations).

Usage:
    python3 notify.py <digest_dir>
    python3 notify.py 2026-05-07          # resolves to ~/code/papers/digests/2026-05-07
    python3 notify.py                     # uses today's date

Exit code:
    0 — at least one channel pushed successfully
    1 — both channels failed
    2 — digest dir not found
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_ROOT / "scripts"
DEFAULT_DIGESTS = Path.home() / "code" / "papers" / "digests"


def resolve_digest_dir(arg: Optional[str]) -> Path:
    if arg is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return DEFAULT_DIGESTS / date_str
    p = Path(arg)
    if p.is_absolute() and p.exists():
        return p
    # Treat arg as date or tag suffix
    return DEFAULT_DIGESTS / arg


def run(script: Path, digest_dir: Path) -> int:
    print(f"\n=== {script.name} {digest_dir} ===", file=sys.stderr)
    return subprocess.call(
        [sys.executable, str(script), str(digest_dir)],
        env={**os.environ},
    )


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    digest_dir = resolve_digest_dir(arg)
    if not digest_dir.is_dir():
        print(f"[notify] digest dir not found: {digest_dir}", file=sys.stderr)
        sys.exit(2)

    feishu_rc = run(SCRIPTS_DIR / "push_feishu.py", digest_dir)
    if feishu_rc == 0:
        print("[notify] Feishu push OK; skipping email.")
        # Also send email when configured AND user wants both? Default no — Feishu only.
        # If you want belt-and-suspenders, set ALWAYS_EMAIL=1.
        if os.environ.get("ALWAYS_EMAIL") == "1":
            run(SCRIPTS_DIR / "push_email.py", digest_dir)
        sys.exit(0)

    print("[notify] Feishu failed; trying email fallback…", file=sys.stderr)
    email_rc = run(SCRIPTS_DIR / "push_email.py", digest_dir)
    if email_rc == 0:
        print("[notify] Email fallback OK.")
        sys.exit(0)
    if email_rc == 2:
        print("[notify] No email configured; cannot fall back.", file=sys.stderr)
        sys.exit(1)
    print("[notify] Both Feishu and email failed.", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
