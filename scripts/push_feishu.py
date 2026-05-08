#!/usr/bin/env python3
"""
Push paper-radar daily digest to Feishu (Lark) via custom-bot webhook.

Sends one summary card + one detail card per paper (full 6-section deep-read),
so the user can read everything inside Feishu without opening the file.

Reads ~/.claude/skills/paper-radar/data/webhook.txt for the webhook URL
(gitignored, set once by the user).

Usage:
    python3 push_feishu.py <digest_dir>
    python3 push_feishu.py /Users/chenyinuo/code/papers/digests/2026-05-07

Optional flags via env:
    PUSH_DETAILS=0   skip the detail cards, summary only
    KEYWORD=Papers   keyword that must appear in every payload (default Papers)

Exit code: 0 on full success, 1 if any send fails (we still try the rest).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEBHOOK_FILE = SKILL_ROOT / "data" / "webhook.txt"

# Feishu single-card / single-element soft limit. Empirically ~30 KB JSON, but
# Markdown rendering tends to break sooner around 28 KB content. Stay under 25 KB.
MAX_MD_ELEM = 25_000
KEYWORD = os.environ.get("KEYWORD", "Papers")


def load_webhook() -> str:
    url = os.environ.get("FEISHU_WEBHOOK", "").strip()
    if url:
        return url
    if DEFAULT_WEBHOOK_FILE.exists():
        url = DEFAULT_WEBHOOK_FILE.read_text(encoding="utf-8").strip()
        if url:
            return url
    raise SystemExit(
        "No Feishu webhook configured. Set FEISHU_WEBHOOK env var or "
        f"create {DEFAULT_WEBHOOK_FILE} (one line, the webhook URL)."
    )


def parse_ranked_md_full(text: str) -> list[dict]:
    """Split ranked.md into per-paper chunks. Returns list of:
        {title, arxiv, tagline, full_md}
    full_md is the entire chunk for that paper (sections 1-6 + tail).
    """
    # ranked.md uses one of two heading formats:
    #   A) wrapper: `# [#N score=NN] <title>` (newer daily-routine output)
    #      followed by an inner `# <title>` heading inside the chunk.
    #   B) flat:    `# <title>` (older manual runs).
    # Prefer (A) when wrapper lines exist, else fall back to (B).
    has_wrapper = bool(re.search(r"(?m)^# \[#\d+\s+score=", text))
    if has_wrapper:
        chunks = re.split(r"(?m)^# \[#\d+\s+score=[^\]]+\]\s*", text)[1:]
    else:
        parts = re.split(r"(?m)^# (?!Paper Radar)", text)
        if not parts:
            return []
        chunks = parts[1:]
    papers = []
    for chunk in chunks:
        lines = chunk.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        # Trim trailing horizontal-rule separators that came from the concat
        body = re.sub(r"\n+---\s*$", "", body).strip()

        # Multiple arxiv-link formats: `**arxiv**: URL`, `**arXiv:** URL`,
        # `arxiv: URL`, or just a bare arxiv URL anywhere in body.
        arxiv = ""
        for pat in (
            r"\*\*arxiv:?\*\*:?\s*(\S+)",
            r"(?m)^\s*arxiv:?\s*(\S+)",
            r"(https?://arxiv\.org/abs/\d{4}\.\d{4,5}\S*)",
        ):
            m = re.search(pat, body, flags=re.IGNORECASE)
            if m:
                arxiv = m.group(1)
                break
        # If we got something that's just `2605.04649`, expand to abs URL
        if arxiv and not arxiv.startswith("http"):
            am = re.search(r"(\d{4}\.\d{4,5})", arxiv)
            if am:
                arxiv = f"https://arxiv.org/abs/{am.group(1)}"

        # Tagline = first paragraph under "## 1. ..."
        tagline = ""
        sec1_m = re.search(
            r"##\s*1\.\s*[^\n]*\n(.*?)(?=\n##\s|\n---|\Z)",
            body,
            flags=re.DOTALL,
        )
        if sec1_m:
            paras = [p.strip() for p in re.split(r"\n\s*\n", sec1_m.group(1).strip()) if p.strip()]
            if paras:
                tagline = re.sub(r"^[>*\s]+", "", paras[0])
                tagline = re.sub(r"\s+", " ", tagline)[:200]

        # Reassemble full_md as `# Title\n\nbody`
        full_md = f"# {title}\n\n{body}"
        papers.append(
            {
                "title": title,
                "arxiv": arxiv,
                "tagline": tagline,
                "full_md": full_md,
            }
        )
    return papers


def parse_candidates_md(text: str, top_n: int = 15) -> list[dict]:
    """Fallback when no ranked.md (Stage 2 didn't run): extract from candidates.md."""
    chunks = re.split(r"(?m)^## \[\d+\]", text)[1:]
    papers = []
    for c in chunks[:top_n]:
        title_m = re.match(r"\s*([^\n]+)", c)
        title = title_m.group(1).strip() if title_m else ""
        arxiv_m = re.search(r"arxiv:\s*(\S+)", c)
        arxiv = arxiv_m.group(1) if arxiv_m else ""
        absm = re.search(r"^\s*>\s*(.+?)$", c, flags=re.MULTILINE)
        tag = absm.group(1).strip()[:200] if absm else ""
        papers.append({"title": title, "arxiv": arxiv, "tagline": tag, "full_md": ""})
    return papers


def split_markdown_for_card(md: str, limit: int = MAX_MD_ELEM) -> list[str]:
    """If markdown body exceeds Feishu's per-element limit, chunk it on
    paragraph boundaries. Each returned piece becomes a separate `markdown`
    element in the SAME card.
    """
    if len(md) <= limit:
        return [md]
    # Split on blank line (paragraph), then greedy-pack
    paras = md.split("\n\n")
    out, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 > limit and buf:
            out.append(buf)
            buf = p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf:
        out.append(buf)
    return out


def build_summary_card(date_str: str, papers: list[dict], digest_dir: str) -> dict:
    """First card: today's overview / top-N table of contents."""
    if not papers:
        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"📚 **Papers Radar — {date_str}**\n\n"
                    "**今天 arXiv 上没有命中你 stack 的高分 Papers。**\n\n"
                    "（建议放空一天，或运行 `扫论文` 提高 K 值。）"
                ),
            }
        ]
    else:
        toc_lines = []
        for i, p in enumerate(papers, 1):
            link = f"[{p['title']}]({p['arxiv']})" if p["arxiv"] else f"**{p['title']}**"
            line = f"**{i}.** {link}"
            if p["tagline"]:
                line += f"\n　　_{p['tagline']}_"
            toc_lines.append(line)
        elements = [
            {
                "tag": "markdown",
                "content": (
                    f"今天 **{len(papers)} 篇 Papers** 值得读，按评分序。"
                    "\n下面每篇会单独发一张详情卡片（6-section 全文）。"
                ),
            },
            {"tag": "hr"},
            {"tag": "markdown", "content": "\n\n".join(toc_lines)},
            {"tag": "hr"},
            {
                "tag": "markdown",
                "content": (
                    f"完整文件：`{digest_dir}/ranked.md`\n"
                    f"候选清单：`{digest_dir}/candidates.md`"
                ),
            },
        ]

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📚 Papers Radar — {date_str} (overview)",
                },
                "template": "blue",
            },
            "elements": elements,
        },
    }


