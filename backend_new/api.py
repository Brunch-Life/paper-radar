"""paper-radar REST API + static SPA. Clean rewrite (contract unchanged).

Read endpoints
    GET  /healthz
    GET  /api/dates?limit=            digest date list (daily + annual)
    GET  /api/papers?date=            ranked papers for a date
    GET  /api/paper?pid=&date=        one deep-read (any pid kind)
    GET  /api/search?q=&limit=        FTS5 + fuzzy + tag:xxx facets
    GET  /api/tags                    tag vocabulary with counts
    GET  /api/ondemand                on-demand read history
    GET  /api/weekly                  weekly syntheses
    GET  /api/recommend?limit=        personalized re-rank
    GET  /api/top5?year_tag=          annual top-5
    GET  /api/candidates?date=&top_n= stage-1 candidates
Signal endpoints (POST, {pid, ...})
    /api/vote /api/save /api/hide /api/click /api/dwell /api/interest
Routed to the root worker by Caddy (NOT here): /api/deepread*, /api/ask*

Loopback only; Caddy terminates TLS in front. This service runs as the
hardened `paperradar` user — read-only FS, no internet; mutable state goes
to redis via paper_radar.signals.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from paper_radar import digests, search, signals

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="Paper Radar", version="2.0.0",
              description="Personal daily arXiv triage — REST + SPA")
START_TIME = datetime.now().isoformat()


def _pid(payload: dict) -> str:
    return str(payload.get("pid") or payload.get("arxiv_id") or "").strip()


def _maybe_404(res: dict):
    return JSONResponse(res, status_code=404) if "error" in res else res


# ---- health -----------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok", "started_at": START_TIME,
            "digests_dir": str(digests.DIGESTS_DIR)}


# ---- digests ----------------------------------------------------------
@app.get("/api/dates")
def api_dates(limit: int = 14):
    return digests.list_dates(limit)


@app.get("/api/papers")
def api_papers(date: Optional[str] = None):
    return _maybe_404(digests.list_papers(date))


@app.get("/api/paper")
def api_paper(pid: str = "", arxiv_id: str = "", date: Optional[str] = None):
    return _maybe_404(digests.get_paper((pid or arxiv_id).strip(), date))


@app.get("/api/ondemand")
def api_ondemand():
    return digests.list_ondemand()


@app.get("/api/weekly")
def api_weekly():
    return digests.list_weekly()


@app.get("/api/top5")
def api_top5(year_tag: Optional[str] = None):
    return _maybe_404(digests.get_top5_annual(year_tag))


@app.get("/api/candidates")
def api_candidates(date: Optional[str] = None, top_n: int = 30):
    return _maybe_404(digests.get_candidates(date, top_n))


# ---- search -----------------------------------------------------------
@app.get("/api/search")
def api_search(q: str, limit: int = 30):
    return search.search(q, limit)


@app.get("/api/tags")
def api_tags():
    return {"tags": search.tag_counts()}


# ---- signals ----------------------------------------------------------
@app.get("/api/signals")
def api_signals():
    return signals.get_signals()


@app.post("/api/vote")
def api_vote(payload: dict = Body(...)):
    return signals.set_vote(_pid(payload), payload.get("vote"))


@app.post("/api/save")
def api_save(payload: dict = Body(...)):
    return signals.set_save(_pid(payload), bool(payload.get("saved")))


@app.post("/api/hide")
def api_hide(payload: dict = Body(...)):
    return signals.set_hidden(_pid(payload), bool(payload.get("hidden", True)))


@app.post("/api/click")
def api_click(payload: dict = Body(...)):
    return signals.record_click(_pid(payload))


@app.post("/api/dwell")
def api_dwell(payload: dict = Body(...)):
    return signals.record_dwell(_pid(payload), payload.get("ms", 0))


@app.post("/api/interest")
def api_interest(payload: dict = Body(...)):
    pid = _pid(payload)
    paper_tags, _ = search.tag_index()
    explicit = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    return signals.set_interest(pid, payload.get("score"),
                                list(paper_tags.get(pid, [])) + explicit)


# ---- annotations (highlighter + notes) ---------------------------------
@app.get("/api/annotations")
def api_annotations(pid: str = ""):
    return signals.list_annotations(pid.strip())


@app.post("/api/annotate")
def api_annotate(payload: dict = Body(...)):
    return signals.add_annotation(_pid(payload),
                                  str(payload.get("quote") or ""),
                                  str(payload.get("note") or ""))


@app.post("/api/annotate/update")
def api_annotate_update(payload: dict = Body(...)):
    return signals.update_annotation(_pid(payload),
                                     str(payload.get("id") or "").strip(),
                                     str(payload.get("note") or ""))


@app.post("/api/annotate/delete")
def api_annotate_delete(payload: dict = Body(...)):
    return signals.delete_annotation(_pid(payload),
                                     str(payload.get("id") or "").strip())


# ---- recommend --------------------------------------------------------
@app.get("/api/recommend")
def api_recommend(limit: int = 40):
    pt, pa = search.tag_index()
    ranked = signals.personalize(search.all_papers(), pt, pa)
    sig = signals.get_signals()
    downs = {p for p, v in sig["votes"].items() if v == "down"}
    hidden = set(sig.get("hidden", []))
    out = [p for p in ranked if p["pid"] not in downs and p["pid"] not in hidden][:limit]
    return {"count": len(out), "papers": out, "personalized": sig["available"]}


# ---- SPA static (mounted last so /api/* wins) ---------------------------
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:  # pragma: no cover
    @app.get("/")
    def fallback_index():
        return {"error": "frontend not built", "expected_at": str(FRONTEND_DIR)}
