"""paper-radar data store — the single source of truth for reading digests.

Clean rewrite. This module only READS the on-disk layout produced by the
daily pipeline:

    digests/
      YYYY-MM-DD/              daily digest
        candidates.json        stage-1 scored candidates
        ranked.md              stage-3 assembly ("# [#N score=NN] <title>" chunks)
        deepreads/<aid>.md     stage-2 per-paper deep-reads (the ORIGINALS)
      annual-YYYY-MM-DD/       annual digest (annual_review.md instead of ranked.md)
      ondemand/deepreads/      pid-keyed on-demand reads + .json sidecars
      weekly/<date>.md         weekly syntheses

pid model — one opaque id opens ANY paper:
    2606.12345                 arxiv id (doubles as the redis signal key)
    r_<date>_<sha1(title)[:8]> ranked paper that has no arxiv id
    od_<jobhash>               on-demand read of a non-arxiv URL
    wk_<YYYY-MM-DD>            weekly synthesis

Consumers: paper_radar.api (REST), paper_radar.search (FTS corpus),
scripts/mcp_server.py (MCP tools), and the root worker (ondemand.py).
Pure stdlib. Every public function returns JSON-serializable dicts.

Performance: ranked.md parsing is cached per date, keyed on the mtimes of
ranked.md/annual_review.md and of the date's deepreads (the shown body is
decoupled to the stage-2 original, so those files are part of the cache key).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Optional

DIGESTS_DIR = Path(
    os.environ.get(
        "PAPER_RADAR_DIGESTS_DIR",
        str(Path.home() / "code" / "paper_reading_walkstream" / "digests"),
    )
)

_DAILY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ANNUAL_RE = re.compile(r"annual-\d{4}-\d{2}-\d{2}")
_ARXIV_RE = re.compile(r"\d{4}\.\d{4,5}")


# ---------------------------------------------------------------------------
# Date listing
# ---------------------------------------------------------------------------
def _dirs_matching(rx: re.Pattern) -> list[str]:
    if not DIGESTS_DIR.exists():
        return []
    return sorted(
        (p.name for p in DIGESTS_DIR.iterdir() if p.is_dir() and rx.fullmatch(p.name)),
        reverse=True,
    )


def daily_dates() -> list[str]:
    return _dirs_matching(_DAILY_RE)


def annual_dates() -> list[str]:
    return _dirs_matching(_ANNUAL_RE)


def all_dates() -> list[str]:
    """Daily (newest first) then annual — the scan order for pid lookups."""
    return daily_dates() + annual_dates()


def _resolve_date(date: Optional[str]) -> Optional[str]:
    """None → latest daily; else validate that the digest dir exists."""
    if date is None:
        ds = daily_dates()
        return ds[0] if ds else None
    if (_DAILY_RE.fullmatch(date) or _ANNUAL_RE.fullmatch(date)) \
            and (DIGESTS_DIR / date).is_dir():
        return date
    return None


# ---------------------------------------------------------------------------
# Candidates (stage-1 output)
# ---------------------------------------------------------------------------
def read_candidates(date: str) -> list[dict]:
    p = DIGESTS_DIR / date / "candidates.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


_TIER_RE = re.compile(r"^[SABC]:")


def clean_labels(labels) -> list[str]:
    """topic_labels → display tags: drop control markers, strip tier prefix, dedupe."""
    out, seen = [], set()
    for raw in labels or []:
        if not raw or raw == "DROP-NEG":
            continue
        t = _TIER_RE.sub("", raw).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def _candidate_tags(date: str) -> dict:
    """arxiv_id → cleaned display tags for one date."""
    idx = {}
    for c in read_candidates(date):
        aid = c.get("arxiv_id")
        if aid:
            idx[aid] = clean_labels((c.get("score_breakdown") or {}).get("topic_labels", []))
    return idx


# ---------------------------------------------------------------------------
# pid model
# ---------------------------------------------------------------------------
_R_PID_RE = re.compile(
    r"r_(annual-\d{4}-\d{2}-\d{2}|\d{4}-\d{2}-\d{2})_([0-9a-f]{8}(?:_\d+)?)"
)
_WK_PID_RE = re.compile(r"wk_(\d{4}-\d{2}-\d{2})")


def parse_pid(pid: str):
    """→ (kind, payload). kind ∈ {ondemand, weekly, ranked, arxiv}.

    Malformed wk_/r_ ids fall through to the arxiv branch, where they will
    simply not match anything → a clean not-found.
    """
    pid = (pid or "").strip()
    if pid.startswith("od_"):
        return ("ondemand", pid)
    if pid.startswith("wk_"):
        m = _WK_PID_RE.fullmatch(pid)
        if m:
            return ("weekly", m.group(1))
        return ("arxiv", pid)
    m = _R_PID_RE.fullmatch(pid)
    if m:
        return ("ranked", (m.group(1), m.group(2)))
    return ("arxiv", pid)


def _synthetic_pid(date: str, title: str) -> str:
    return f"r_{date}_{hashlib.sha1((title or '').strip().encode()).hexdigest()[:8]}"


def _pid_is_unsafe(pid: str) -> bool:
    """pid feeds filesystem paths and is reachable unauthenticated — reject
    anything that could escape the digests dir before it touches a path."""
    return (not pid) or ("/" in pid) or ("\\" in pid) or (".." in pid) or ("\x00" in pid)


# ---------------------------------------------------------------------------
# ranked.md parsing (cached per date)
# ---------------------------------------------------------------------------
_chunks_lock = threading.Lock()
_chunks_cache: dict[str, tuple] = {}   # date → (key, chunks)

_WRAPPER_SPLIT = re.compile(r"(?m)^# \[#\d+\s+score=[^\]]+\]\s*")
_FLAT_SPLIT = re.compile(r"(?m)^# (?!Paper Radar)")
_ARXIV_URL_PATTERNS = (
    # main-figure line (prepended by add_figure): the paper's OWN id, always at
    # the top of the body — try FIRST so a cited paper's /abs/ link can't win.
    re.compile(r"!\[[^\]]*\]\((https?://arxiv\.org/(?:html|abs|pdf)/\d{4}\.\d{4,5}\S*)\)"),
    re.compile(r"\*\*arxiv:?\*\*:?\s*(\S+)", re.IGNORECASE),
    re.compile(r"(?m)^\s*arxiv:?\s*(\S+)", re.IGNORECASE),
    # any arxiv URL — /html/ and /pdf/ too, not just /abs/ (fixes r_ misclassification)
    re.compile(r"(https?://arxiv\.org/(?:abs|pdf|html)/\d{4}\.\d{4,5}\S*)"),
)
_SEC1_RE = re.compile(r"##\s*1\.\s*[^\n]*\n(.*?)(?=\n##\s|\n---|\Z)", re.DOTALL)
_LEADING_H1_RE = re.compile(r"^#\s+[^\n]*\n+")


def _safe_mtime(p: Path) -> int:
    try:
        return int(p.stat().st_mtime)
    except OSError:
        return 0


def _float_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _deepreads_signature(date: str) -> tuple:
    """(count, max mtime) of a date's deepreads — part of the cache key because
    the shown body prefers the stage-2 original over ranked.md's reproduction."""
    d = DIGESTS_DIR / date / "deepreads"
    if not d.is_dir():
        return (0, 0)
    n, mx = 0, 0
    try:
        with os.scandir(d) as it:
            for e in it:
                if e.name.endswith(".md"):
                    n += 1
                    try:
                        mx = max(mx, int(e.stat().st_mtime))
                    except OSError:
                        pass
    except OSError:
        pass
    return (n, mx)


