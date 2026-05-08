#!/usr/bin/env python3
"""
Generate a self-contained single-file HTML viewer for a paper-radar digest.

Output: `<digest_dir>/viewer.html` — opens in any browser, also drops cleanly
into Claude Desktop as an Artifact (paste the contents into a chat or `open`
the file via the local `file://` URL).

Usage:
    python3 build_viewer.py <digest_dir>
    python3 build_viewer.py 2026-05-08
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))
from paper_radar.scoring import HF_UPVOTE_CAP  # noqa

DEFAULT_DIGESTS = Path(
    os.environ.get(
        "PAPER_RADAR_DIGESTS_DIR",
        str(Path.home() / "code" / "paper_reading_walkstream" / "digests"),
    )
)


def parse_ranked_md(text: str) -> list[dict]:
    """Split ranked.md into per-paper dicts. Handles both heading formats."""
    has_wrapper = bool(re.search(r"(?m)^# \[#\d+\s+score=", text))
    if has_wrapper:
        chunks = re.split(r"(?m)^# \[#\d+\s+score=[^\]]+\]\s*", text)[1:]
    else:
        chunks = re.split(r"(?m)^# (?!Paper Radar)", text)[1:]

    out = []
    for chunk in chunks:
        lines = chunk.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        body = re.sub(r"\n+---\s*$", "", body).strip()

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
        aid_m = re.search(r"(\d{4}\.\d{4,5})", arxiv or body)
        aid = aid_m.group(1) if aid_m else ""
        if arxiv and not arxiv.startswith("http") and aid:
            arxiv = f"https://arxiv.org/abs/{aid}"

        # Tagline = first paragraph of section 1
        tagline = ""
        sec1 = re.search(
            r"##\s*1\.\s*[^\n]*\n(.*?)(?=\n##\s|\n---|\Z)",
            body,
            flags=re.DOTALL,
        )
        if sec1:
            paras = [
                p.strip()
                for p in re.split(r"\n\s*\n", sec1.group(1).strip())
                if p.strip()
            ]
            if paras:
                tagline = re.sub(r"^[>*\s]+", "", paras[0])
                tagline = re.sub(r"\s+", " ", tagline)[:240]

        out.append(
            {
                "title": title,
                "arxiv_id": aid,
                "arxiv_url": arxiv,
                "tagline": tagline,
                "full_md": body,
            }
        )
    return out


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📚 Paper Radar — {date}</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<style>
  :root {{
    --bg: #0f1115; --bg-card: #181b21; --bg-card-hover: #1f232b;
    --fg: #e6e8ea; --fg-muted: #8b8f97; --fg-accent: #6ab7ff;
    --border: #2a2f38; --tagline: #b8bcc4;
    --score-bg: #2563eb; --score-fg: #fff;
    --tag-s: #a855f7; --tag-a: #2563eb; --tag-b: #059669; --tag-c: #6b7280;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg: #fafafa; --bg-card: #fff; --bg-card-hover: #f5f5f5;
      --fg: #111; --fg-muted: #555; --fg-accent: #1d4ed8;
      --border: #e5e5e5; --tagline: #444;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans", sans-serif;
         background: var(--bg); color: var(--fg); margin: 0; padding: 0; line-height: 1.55; }}
  header {{ position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--border);
            padding: 16px 24px; z-index: 10; display: flex; align-items: center; gap: 16px; }}
  header h1 {{ margin: 0; font-size: 18px; font-weight: 600; }}
  header .meta {{ color: var(--fg-muted); font-size: 14px; }}
  #search {{ flex: 1; max-width: 420px; margin-left: auto; padding: 8px 12px;
             background: var(--bg-card); border: 1px solid var(--border); color: var(--fg);
             border-radius: 6px; font-size: 14px; }}
  main {{ max-width: 920px; margin: 0 auto; padding: 24px; }}
  .toc {{ margin-bottom: 24px; padding: 16px; background: var(--bg-card); border-radius: 8px; }}
  .toc h2 {{ margin: 0 0 12px; font-size: 14px; color: var(--fg-muted); font-weight: 500; text-transform: uppercase; letter-spacing: 0.05em; }}
  .toc ol {{ margin: 0; padding-left: 24px; }}
  .toc li {{ margin: 4px 0; }}
  .toc a {{ color: var(--fg); text-decoration: none; }}
  .toc a:hover {{ color: var(--fg-accent); }}
  .toc .tagline {{ color: var(--tagline); font-size: 13px; }}
  .paper {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px;
            margin-bottom: 12px; overflow: hidden; transition: background .15s; }}
  .paper:hover {{ background: var(--bg-card-hover); }}
  .paper-head {{ padding: 16px 20px; cursor: pointer; user-select: none;
                 display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: start; }}
  .rank {{ font-size: 13px; font-weight: 600; color: var(--score-fg); background: var(--score-bg);
           padding: 2px 8px; border-radius: 4px; min-width: 32px; text-align: center; }}
  .paper-title {{ font-size: 15px; font-weight: 600; margin: 0 0 4px; }}
  .paper-tagline {{ color: var(--tagline); font-size: 13.5px; margin: 0 0 6px; }}
  .paper-meta {{ font-size: 12px; color: var(--fg-muted); display: flex; gap: 12px; flex-wrap: wrap; }}
  .paper-meta a {{ color: var(--fg-accent); text-decoration: none; }}
  .paper-meta a:hover {{ text-decoration: underline; }}
  .paper-body {{ display: none; padding: 4px 24px 24px 56px;
                 border-top: 1px solid var(--border); background: var(--bg); }}
  .paper.open .paper-body {{ display: block; }}
  .paper.open .paper-head {{ background: var(--bg-card-hover); }}
  .paper-body h1, .paper-body h2 {{ font-size: 15px; margin: 18px 0 8px; }}
  .paper-body h2 {{ color: var(--fg-accent); }}
  .paper-body p {{ margin: 8px 0; font-size: 14px; }}
  .paper-body code {{ background: var(--bg-card); padding: 1px 4px; border-radius: 3px; font-size: 13px; }}
  .paper-body pre {{ background: var(--bg-card); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 12.5px; }}
  .paper-body table {{ border-collapse: collapse; margin: 12px 0; font-size: 13px; }}
  .paper-body th, .paper-body td {{ border: 1px solid var(--border); padding: 6px 10px; }}
  .paper-body strong {{ color: var(--fg); }}
  .actions {{ margin-top: 16px; display: flex; gap: 8px; flex-wrap: wrap; }}
  .actions a, .actions button {{ background: var(--bg-card); border: 1px solid var(--border);
    color: var(--fg); padding: 6px 12px; border-radius: 6px; font-size: 12.5px; cursor: pointer;
    text-decoration: none; }}
  .actions a:hover, .actions button:hover {{ background: var(--bg-card-hover); border-color: var(--fg-accent); }}
  .hidden {{ display: none !important; }}
  .empty {{ padding: 48px; text-align: center; color: var(--fg-muted); }}
</style>
</head>
<body>
<header>
  <h1>📚 Paper Radar</h1>
  <span class="meta">{date} · {count} papers</span>
  <input id="search" type="search" placeholder="搜标题 / 一句话定位…" autocomplete="off">
</header>
<main>
  <div class="toc">
    <h2>今日清单</h2>
    <ol>{toc_items}</ol>
  </div>
  <div id="papers">{paper_cards}</div>
  <div class="empty hidden" id="empty">没有匹配的论文。</div>
</main>
<script>
  // Lazy-render the markdown body the first time a paper opens (faster initial load)
  document.querySelectorAll('.paper-head').forEach(h => {{
    h.addEventListener('click', e => {{
      const card = h.parentElement;
      card.classList.toggle('open');
      const body = card.querySelector('.paper-body');
      if (card.classList.contains('open') && !body.dataset.rendered) {{
        const md = body.querySelector('script[type="text/markdown"]').textContent;
        body.querySelector('.md-target').innerHTML = marked.parse(md);
        body.dataset.rendered = '1';
      }}
    }});
  }});
  // Search filter
  const search = document.getElementById('search');
  const empty = document.getElementById('empty');
  search.addEventListener('input', () => {{
    const q = search.value.trim().toLowerCase();
    let any = false;
    document.querySelectorAll('.paper').forEach(card => {{
      const blob = (card.dataset.blob || '').toLowerCase();
      const hit = !q || blob.includes(q);
      card.classList.toggle('hidden', !hit);
      if (hit) any = true;
    }});
    empty.classList.toggle('hidden', any);
  }});
  // Copy "ask Claude" prompt to clipboard
  document.querySelectorAll('.btn-ask').forEach(b => {{
    b.addEventListener('click', e => {{
      e.preventDefault();
      const aid = b.dataset.aid;
      const prompt = `Use paper-radar:get_paper("${{aid}}") to load the deep-read, then answer:\n\n[your question here]`;
      navigator.clipboard.writeText(prompt).then(() => {{
        b.textContent = '✅ 已复制到剪贴板';
        setTimeout(() => b.textContent = '🤖 Ask Claude', 1800);
      }});
    }});
  }});
</script>
</body>
</html>
"""


