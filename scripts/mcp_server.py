#!/usr/bin/env python3
"""
paper-radar MCP server
======================

Exposes the locally-curated paper digests as MCP tools, so any Claude Code
session (not just the one that triggered the daily routine) can pull a
specific paper's 6-section deep-read into context and continue the
conversation about it.

Tools:
  list_dates(limit=14)           — recent digest dates (YYYY-MM-DD)
  list_papers(date=None)         — top-10 ranked papers for a date
                                   (default: latest digest)
  get_paper(arxiv_id, date=None) — full 6-section markdown for a paper
                                   (resolves date if omitted)
  search_papers(query, limit=20) — fuzzy search across all digests'
                                   titles + 一句话定位 + arxiv_ids
  get_top5_annual(year=None)     — TOP 5 from the annual review

Designed to be called from Claude Code at any time so the user can read
papers anywhere (Obsidian, Feishu, file viewer) and switch back to Claude
Code to ask follow-up questions with the paper context auto-loaded.

Run as stdio MCP server:
    python3 mcp_server.py

Register via:
    claude mcp add paper-radar /path/to/.mcp-venv/bin/python /path/to/mcp_server.py
"""
import json
import os
import re
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ---- Paths -------------------------------------------------------------
DIGESTS_DIR = Path(
    os.environ.get(
        "PAPER_RADAR_DIGESTS_DIR",
        str(Path.home() / "code" / "paper_reading_walkstream" / "digests"),
    )
)

mcp = FastMCP("paper-radar")


# ---- Helpers ----------------------------------------------------------
def _list_daily_digests() -> list[str]:
    """Return YYYY-MM-DD dirs (descending), excluding annual-* / hidden."""
    if not DIGESTS_DIR.exists():
        return []
    out = []
    for p in DIGESTS_DIR.iterdir():
        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name):
            out.append(p.name)
    return sorted(out, reverse=True)


def _resolve_date(date: Optional[str]) -> Optional[str]:
    """If date is None, return the most recent digest date; else validate.

    Accepts both daily ('YYYY-MM-DD') and annual ('annual-YYYY-MM-DD') formats.
    """
    if date is None:
        ds = _list_daily_digests()
        return ds[0] if ds else None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return date
    if re.fullmatch(r"annual-\d{4}-\d{2}-\d{2}", date):
        if (DIGESTS_DIR / date).is_dir():
            return date
    return None


def _read_candidates(date: str) -> list[dict]:
    p = DIGESTS_DIR / date / "candidates.json"
    if not p.exists():
        return []
    try:
        return json.load(open(p))
    except Exception:
        return []


def _ranked_chunks(date: str) -> list[dict]:
    """Parse ranked.md into per-paper chunks. Returns list with
    {arxiv_id, title, tagline, full_md}.

    ranked.md uses one of two heading formats:
      A) wrapper:  `# [#N score=NN] <title>`     (newer, daily routine)
         followed by an inner `# <title>` heading inside the chunk.
      B) flat:     `# <title>`                   (older, manual runs)

    We pick (A) if any wrapper line exists, else fall back to (B).

    For annual-* digests, ranked.md doesn't exist — use annual_review.md
    instead (same heading conventions).
    """
    ranked = DIGESTS_DIR / date / "ranked.md"
    if not ranked.exists():
        # Annual digests have annual_review.md instead of ranked.md
        annual_review = DIGESTS_DIR / date / "annual_review.md"
        if annual_review.exists():
            ranked = annual_review
        else:
            return []
    text = ranked.read_text(encoding="utf-8")
    has_wrapper = bool(re.search(r"(?m)^# \[#\d+\s+score=", text))
    if has_wrapper:
        # Split only on wrapper headings — inner `# <title>` becomes part of body.
        parts = re.split(r"(?m)^# \[#\d+\s+score=[^\]]+\]\s*", text)[1:]
    else:
        parts = re.split(r"(?m)^# (?!Paper Radar)", text)[1:]
    out = []
    for chunk in parts:
        lines = chunk.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        body = re.sub(r"\n+---\s*$", "", body).strip()

        # Try multiple arxiv-link formats: `**arxiv**: URL`, `**arXiv:** URL`,
        # `arxiv: URL`, or just a bare arxiv URL anywhere in body.
        arxiv_url = ""
        for pat in (
            r"\*\*arxiv:?\*\*:?\s*(\S+)",
            r"(?m)^\s*arxiv:?\s*(\S+)",
            r"(https?://arxiv\.org/abs/\d{4}\.\d{4,5}\S*)",
        ):
            m = re.search(pat, body, flags=re.IGNORECASE)
            if m:
                arxiv_url = m.group(1)
                break
        # arxiv_id can come from URL OR from a bare "arXiv: 2605.04649" line
        aid = ""
        if arxiv_url:
            am = re.search(r"(\d{4}\.\d{4,5})", arxiv_url)
            if am:
                aid = am.group(1)
        if not aid:
            # search whole body for `arxiv 2605.04649` style
            am = re.search(r"\barxiv:?\s*(\d{4}\.\d{4,5})", body, flags=re.IGNORECASE)
            if am:
                aid = am.group(1)

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
                "arxiv_id": aid,
                "title": title,
                "arxiv_url": arxiv_url,
                "tagline": tagline,
                "full_md": f"# {title}\n\n{body}",
            }
        )
    return out


