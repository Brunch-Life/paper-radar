"""FastAPI app — REST + static frontend for paper-radar.

Endpoints:
    GET  /api/dates                       → digest date list
    GET  /api/papers?date=...             → ranked papers for date
    GET  /api/paper?arxiv_id=...&date=... → single paper deep-read
    GET  /api/search?q=...&limit=...      → fuzzy search
    GET  /api/top5?year_tag=...           → annual TOP 5
    GET  /api/candidates?date=...&n=...   → stage-1 candidates
    GET  /healthz                         → server health
    GET  /                                → SPA index.html
    GET  /<static>                        → frontend assets

Loopback only (host=127.0.0.1). No auth — meant for personal local use.
Run:  python -m uvicorn paper_radar.api:app --host 127.0.0.1 --port 7878
Or use the wrapper at scripts/serve.py.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from paper_radar import digests

SKILL_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = SKILL_ROOT / "frontend"

app = FastAPI(
    title="Paper Radar",
    description="Personal daily arXiv triage — REST + SPA",
    version="1.0.0",
)

START_TIME = datetime.now().isoformat()


# ---- API endpoints ---------------------------------------------------
@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "started_at": START_TIME,
        "digests_dir": str(digests.DIGESTS_DIR),
    }


@app.get("/api/dates")
def api_dates(limit: int = 14):
    return digests.list_dates(limit)


@app.get("/api/papers")
def api_papers(date: Optional[str] = None):
    res = digests.list_papers(date)
    if "error" in res:
        return JSONResponse(res, status_code=404)
    return res


@app.get("/api/paper")
def api_paper(arxiv_id: str, date: Optional[str] = None):
    res = digests.get_paper(arxiv_id, date)
    if "error" in res:
        return JSONResponse(res, status_code=404)
    return res


@app.get("/api/search")
def api_search(q: str, limit: int = 20):
    return digests.search_papers(q, limit)


@app.get("/api/top5")
def api_top5(year_tag: Optional[str] = None):
    res = digests.get_top5_annual(year_tag)
    if "error" in res:
        return JSONResponse(res, status_code=404)
    return res


@app.get("/api/candidates")
def api_candidates(date: Optional[str] = None, top_n: int = 30):
    res = digests.get_candidates(date, top_n)
    if "error" in res:
        return JSONResponse(res, status_code=404)
    return res


# ---- SPA static serving ----------------------------------------------
# Mounted last so /api/* takes precedence.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True),
              name="frontend")
else:
    @app.get("/")
    def fallback_index():
        return {
            "error": "frontend not built",
            "expected_at": str(FRONTEND_DIR),
            "api_docs": "/docs",
        }
