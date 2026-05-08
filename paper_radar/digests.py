"""Single source of truth for reading paper-radar digests.

Both the FastAPI web server (paper_radar.api) and the MCP server
(scripts/mcp_server.py) import from here. Pure data-access library;
no FastAPI / MCP / Anthropic dependency.

All public functions return plain dicts (JSON-serializable) so the
two transport layers can pass them through unchanged.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

# ---- Paths -----------------------------------------------------------
DIGESTS_DIR = Path(
    os.environ.get(
        "PAPER_RADAR_DIGESTS_DIR",
        str(Path.home() / "code" / "paper_reading_walkstream" / "digests"),
    )
)


# ---- Internal helpers ------------------------------------------------
def _list_daily_digests() -> list[str]:
    """Return YYYY-MM-DD dirs (descending), excluding annual-* / hidden."""
    if not DIGESTS_DIR.exists():
        return []
    out = []
    for p in DIGESTS_DIR.iterdir():
        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name):
            out.append(p.name)
    return sorted(out, reverse=True)


def _list_annual_digests() -> list[str]:
    if not DIGESTS_DIR.exists():
        return []
    return sorted(
        [p.name for p in DIGESTS_DIR.iterdir()
         if p.is_dir() and p.name.startswith("annual-")],
        reverse=True,
    )


def _resolve_date(date: Optional[str]) -> Optional[str]:
    """If date is None, return the most recent digest date; else validate.

    Accepts both daily ('YYYY-MM-DD') and annual ('annual-YYYY-MM-DD').
    """
    if date is None:
        ds = _list_daily_digests()
        return ds[0] if ds else None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        if (DIGESTS_DIR / date).is_dir():
            return date
        return None
    if re.fullmatch(r"annual-\d{4}-\d{2}-\d{2}", date):
        if (DIGESTS_DIR / date).is_dir():
            return date
    return None


def _read_candidates_json(date: str) -> list[dict]:
    p = DIGESTS_DIR / date / "candidates.json"
    if not p.exists():
        return []
    try:
        return json.load(open(p))
    except Exception:
        return []


def _ranked_chunks(date: str) -> list[dict]:
    """Parse ranked.md (or annual_review.md for annual digests) into
    per-paper chunks. Returns list with {arxiv_id, title, arxiv_url,
    tagline, full_md}.

    ranked.md uses one of two heading formats:
      A) wrapper:  `# [#N score=NN] <title>`
      B) flat:     `# <title>`
    """
    ranked = DIGESTS_DIR / date / "ranked.md"
    if not ranked.exists():
        annual_review = DIGESTS_DIR / date / "annual_review.md"
        if annual_review.exists():
            ranked = annual_review
        else:
            return []
    text = ranked.read_text(encoding="utf-8")
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

        # Multiple arxiv-link formats
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
        aid = ""
        if arxiv_url:
            am = re.search(r"(\d{4}\.\d{4,5})", arxiv_url)
            if am:
                aid = am.group(1)
        if not aid:
            am = re.search(r"\barxiv:?\s*(\d{4}\.\d{4,5})",
                           body, flags=re.IGNORECASE)
            if am:
                aid = am.group(1)
        if arxiv_url and not arxiv_url.startswith("http") and aid:
            arxiv_url = f"https://arxiv.org/abs/{aid}"

        # Tagline = first paragraph in section 1
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
    if not DIGESTS_DIR.exists():
        return None
    for d in DIGESTS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = d / "deepreads" / f"{arxiv_id}.md"
        if p.exists():
            return p
    return None


# ---- Public API ------------------------------------------------------
def list_dates(limit: int = 14) -> dict:
    """List recent digest dates (daily + annual)."""
    dates = _list_daily_digests()[:limit]
    return {
        "digests_dir": str(DIGESTS_DIR),
        "dates": dates,
        "annual_digests": _list_annual_digests(),
    }


def list_papers(date: Optional[str] = None) -> dict:
    """List ranked papers for a digest date (default: latest daily)."""
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
    }


def get_paper(arxiv_id: str, date: Optional[str] = None) -> dict:
    """Return full 6-section deep-read markdown for one paper."""
    arxiv_id = arxiv_id.strip().split("v")[0]

    d = _resolve_date(date) if date else None
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


def search_papers(query: str, limit: int = 20) -> dict:
    """Fuzzy substring search across all digests + annual candidates."""
    q = (query or "").lower().strip()
    if not q:
        return {"query": query, "count": 0, "hits": []}

    hits = []
    for date in _list_daily_digests() + _list_annual_digests():
        for c in _ranked_chunks(date):
            blob = (
                c["title"].lower() + " " +
                c["tagline"].lower() + " " +
                c["arxiv_id"]
            )
            if q in blob:
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
    # Also scan annual-* candidates (250 papers per annual)
    for d in _list_annual_digests():
        for p in _read_candidates_json(d):
            blob = (p.get("title", "") + " " +
                    p.get("abstract", "")).lower()
            if q in blob:
                hits.append(
                    {
                        "date": d,
                        "arxiv_id": p.get("arxiv_id", ""),
                        "title": p.get("title", ""),
                        "tagline": p.get("abstract", "")[:200],
                        "score": 1,
                        "from": "annual-candidates",
                    }
                )

    hits.sort(key=lambda h: -h["score"])
    return {"query": query, "count": len(hits), "hits": hits[:limit]}


def get_top5_annual(year_tag: Optional[str] = None) -> dict:
    """Return top5.md from an annual digest."""
    annuals = _list_annual_digests()
    if not annuals:
        return {"error": "no annual digests found"}
    tag = year_tag or annuals[0]
    p = DIGESTS_DIR / tag / "top5.md"
    if not p.exists():
        return {"error": f"{tag}/top5.md not found"}
    return {"tag": tag, "top5_md": p.read_text(encoding="utf-8")}


def get_candidates(date: Optional[str] = None, top_n: int = 30) -> dict:
    """Return Stage-1 candidates (pre-deep-read) for a digest."""
    d = _resolve_date(date)
    if d is None:
        return {"error": "no digests"}
    cands = _read_candidates_json(d)
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