def _find_deepread(arxiv_id: str, date: Optional[str] = None) -> Optional[Path]:
    """Locate the per-paper deep-read .md anywhere under digests/."""
    if date:
        p = DIGESTS_DIR / date / "deepreads" / f"{arxiv_id}.md"
        if p.exists():
            return p
    # search across all daily + annual digests
    for d in DIGESTS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = d / "deepreads" / f"{arxiv_id}.md"
        if p.exists():
            return p
    return None


# ---- Tools ------------------------------------------------------------
@mcp.tool()
def list_dates(limit: int = 14) -> dict:
    """List the most recent digest dates available (YYYY-MM-DD).

    Args:
        limit: maximum number of dates to return.
    """
    dates = _list_daily_digests()[:limit]
    return {
        "digests_dir": str(DIGESTS_DIR),
        "dates": dates,
        "annual_digests": sorted(
            [p.name for p in DIGESTS_DIR.iterdir()
             if p.is_dir() and p.name.startswith("annual-")],
            reverse=True,
        ),
    }


@mcp.tool()
def list_papers(date: Optional[str] = None) -> dict:
    """List the top-10 ranked papers for a given digest date.

    Args:
        date: YYYY-MM-DD. Default = most recent digest.
    Returns:
        {date, papers: [{arxiv_id, title, arxiv_url, tagline}, ...]}
    """
    d = _resolve_date(date)
    if d is None:
        return {"error": "no digests found", "digests_dir": str(DIGESTS_DIR)}

    chunks = _ranked_chunks(d)
    papers_meta = []
    for i, c in enumerate(chunks, 1):
        papers_meta.append(
            {
                "rank": i,
                "arxiv_id": c["arxiv_id"],
                "title": c["title"],
                "arxiv_url": c["arxiv_url"],
                "tagline": c["tagline"],
            }
        )
    return {
        "date": d,
        "count": len(papers_meta),
        "papers": papers_meta,
        "note": "Use get_paper(arxiv_id) to load a paper's full 6-section deep-read.",
    }


@mcp.tool()
def get_paper(arxiv_id: str, date: Optional[str] = None) -> dict:
    """Return the full 6-section deep-read markdown for one paper.

    Use this to pull a paper into context before asking follow-up
    questions about it. The returned `full_md` is the same content
    that gets pushed to Feishu as a detail card.

    Args:
        arxiv_id: e.g. "2509.09674". Strip any version suffix.
        date: optional digest date. If omitted, searches all digests.
    Returns:
        {arxiv_id, title, full_md, source_path, date}
    """
    arxiv_id = arxiv_id.strip().split("v")[0]

    # First try ranked.md chunks (faster, has fancy headers preserved)
    d = _resolve_date(date)
    if d:
        for c in _ranked_chunks(d):
            if c["arxiv_id"] == arxiv_id:
                return {
                    "arxiv_id": arxiv_id,
                    "title": c["title"],
                    "arxiv_url": c["arxiv_url"],
                    "full_md": c["full_md"],
                    "source": "ranked.md",
                    "date": d,
                }

    # Fall back to per-paper deepreads/<id>.md
    p = _find_deepread(arxiv_id, date)
    if p:
        return {
            "arxiv_id": arxiv_id,
            "title": "",
            "full_md": p.read_text(encoding="utf-8"),
            "source": str(p),
            "date": p.parent.parent.name,
        }
    return {
        "error": f"paper {arxiv_id} not found in any digest",
        "searched": str(DIGESTS_DIR),
    }


@mcp.tool()
def search_papers(query: str, limit: int = 20) -> dict:
    """Fuzzy search papers across all digests by title / tagline / arxiv_id.

    Case-insensitive substring match. Useful when the user remembers
    "that paper about world model rewards" but not the arxiv_id.

    Args:
        query: search string (case-insensitive).
        limit: max hits.
    Returns:
        {query, hits: [{date, arxiv_id, title, tagline, score}]}
    """
    q = query.lower().strip()
    if not q:
        return {"error": "empty query"}

    hits = []
    for date in _list_daily_digests():
        for c in _ranked_chunks(date):
            blob = (
                c["title"].lower() + " " +
                c["tagline"].lower() + " " +
                c["arxiv_id"]
            )
            if q in blob:
                # Trivial relevance: title hit > tagline hit > id hit
                if q in c["title"].lower():
                    score = 3
                elif q in c["tagline"].lower():
                    score = 2
                else:
                    score = 1
                hits.append(
                    {
                        "date": date,
                        "arxiv_id": c["arxiv_id"],
                        "title": c["title"],
                        "tagline": c["tagline"],
                        "score": score,
                    }
                )
    # Also scan annual-* candidates (they have a candidates.json with all 250)
    for d in DIGESTS_DIR.iterdir():
        if not d.is_dir() or not d.name.startswith("annual-"):
            continue
        cand = d / "candidates.json"
        if not cand.exists():
            continue
        try:
            for p in json.load(open(cand)):
                blob = (p.get("title", "") + " " + p.get("abstract", "")).lower()
                if q in blob:
                    hits.append(
                        {
                            "date": d.name,
                            "arxiv_id": p.get("arxiv_id", ""),
                            "title": p.get("title", ""),
                            "tagline": p.get("abstract", "")[:200],
                            "score": 1,
                            "from": "annual-candidates",
                        }
                    )
        except Exception:
            pass

    hits.sort(key=lambda h: (-h["score"], h["date"]), reverse=False)
    return {"query": query, "count": len(hits), "hits": hits[:limit]}