def build_detail_card(date_str: str, idx: int, total: int, paper: dict) -> dict:
    """One card per paper, containing full 6-section markdown."""
    body = paper["full_md"] or "(deep-read content unavailable)"
    # Make sure keyword is present (Feishu bot may filter)
    if KEYWORD and KEYWORD.lower() not in body.lower():
        body = f"_(Papers Radar)_\n\n" + body
    # Split if too long
    md_chunks = split_markdown_for_card(body)
    elements = []
    for chunk in md_chunks:
        elements.append({"tag": "markdown", "content": chunk})

    # Footer with arxiv link if available
    if paper["arxiv"]:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "markdown",
                "content": f"🔗 **arxiv**: {paper['arxiv']}",
            }
        )

    title_short = paper["title"][:60] + ("…" if len(paper["title"]) > 60 else "")
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📄 [{idx}/{total}] {title_short}",
                },
                "template": "wathet",  # lighter blue for details
            },
            "elements": elements,
        },
    }


def post(webhook: str, payload: dict, timeout: int = 20) -> dict:
    r = requests.post(webhook, json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def post_safe(webhook: str, payload: dict, label: str) -> bool:
    """Post one message; return True on success, log on failure."""
    try:
        resp = post(webhook, payload)
    except Exception as e:
        print(f"[push_feishu] {label} POST exception: {e}", file=sys.stderr)
        return False
    code = resp.get("code") if isinstance(resp, dict) else None
    if code in (0, None) and resp.get("StatusCode", 0) == 0:
        print(f"[push_feishu] {label} OK", file=sys.stderr)
        return True
    print(f"[push_feishu] {label} returned: {resp}", file=sys.stderr)
    return False


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)

    digest_dir = Path(sys.argv[1]).resolve()
    if not digest_dir.is_dir():
        print(f"[push_feishu] not a directory: {digest_dir}", file=sys.stderr)
        sys.exit(1)

    date_str = digest_dir.name
    push_details = os.environ.get("PUSH_DETAILS", "1") != "0"

    ranked = digest_dir / "ranked.md"
    cands = digest_dir / "candidates.md"
    if ranked.exists():
        papers = parse_ranked_md_full(ranked.read_text(encoding="utf-8"))
        papers = papers[:10]
        source = "ranked.md"
    elif cands.exists():
        papers = parse_candidates_md(cands.read_text(encoding="utf-8"), top_n=15)
        push_details = False  # no full content available
        source = "candidates.md"
    else:
        papers = []
        source = "(no digest files)"
    print(f"[push_feishu] source={source}, papers={len(papers)}, push_details={push_details}", file=sys.stderr)

    webhook = load_webhook()
    failures = 0

    # 1. Summary card
    summary = build_summary_card(date_str, papers, str(digest_dir))
    if not post_safe(webhook, summary, "summary"):
        failures += 1

    # 2. Detail cards (one per paper)
    if push_details and papers:
        for i, p in enumerate(papers, 1):
            time.sleep(0.3)  # be polite, well under Feishu's 100 msg/min cap
            ok = post_safe(
                webhook,
                build_detail_card(date_str, i, len(papers), p),
                label=f"detail #{i} ({p['title'][:40]})",
            )
            if not ok:
                failures += 1

    if failures:
        print(f"[push_feishu] DONE with {failures} failure(s)")
        sys.exit(1)
    print(f"[push_feishu] DONE — pushed 1 summary + {len(papers) if push_details else 0} detail cards")
    sys.exit(0)


if __name__ == "__main__":
    main()