def _source_file(date: str) -> Optional[Path]:
    ranked = DIGESTS_DIR / date / "ranked.md"
    if ranked.exists():
        return ranked
    annual = DIGESTS_DIR / date / "annual_review.md"
    if annual.exists():
        return annual
    return None


def _extract_arxiv(body: str) -> tuple[str, str]:
    """→ (arxiv_url, arxiv_id) pulled from a chunk body ('' when absent)."""
    url = ""
    for pat in _ARXIV_URL_PATTERNS:
        m = pat.search(body)
        if m:
            url = m.group(1)
            break
    aid = ""
    if url:
        m = _ARXIV_RE.search(url)
        if m:
            aid = m.group(0)
    if not aid:
        m = re.search(r"\barxiv:?\s*(\d{4}\.\d{4,5})", body, re.IGNORECASE)
        if m:
            aid = m.group(1)
    # The first match may be the main figure's /html/<id>/figures/... URL.
    # It is useful for identifying the owning paper, but must never become the
    # user-facing "arXiv" link.  Canonicalise every recognised source form
    # (/html/, /pdf/, /abs/, bare id) to the paper abstract page.
    if aid:
        url = f"https://arxiv.org/abs/{aid}"
    return url, aid


def _first_para_of_sec1(body: str) -> str:
    m = _SEC1_RE.search(body)
    if not m:
        return ""
    for para in re.split(r"\n\s*\n", m.group(1).strip()):
        para = para.strip()
        if para:
            para = re.sub(r"^[>*\s]+", "", para)
            return re.sub(r"\s+", " ", para)[:240]
    return ""