@mcp.tool()
def get_top5_annual(year_tag: Optional[str] = None) -> dict:
    """Return the TOP 5 highlight markdown from the annual review.

    Args:
        year_tag: e.g. "annual-2026-05-07". Default = most recent annual digest.
    Returns:
        {tag, top5_md}
    """
    annuals = sorted(
        [p.name for p in DIGESTS_DIR.iterdir()
         if p.is_dir() and p.name.startswith("annual-")],
        reverse=True,
    )
    if not annuals:
        return {"error": "no annual digests found"}
    tag = year_tag or annuals[0]
    p = DIGESTS_DIR / tag / "top5.md"
    if not p.exists():
        return {"error": f"{tag}/top5.md not found"}
    return {"tag": tag, "top5_md": p.read_text(encoding="utf-8")}


@mcp.tool()
def get_candidates(date: Optional[str] = None, top_n: int = 30) -> dict:
    """Return the top-N candidate papers from Stage 1 (before deep-read).

    Useful when ranked.md isn't available yet (e.g. mid-routine), or
    when you want to see what was filtered out vs what made the top-10.

    Args:
        date: digest date. Default = most recent.
        top_n: how many candidates to return (sorted by score desc).
    """
    d = _resolve_date(date)
    if d is None:
        return {"error": "no digests"}
    cands = _read_candidates(d)
    cands_sorted = sorted(cands, key=lambda p: -p.get("score", 0))[:top_n]
    return {
        "date": d,
        "total_candidates": len(cands),
        "returned": len(cands_sorted),
        "candidates": [
            {
                "arxiv_id": c["arxiv_id"],
                "score": c.get("score", 0),
                "title": c.get("title", ""),
                "topic_labels": c.get("score_breakdown", {}).get("topic_labels", []),
                "hf_upvotes": c.get("score_breakdown", {}).get("hf_upvotes", 0),
                "abs_url": c.get("abs_url", ""),
            }
            for c in cands_sorted
        ],
    }


# ---- Cowork bridge compat shim ---------------------------------------
# The Cowork (Claude Desktop) bridge forwards tool names with the
# `mcp__<server>__<tool>` prefix that Claude Code's tool registry uses,
# but does NOT strip the prefix before sending to the stdio MCP server.
# Net effect: the server receives `mcp__paper-radar__list_dates` and
# replies `Unknown tool` (only `list_dates` is registered).
#
# Until the bridge is fixed upstream, register every public tool a
# second time under its prefixed name as a thin pass-through.
# Repro details: see commit message + chat transcript 2026-05-08.
_PREFIX = "mcp__paper-radar__"


@mcp.tool(name=_PREFIX + "list_dates")
def _aliased_list_dates(limit: int = 14) -> dict:
    """[Cowork-bridge alias] Same as list_dates."""
    return list_dates(limit)


@mcp.tool(name=_PREFIX + "list_papers")
def _aliased_list_papers(date: Optional[str] = None) -> dict:
    """[Cowork-bridge alias] Same as list_papers."""
    return list_papers(date)


@mcp.tool(name=_PREFIX + "get_paper")
def _aliased_get_paper(arxiv_id: str, date: Optional[str] = None) -> dict:
    """[Cowork-bridge alias] Same as get_paper."""
    return get_paper(arxiv_id, date)


@mcp.tool(name=_PREFIX + "search_papers")
def _aliased_search_papers(query: str, limit: int = 20) -> dict:
    """[Cowork-bridge alias] Same as search_papers."""
    return search_papers(query, limit)


@mcp.tool(name=_PREFIX + "get_top5_annual")
def _aliased_get_top5_annual(year_tag: Optional[str] = None) -> dict:
    """[Cowork-bridge alias] Same as get_top5_annual."""
    return get_top5_annual(year_tag)


@mcp.tool(name=_PREFIX + "get_candidates")
def _aliased_get_candidates(date: Optional[str] = None, top_n: int = 30) -> dict:
    """[Cowork-bridge alias] Same as get_candidates."""
    return get_candidates(date, top_n)


if __name__ == "__main__":
    mcp.run()
