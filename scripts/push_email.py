#!/usr/bin/env python3
"""
Push paper-radar daily digest to Hotmail/Outlook via SMTP.

Reads ~/.claude/skills/paper-radar/data/email_config.json:
{
  "smtp_host": "smtp-mail.outlook.com",
  "smtp_port": 587,
  "smtp_user": "brunchlife@hotmail.com",
  "smtp_password": "<app password>",
  "to": "brunchlife@hotmail.com"
}

Or via env: SMTP_USER, SMTP_PASSWORD, SMTP_HOST, SMTP_PORT, EMAIL_TO.

If creds are not configured, exits 2 (skipped — caller should treat as "no
fallback available", not a failure of the email path itself).

Usage:
    python3 push_email.py <digest_dir>
"""
import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = SKILL_ROOT / "data" / "email_config.json"


def load_config() -> dict:
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.load(open(CONFIG_FILE))
        except Exception as e:
            print(f"[push_email] cannot read {CONFIG_FILE}: {e}", file=sys.stderr)
    cfg.setdefault("smtp_host", os.environ.get("SMTP_HOST", "smtp-mail.outlook.com"))
    cfg.setdefault("smtp_port", int(os.environ.get("SMTP_PORT", 587)))
    cfg.setdefault("smtp_user", os.environ.get("SMTP_USER", ""))
    cfg.setdefault("smtp_password", os.environ.get("SMTP_PASSWORD", ""))
    cfg.setdefault("to", os.environ.get("EMAIL_TO", "brunchlife@hotmail.com"))
    return cfg


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    digest_dir = Path(sys.argv[1]).resolve()
    if not digest_dir.is_dir():
        print(f"[push_email] not a directory: {digest_dir}", file=sys.stderr)
        sys.exit(1)

    cfg = load_config()
    if not cfg["smtp_user"] or not cfg["smtp_password"]:
        print(
            f"[push_email] SKIP — fill {CONFIG_FILE} with smtp_user/password "
            "(Hotmail app-password) or set SMTP_USER/SMTP_PASSWORD env vars.",
            file=sys.stderr,
        )
        sys.exit(2)

    date_str = digest_dir.name
    ranked = digest_dir / "ranked.md"
    cands = digest_dir / "candidates.md"
    if ranked.exists():
        body = ranked.read_text(encoding="utf-8")
        attachment = ranked
    elif cands.exists():
        body = cands.read_text(encoding="utf-8")
        attachment = cands
    else:
        body = "今天 arXiv 没找到值得读的。"
        attachment = None

    msg = EmailMessage()
    msg["Subject"] = f"📚 Paper Radar — {date_str}"
    msg["From"] = cfg["smtp_user"]
    msg["To"] = cfg["to"]
    # Plain text first; markdown body in HTML alternative for clients that prefer html
    msg.set_content(body)
    msg.add_alternative(
        f"<pre style='font-family:Menlo,monospace;font-size:13px'>{body}</pre>",
        subtype="html",
    )
    if attachment:
        msg.add_attachment(
            attachment.read_bytes(),
            maintype="text",
            subtype="markdown",
            filename=attachment.name,
        )

    try:
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.send_message(msg)
    except Exception as e:
        print(f"[push_email] SMTP failed: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[push_email] OK → {cfg['to']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