def html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_html(date_str: str, papers: list[dict]) -> str:
    toc_items = []
    paper_cards = []
    for i, p in enumerate(papers, 1):
        title_h = html_escape(p["title"])
        tag_h = html_escape(p["tagline"]) if p["tagline"] else ""
        aid = p["arxiv_id"]
        url = p["arxiv_url"] or "#"
        anchor = f"p-{i}"

        toc_items.append(
            f'<li><a href="#{anchor}">{title_h}</a>'
            + (f' <span class="tagline">— {tag_h}</span>' if tag_h else "")
            + "</li>"
        )

        # Markdown body inserted as inline script (will be rendered client-side
        # via marked.js the first time the card is opened).
        md_inline = p["full_md"].replace("</script>", "<\\/script>")

        # Search blob = title + tagline + arxiv_id (used by JS filter)
        blob = f"{p['title']} {p['tagline']} {aid}".replace('"', "&quot;")

        paper_cards.append(
            f"""
<div class="paper" id="{anchor}" data-blob="{html_escape(blob)}">
  <div class="paper-head">
    <span class="rank">#{i}</span>
    <div>
      <p class="paper-title">{title_h}</p>
      {f'<p class="paper-tagline">{tag_h}</p>' if tag_h else ''}
      <p class="paper-meta">
        {f'<a href="{url}" target="_blank">{aid or url}</a>' if aid or url != '#' else ''}
        <span>· 点击展开 6-section</span>
      </p>
    </div>
  </div>
  <div class="paper-body">
    <div class="md-target"></div>
    <div class="actions">
      {f'<a href="{url}" target="_blank">📄 arXiv</a>' if url != '#' else ''}
      <button class="btn-ask" data-aid="{aid}">🤖 Ask Claude</button>
    </div>
    <script type="text/markdown">{md_inline}</script>
  </div>
</div>
"""
        )

    return HTML_TEMPLATE.format(
        date=html_escape(date_str),
        count=len(papers),
        toc_items="\n".join(toc_items),
        paper_cards="\n".join(paper_cards),
    )