def _parse_chunks(date: str) -> list[dict]:
    src = _source_file(date)
    if src is None:
        return []
    text = src.read_text(encoding="utf-8")
    if re.search(r"(?m)^# \[#\d+\s+score=", text):
        raw = _WRAPPER_SPLIT.split(text)[1:]
    else:
        raw = _FLAT_SPLIT.split(text)[1:]

    out: list[dict] = []
    seen: dict[str, int] = {}
    for chunk in raw:
        lines = chunk.splitlines()
        title = lines[0].strip() if lines else ""
        body = "\n".join(lines[1:]).strip()
        body = re.sub(r"\n+---\s*$", "", body).strip()

        arxiv_url, aid = _extract_arxiv(body)

        # Body decoupling: prefer the stage-2 deep-read ORIGINAL over the
        # stage-3 reproduction in ranked.md, so the prose shown is the
        # deep-read model's own. Fall back for no-arxiv (r_) papers.
        body_src = body
        if aid:
            p = DIGESTS_DIR / date / "deepreads" / f"{aid}.md"
            if p.exists():
                try:
                    dr = p.read_text(encoding="utf-8").strip()
                    if dr:
                        # drop the deepread's own leading "# title" — the
                        # canonical ranked title is re-added below
                        body_src = _LEADING_H1_RE.sub("", dr, count=1)
                except Exception:
                    pass

        pid = aid or _synthetic_pid(date, title)
        if not aid:   # only synthetic pids can collide; suffix deterministically
            n = seen.get(pid, 0)
            seen[pid] = n + 1
            if n:
                pid = f"{pid}_{n}"

        out.append({
            "pid": pid,
            "arxiv_id": aid,
            "title": title,
            "arxiv_url": arxiv_url,
            "tagline": _first_para_of_sec1(body_src),
            "full_md": f"# {title}\n\n{body_src}",
        })
    return out


def ranked_chunks(date: str) -> list[dict]:
    """Parsed chunks for one digest date, cached on (source, deepreads) mtimes."""
    src = _source_file(date)
    key = (_safe_mtime(src) if src else 0, _deepreads_signature(date))
    with _chunks_lock:
        hit = _chunks_cache.get(date)
        if hit and hit[0] == key:
            return hit[1]
    chunks = _parse_chunks(date)
    with _chunks_lock:
        _chunks_cache[date] = (key, chunks)
    return chunks