def resolve_dir(arg: str | None) -> Path:
    if arg is None:
        return DEFAULT_DIGESTS / datetime.now().strftime("%Y-%m-%d")
    p = Path(arg)
    if p.is_absolute() and p.exists():
        return p
    return DEFAULT_DIGESTS / arg


def main():
    digest_dir = resolve_dir(sys.argv[1] if len(sys.argv) > 1 else None)
    if not digest_dir.is_dir():
        print(f"[viewer] digest dir not found: {digest_dir}", file=sys.stderr)
        sys.exit(1)

    ranked = digest_dir / "ranked.md"
    if not ranked.exists():
        print(f"[viewer] no ranked.md in {digest_dir}", file=sys.stderr)
        sys.exit(1)

    papers = parse_ranked_md(ranked.read_text(encoding="utf-8"))
    if not papers:
        print(f"[viewer] no papers parsed from {ranked}", file=sys.stderr)
        sys.exit(1)

    html = build_html(digest_dir.name, papers)
    out = digest_dir / "viewer.html"
    out.write_text(html, encoding="utf-8")
    print(f"[viewer] {len(papers)} papers → {out}")
    print(f"[viewer] open via: open {out}")
    print(f"[viewer] file URL:  file://{out}")


if __name__ == "__main__":
    main()