def _attach_review(res: dict, review_path: Path) -> dict:
    """Attach the gpt audit sidecar ({score, verdict, issues, model, ts}) if present,
    plus `degraded` — True when a sibling <stem>.degraded marker exists, i.e. this
    deep-read came from the browse fallback (full paper text was NOT read)."""
    try:
        if review_path.exists():
            res["review"] = json.loads(review_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    try:
        marker = review_path.with_name(review_path.name.replace(".review.json", ".degraded"))
        res["degraded"] = marker.exists()
    except Exception:
        pass
    return res


def _find_deepread(aid: str, date: Optional[str] = None) -> Optional[Path]:
    """Locate a per-paper deep-read .md anywhere under digests/."""
    if date:
        p = DIGESTS_DIR / date / "deepreads" / f"{aid}.md"
        if p.exists():
            return p
    if not DIGESTS_DIR.exists():
        return None
    for d in all_dates():
        p = DIGESTS_DIR / d / "deepreads" / f"{aid}.md"
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# Weekly + on-demand stores
# ---------------------------------------------------------------------------
_OND_DIR = None  # computed lazily so env overrides in tests still work


def _ondemand_dir() -> Path:
    return DIGESTS_DIR / "ondemand" / "deepreads"


def _weekly_dir() -> Path:
    return DIGESTS_DIR / "weekly"


def _extract_title(md: str) -> str:
    m = re.match(r"^#\s+([^\n]+)", md or "")
    return m.group(1).strip() if m else ""


def _first_para(md: str) -> str:
    body = re.sub(r"^#\s+[^\n]+\n", "", md or "", count=1)
    for p in re.split(r"\n\s*\n", body.strip()):
        p = re.sub(r"^[>*#\s-]+", "", p.strip())
        p = re.sub(r"\s+", " ", p)
        if len(p) > 10:
            return p[:240]
    return ""


def _read_sidecar(pid: str) -> dict:
    sc = _ondemand_dir() / f"{pid}.json"
    if not sc.exists():
        return {}
    try:
        return json.loads(sc.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get_ondemand(pid: str) -> dict:
    md_p = _ondemand_dir() / f"{pid}.md"
    if not md_p.exists():
        return _err(pid)
    md = md_p.read_text(encoding="utf-8")
    meta = _read_sidecar(pid)
    return _attach_review({
        "pid": pid,
        "arxiv_id": meta.get("arxiv_id") or "",
        "title": meta.get("title") or _extract_title(md),
        "arxiv_url": meta.get("url") or "",
        "full_md": md,
        "source": str(md_p),
        "date": "ondemand",
    }, _ondemand_dir() / f"{pid}.review.json")


def list_ondemand() -> dict:
    """On-demand deep-reads, newest first (for the browse view)."""
    base = _ondemand_dir()
    if not base.exists():
        return {"count": 0, "papers": []}
    papers = []
    for f in base.glob("*.md"):
        pid = f.stem
        if pid.startswith("."):        # worker scratch files
            continue
        meta = _read_sidecar(pid)
        is_arxiv = not pid.startswith("od_")
        try:
            md = f.read_text(encoding="utf-8")
        except Exception:
            continue
        papers.append({
            "pid": pid,
            "arxiv_id": meta.get("arxiv_id") or (pid if is_arxiv else ""),
            "title": meta.get("title") or _extract_title(md) or pid,
            "tagline": meta.get("tagline") or "",
            "tags": meta.get("tags") or ["on-demand"],
            "arxiv_url": meta.get("url") or (f"https://arxiv.org/abs/{pid}" if is_arxiv else ""),
            "date": "ondemand",
            "ts": meta.get("ts") or _float_mtime(f),
        })
    papers.sort(key=lambda p: -p["ts"])
    return {"count": len(papers), "papers": papers}


def _get_weekly(date: str) -> dict:
    p = _weekly_dir() / f"{date}.md"
    if not p.exists():
        return _err("wk_" + date)
    md = p.read_text(encoding="utf-8")
    return {
        "pid": "wk_" + date,
        "arxiv_id": "",
        "title": _extract_title(md) or f"本周综述 {date}",
        "arxiv_url": "",
        "full_md": md,
        "source": str(p),
        "date": "weekly",
    }


def list_weekly() -> dict:
    base = _weekly_dir()
    if not base.exists():
        return {"count": 0, "papers": []}
    out = []
    for f in base.glob("*.md"):
        try:
            md = f.read_text(encoding="utf-8")
        except Exception:
            continue
        out.append({
            "pid": "wk_" + f.stem,
            "arxiv_id": "",
            "title": _extract_title(md) or f"本周综述 {f.stem}",
            "tagline": _first_para(md),
            "tags": ["weekly"],
            "arxiv_url": "",
            "date": "weekly",
            "ts": _float_mtime(f),
        })
    out.sort(key=lambda p: -p["ts"])
    return {"count": len(out), "papers": out}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _err(pid: str) -> dict:
    return {"error": f"paper {pid} not found", "searched": str(DIGESTS_DIR)}


def list_dates(limit: int = 14) -> dict:
    return {
        "digests_dir": str(DIGESTS_DIR),
        "dates": daily_dates()[:limit],
        "annual_digests": annual_dates(),
    }


def list_papers(date: Optional[str] = None) -> dict:
    d = _resolve_date(date)
    if d is None:
        return {"error": "no digests found", "digests_dir": str(DIGESTS_DIR)}
    tags_idx = _candidate_tags(d)
    papers = [
        {
            "rank": i,
            "pid": c["pid"],
            "arxiv_id": c["arxiv_id"],
            "title": c["title"],
            "arxiv_url": c["arxiv_url"],
            "tagline": c["tagline"],
            "tags": tags_idx.get(c["arxiv_id"], []),
        }
        for i, c in enumerate(ranked_chunks(d), 1)
    ]
    return {"date": d, "count": len(papers), "papers": papers}


def _ranked_result(c: dict, date: str) -> dict:
    res = {
        "pid": c["pid"], "arxiv_id": c["arxiv_id"], "title": c["title"],
        "arxiv_url": c["arxiv_url"], "full_md": c["full_md"],
        "source": "ranked.md", "date": date,
    }
    if c["arxiv_id"]:
        _attach_review(res, DIGESTS_DIR / date / "deepreads" / f"{c['arxiv_id']}.review.json")
    return res


def get_paper(pid: str, date: Optional[str] = None) -> dict:
    """Full deep-read markdown for one paper, by pid (any kind)."""
    pid = (pid or "").strip()
    if _pid_is_unsafe(pid):
        return _err(pid)
    kind, payload = parse_pid(pid)

    if kind == "ondemand":
        return _get_ondemand(pid)

    if kind == "weekly":
        return _get_weekly(payload)

    if kind == "ranked":
        d, _slug = payload
        if (DIGESTS_DIR / d).is_dir():
            for c in ranked_chunks(d):
                if c["pid"] == pid:
                    return _ranked_result(c, d)
        return _err(pid)

    # arxiv id (optionally with version suffix)
    aid = re.sub(r"v\d+$", "", pid)
    if not aid:
        return _err(pid)
    dates = [_resolve_date(date)] if date else all_dates()
    for d in dates:
        if not d:
            continue
        for c in ranked_chunks(d):
            if c["arxiv_id"] == aid:
                return _ranked_result(c, d)
    # not in any ranked digest — an on-demand read keyed by arxiv id?
    od = _ondemand_dir() / f"{aid}.md"
    if od.exists():
        return _get_ondemand(aid)
    # bare deepread (generated but not ranked)
    p = _find_deepread(aid, date)
    if p:
        md = p.read_text(encoding="utf-8")
        return _attach_review({
            "pid": aid, "arxiv_id": aid, "title": _extract_title(md),
            "arxiv_url": f"https://arxiv.org/abs/{aid}",
            "full_md": md, "source": str(p), "date": p.parent.parent.name,
        }, p.with_suffix(".review.json"))
    return _err(pid)


def search_papers(query: str, limit: int = 20) -> dict:
    """Naive substring search (kept for the MCP server; the web app uses
    paper_radar.search — FTS5 + fuzzy — instead)."""
    q = (query or "").lower().strip()
    if not q:
        return {"query": query, "count": 0, "hits": []}
    hits = []
    for date in all_dates():
        for c in ranked_chunks(date):
            blob = f"{c['title'].lower()} {c['tagline'].lower()} {c['arxiv_id']}"
            if q in blob:
                score = 3 if q in c["title"].lower() else 2 if q in c["tagline"].lower() else 1
                hits.append({
                    "date": date, "arxiv_id": c["arxiv_id"], "title": c["title"],
                    "tagline": c["tagline"], "score": score,
                })
    for d in annual_dates():
        for p in read_candidates(d):
            if q in (p.get("title", "") + " " + p.get("abstract", "")).lower():
                hits.append({
                    "date": d, "arxiv_id": p.get("arxiv_id", ""),
                    "title": p.get("title", ""),
                    "tagline": p.get("abstract", "")[:200],
                    "score": 1, "from": "annual-candidates",
                })
    hits.sort(key=lambda h: -h["score"])
    return {"query": query, "count": len(hits), "hits": hits[:limit]}


def get_top5_annual(year_tag: Optional[str] = None) -> dict:
    annuals = annual_dates()
    if not annuals:
        return {"error": "no annual digests found"}
    tag = year_tag or annuals[0]
    p = DIGESTS_DIR / tag / "top5.md"
    if not p.exists():
        return {"error": f"{tag}/top5.md not found"}
    return {"tag": tag, "top5_md": p.read_text(encoding="utf-8")}


def get_candidates(date: Optional[str] = None, top_n: int = 30) -> dict:
    d = _resolve_date(date)
    if d is None:
        return {"error": "no digests"}
    cands = read_candidates(d)
    top = sorted(cands, key=lambda p: -p.get("score", 0))[:top_n]
    return {
        "date": d,
        "total_candidates": len(cands),
        "returned": len(top),
        "candidates": [
            {
                "arxiv_id": c["arxiv_id"],
                "score": c.get("score", 0),
                "title": c.get("title", ""),
                "topic_labels": (c.get("score_breakdown") or {}).get("topic_labels", []),
                "hf_upvotes": (c.get("score_breakdown") or {}).get("hf_upvotes", 0),
                "abs_url": c.get("abs_url", ""),
            }
            for c in top
        ],
    }


# ---------------------------------------------------------------------------
# Back-compat aliases (paper_radar.search and older callers import these)
# ---------------------------------------------------------------------------
_all_dates = all_dates
_read_candidates_json = read_candidates
_ranked_chunks = ranked_chunks
_list_daily_digests = daily_dates
_list_annual_digests = annual_dates
