"""paper-radar root worker — on-demand deep-reads, hard delete, ask-this-paper.

Clean rewrite. Runs as ROOT under its own systemd unit so it can reach the
network and write digests — the hardened main API (user `paperradar`,
IPAddressDeny) cannot. Caddy routes /api/deepread* and /api/ask* here
(127.0.0.1:7879); everything else goes to the main API on :7878.

Endpoints
    POST   /api/deepread          {url} → {id, pid, status}   (idempotent per URL)
    GET    /api/deepread?id=      → {id, status, markdown?}   status: running|done|error|unknown
    GET    /api/deepread/health
    POST   /api/deepread/delete   {pid} → hard-delete files + signals, mark hidden
    GET    /api/ask?pid=          → {pid, history}
    POST   /api/ask               {pid, question} → {pid, answer, history}
    DELETE /api/ask?pid=          clear history

Deep-read strategy (per job):
  1. server-side full text via deepread_gpt56.py — arXiv id OR any paper URL;
     downloads HTML→PDF itself (no WebFetch 10MB limit) and calls GPT-5.6-Sol.
  2. no browser fallback: missing full text is reported instead of degraded output.
  3. splice the paper's main arXiv figure; persist pid-keyed copy + sidecar so
     the read is searchable/openable everywhere; un-hide the pid (regeneration
     after a delete brings the paper back).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

from paper_radar import digests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO = Path("/root/paper-radar")
JOB_TIMEOUT_S = 900                     # hard cap per deep-read

OUTDIR = digests.DIGESTS_DIR / "ondemand"          # job files (jid-keyed)
OD_DEEP = OUTDIR / "deepreads"                     # pid-keyed reads + sidecars
OUTDIR.mkdir(parents=True, exist_ok=True)
OD_DEEP.mkdir(parents=True, exist_ok=True)

# ask-this-paper (GPT credentials are loaded from deepread.env per request)
RELAY_BASE = os.environ.get("ANTHROPIC_BASE_URL", "http://127.0.0.1:3000/api")
RELAY_KEY = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
ASK_MODEL = "gpt-5.6-sol"
ASK_MAX_HISTORY = 16               # messages kept (~8 turns)
ASK_DEEPREAD_CHARS = 40000         # 6-section deep-read injected as context
ASK_FULLTEXT_CHARS = 200000        # original paper text injected as context

# original-paper-text cache: fetched once per pid, persisted to disk so every
# later question reuses it (fetch = arxiv HTML → PDF via deepread_opus helpers)
PAPERTEXT_DIR = digests.DIGESTS_DIR / "ondemand" / "papertext"
PAPERTEXT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="paper-radar worker — deep-read / delete / ask")

# ---------------------------------------------------------------------------
# Redis (db2 — shared with paper_radar.signals key-wise)
# ---------------------------------------------------------------------------
try:
    import redis as _redis_lib
except Exception:  # pragma: no cover
    _redis_lib = None

_redis_cached = None
_chat_lock = threading.Lock()   # serializes read-modify-write of a chat key
_claim_lock = threading.Lock()  # makes check-status-then-claim atomic


def _redis():
    """Lazy connect — a redis blip at boot must not disable chat forever."""
    global _redis_cached
    if _redis_cached is not None:
        return _redis_cached
    if _redis_lib is None:
        return None
    try:
        c = _redis_lib.Redis(host="127.0.0.1", port=6379, db=2,
                             decode_responses=True, socket_timeout=2)
        c.ping()
        _redis_cached = c
        return c
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _normalize(url: str) -> str:
    """Full URL, or bare arxiv id (2606.12345[v3]) → abs URL."""
    url = url.strip()
    if re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", url):
        return f"https://arxiv.org/abs/{url}"
    return url


def _job_id(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def _arxiv_id(url: str):
    """arXiv id from a URL — anchored to the arxiv host so a random NNNN.NNNNN
    inside a non-arxiv URL is never misclassified."""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", url or "")
    return m.group(1) if m else None


def _paper_pid(url: str, aid) -> str:
    return aid if aid else "od_" + _job_id(url)


def _md_path(jid: str) -> Path:
    return OUTDIR / f"{jid}.md"


def _status_path(jid: str) -> Path:
    return OUTDIR / f"{jid}.status"


def _read_status(jid: str) -> str:
    p = _status_path(jid)
    return p.read_text().strip() if p.exists() else "unknown"


def _first_title(md: str) -> str:
    m = re.match(r"^#\s+([^\n]+)", md or "")
    return m.group(1).strip() if m else ""


def _section1_tagline(md: str) -> str:
    m = re.search(r"##\s*1\.[^\n]*\n(.*?)(?=\n##\s|\n---|\Z)", md or "", re.DOTALL)
    if not m:
        return ""
    for p in re.split(r"\n\s*\n", m.group(1).strip()):
        p = p.strip()
        if p:
            return re.sub(r"\s+", " ", re.sub(r"^[>*\s]+", "", p))[:240]
    return ""


def _atomic_write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False))
    os.replace(tmp, path)


def _load_env_file(env: dict, path: Path) -> None:
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deep-read pipeline
# ---------------------------------------------------------------------------
def _server_read(target: str) -> str:
    """Full-text read via deepread_gpt56.py. `target` is an arXiv id OR any
    paper URL (research page / direct PDF): the script fetches server-side
    (HTML → PDF fallback, no size limits) and calls GPT-5.6-Sol. → md or ''."""
    scratch = OD_DEEP / f".srv_{_job_id(target)}.md"
    try:
        env = os.environ.copy()
        _load_env_file(env, REPO / "deepread.env")   # RELAY_BASE / GPT_KEY
        subprocess.run(
            [sys.executable, str(REPO / "deepread_gpt56.py"), target, str(scratch)],
            env=env, capture_output=True, timeout=JOB_TIMEOUT_S,
        )
        if scratch.exists() and scratch.stat().st_size > 1000:
            return scratch.read_text()
    except Exception:
        pass
    finally:
        try:
            scratch.unlink()
        except Exception:
            pass
    return ""


def _browse_read(url: str) -> tuple[str, str]:
    """No low-confidence browser fallback: require server-fetched full text."""
    return "", f"无法抓取论文全文：{url}"


def _splice_figure(md: str, aid: str) -> str:
    """Best-effort: put the paper's main arXiv figure under the title."""
    try:
        sys.path.insert(0, str(REPO))
        from paper_figure import main_figure_url, prepend_figure
        return prepend_figure(md, main_figure_url(aid))
    except Exception:
        return md


def _persist(jid: str, url: str, aid, md: str, degraded: bool = False) -> None:
    """Write the job result + the pid-keyed copy/sidecar, un-hide the pid.
    `degraded` = True when this read came from the browse fallback (no full text);
    recorded as a <pid>.degraded marker so the frontend can flag it."""
    _md_path(jid).write_text(md)
    pid = _paper_pid(url, aid)
    try:
        (OD_DEEP / f"{pid}.md").write_text(md)
        _atomic_write_json(OD_DEEP / f"{pid}.json", {
            "pid": pid, "arxiv_id": aid or "", "url": url,
            "title": _first_title(md), "tagline": _section1_tagline(md),
            "tags": ["on-demand"], "ts": time.time(), "status": "done",
            "degraded": degraded,
        })
        mark = OD_DEEP / f"{pid}.degraded"
        if degraded:
            mark.write_text("")
        elif mark.exists():
            mark.unlink()
    except Exception:
        pass
    _status_path(jid).write_text("done")
    try:   # a freshly (re)generated read cancels a previous delete/hide
        r = _redis()
        if r:
            r.srem("pr:hidden", pid)
            if aid:
                r.srem("pr:hidden", aid)
    except Exception:
        pass


def _spawn_review(pid: str) -> None:
    """Fire-and-forget gpt audit of a fresh read (review card at article top).
    Runs in its own thread so the article is served the moment it's ready."""
    def run():
        try:
            env = os.environ.copy()
            _load_env_file(env, REPO / "deepread.env")   # GPT_KEY / RELAY_BASE
            subprocess.run(
                [sys.executable, str(REPO / "review_gpt.py"), pid,
                 str(OD_DEEP / f"{pid}.md"), str(OD_DEEP / f"{pid}.review.json")],
                env=env, capture_output=True, timeout=360,
            )
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


def _run_job(jid: str, url: str) -> None:
    _status_path(jid).write_text("running")
    try:
        aid = _arxiv_id(url)
        md = _server_read(aid or url)
        degraded = False
        err = ""
        if not md:
            degraded = True                 # full-text path failed → browse fallback
            md, err = _browse_read(url)
        if len(md) > 80:
            if aid:
                md = _splice_figure(md, aid)
            _persist(jid, url, aid, md, degraded)
            _spawn_review(_paper_pid(url, aid))
        else:
            _md_path(jid).write_text(f"## 解析失败\n\n{err or '空响应'}")
            _status_path(jid).write_text("error")
    except Exception as exc:   # never leave a job stuck on "running" (bricks the URL)
        try:
            _md_path(jid).write_text(f"## 解析失败\n\n工作进程异常：{exc}")
        except Exception:
            pass
        _status_path(jid).write_text("error")


def _reclaim_stale_jobs() -> None:
    """On worker start, any job still 'running' is an orphan from a previous
    process (a restart / crash mid-run) — flip it to error so the URL can be
    re-submitted instead of being stuck forever behind deepread_start's guard."""
    try:
        for p in OUTDIR.glob("*.status"):
            try:
                if p.read_text().strip() == "running":
                    p.write_text("error")
                    md = _md_path(p.stem)
                    if not md.exists():
                        md.write_text("## 解析失败\n\n任务被中断（工作进程重启），请重试。")
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Deep-read endpoints
# ---------------------------------------------------------------------------
@app.post("/api/deepread")
def deepread_start(payload: dict = Body(...)):
    url = _normalize(str(payload.get("url") or ""))
    if not re.match(r"^https?://", url):
        return JSONResponse({"error": "请提供有效的链接或 arXiv id"}, status_code=400)
    jid = _job_id(url)
    pid = _paper_pid(url, _arxiv_id(url))
    persisted = (OD_DEEP / f"{pid}.md").exists()   # false after a delete of this pid
    with _claim_lock:
        st = _read_status(jid)
        # a stale "done" whose persisted copy was deleted must NOT short-circuit —
        # otherwise re-submitting a deleted paper never regenerates it (stays 404
        # + hidden after reload). Treat that case as fresh.
        if st == "done" and persisted:
            return {"id": jid, "pid": pid, "status": "done", "url": url}
        fresh = st != "running"
        if fresh:
            _status_path(jid).write_text("running")
    if fresh:
        threading.Thread(target=_run_job, args=(jid, url), daemon=True).start()
    return {"id": jid, "pid": pid, "status": "running", "url": url}


_JID_RE = re.compile(r"[0-9a-f]{12}")


@app.get("/api/deepread")
def deepread_poll(id: str):
    # `id` builds a filesystem path on a root process reachable from the net —
    # only ever the 12-hex jid we mint. Reject anything else (traversal guard).
    if not _JID_RE.fullmatch(id or ""):
        return JSONResponse({"id": id, "status": "unknown"}, status_code=400)
    st = _read_status(id)
    res = {"id": id, "status": st}
    if st in ("done", "error") and _md_path(id).exists():
        res["markdown"] = _md_path(id).read_text()
    return res


@app.get("/api/deepread/health")
def deepread_health():
    return {"status": "ok", "outdir": str(OUTDIR)}


# ---------------------------------------------------------------------------
# Hard delete
# ---------------------------------------------------------------------------
def _delete_paper(pid: str) -> list:
    """Remove deep-read file(s) + sidecar + redis signals; mark hidden so the
    paper leaves every view until regenerated (regen clears the mark)."""
    pid = (pid or "").strip()
    removed: list = []
    if not pid or "/" in pid or "\\" in pid or ".." in pid or "\x00" in pid:
        return removed
    aid = pid if re.fullmatch(r"\d{4}\.\d{4,5}", pid) else ""
    for ext in (".md", ".json", ".review.json", ".degraded"):
        f = OD_DEEP / f"{pid}{ext}"
        if f.exists():
            try:
                f.unlink()
                removed.append(f.name)
            except Exception:
                pass
    if aid:
        try:
            for pat in (f"*/deepreads/{aid}.md", f"*/deepreads/{aid}.degraded"):
                for f in digests.DIGESTS_DIR.glob(pat):
                    try:
                        f.unlink()
                        removed.append(str(f.relative_to(digests.DIGESTS_DIR)))
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        r = _redis()
        if r:
            for k in ("pr:vote", "pr:clicks", "pr:dwell"):
                r.hdel(k, pid)
            r.srem("pr:saved", pid)
            r.sadd("pr:hidden", pid)
    except Exception:
        pass
    return removed


@app.post("/api/deepread/delete")
def deepread_delete(payload: dict = Body(...)):
    pid = str(payload.get("pid") or payload.get("arxiv_id") or "").strip()
    if not pid:
        return JSONResponse({"error": "no pid"}, status_code=400)
    return {"ok": True, "pid": pid, "removed": _delete_paper(pid)}


# ---------------------------------------------------------------------------
# Ask this paper (relay chat, per-pid history in redis)
# ---------------------------------------------------------------------------
def _chat_key(pid: str) -> str:
    return f"pr:chat:{pid}"


def _load_history(pid: str) -> list:
    c = _redis()
    if not c:
        return []
    try:
        raw = c.get(_chat_key(pid))
        return json.loads(raw) if raw else []
    except Exception:
        return []


def _save_history(pid: str, hist: list) -> None:
    c = _redis()
    if not c:
        return
    try:
        c.set(_chat_key(pid), json.dumps(hist[-ASK_MAX_HISTORY:], ensure_ascii=False))
    except Exception:
        pass


def _original_text(pid: str, paper: dict) -> str:
    """Original paper full text for a pid, cached on disk (papertext/<pid>.txt).

    First ask on a paper fetches it server-side (arxiv HTML → PDF, or the
    non-arxiv URL from the sidecar) via deepread_opus's fetch helpers; every
    later ask reads the cache. Weekly/ranked-no-arxiv papers have none → ''."""
    pid = (pid or "").strip()
    if not pid or "/" in pid or "\\" in pid or ".." in pid or "\x00" in pid:
        return ""
    cache = PAPERTEXT_DIR / f"{pid}.txt"
    if cache.exists():
        try:
            t = cache.read_text(encoding="utf-8")
            if len(t) > 500:
                return t
        except Exception:
            pass
    # resolve a fetchable source
    aid = paper.get("arxiv_id") or (pid if re.fullmatch(r"\d{4}\.\d{4,5}", pid) else "")
    url = ""
    if not aid and pid.startswith("od_"):
        url = (_read_sidecar_url(pid) or "").strip()
    if not aid and not url:
        return ""
    try:
        sys.path.insert(0, str(REPO))
        import deepread_opus as dr
        text = dr.fetch_text(aid) if aid else dr.fetch_url_text(url)
    except Exception:
        text = ""
    if text and len(text) > 500:
        try:
            tmp = cache.with_suffix(".txt.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, cache)
        except Exception:
            pass
        return text
    return ""


def _read_sidecar_url(pid: str) -> str:
    sc = OD_DEEP / f"{pid}.json"
    if not sc.exists():
        return ""
    try:
        return json.loads(sc.read_text(encoding="utf-8")).get("url") or ""
    except Exception:
        return ""


ASK_SYSTEM = (
    "你在帮一位清华 PhD（真机 RL / VLA，Franka FR3 + ManiSkill3 + RLinf）"
    "针对一篇论文答疑。下面依次是：这篇论文的精读（6-section）和论文原文全文"
    "（可能截断；没有原文段时只有精读）。回答优先引用原文（给出 §/Fig/Table 出处），"
    "精读用作导航；两者冲突以原文为准。简洁、敢下判断、尽量联系到他的 setting；"
    "问题超出材料范围就明说。生造概念/方法名/领域黑话第一次出现，"
    "先给一句白话解释再用术语。中文回答。\n"
)


@app.get("/api/ask")
def ask_history(pid: str):
    return {"pid": pid, "history": _load_history(pid)}


@app.delete("/api/ask")
def ask_clear(pid: str):
    c = _redis()
    if c:
        try:
            c.delete(_chat_key(pid))
        except Exception:
            pass
    return {"pid": pid, "history": []}


def _ask_relay_stream(system: str, messages: list, max_tokens: int = 3000,
                      attempts: int = 3) -> tuple[str, str]:
    """Streamed (SSE) relay call with exponential backoff. Streaming is required
    through the packyapi gateway: the system prompt carries the full paper text
    (up to ~90k chars), so a NON-streamed request looks idle for the whole
    generation and the gateway returns 500/504 (same failure that crippled the
    deep-reads). SSE chunks flow continuously → no idle timeout; retries ride out
    transient 5xx. → (answer, err); answer='' means all attempts failed."""
    last_err = ""
    for attempt in range(1, attempts + 1):
        backoff = min(30, 4 * 2 ** (attempt - 1))   # 4, 8, 16
        try:
            env = os.environ.copy()
            _load_env_file(env, REPO / "deepread.env")
            base = env.get("RELAY_BASE", RELAY_BASE).rstrip("/")
            key = env.get("GPT_KEY", "")
            url = env.get("REVIEW_GPT_URL", "").strip() or (
                base.replace("/api", "/openai", 1) + "/v1/responses"
            )
            url = url.replace("/v1/chat/completions", "/v1/responses")
            input_messages = [{"role": "system", "content": system}] + messages
            with requests.post(
                url,
                headers={"Authorization": f"Bearer {key}",
                         "content-type": "application/json"},
                json={"model": ASK_MODEL, "max_output_tokens": max_tokens,
                      "stream": True, "input": input_messages},
                stream=True, timeout=(20, 180),
            ) as r:
                if r.status_code != 200:
                    last_err = f"relay {r.status_code}: {r.text[:200]}"
                    if attempt < attempts:
                        time.sleep(backoff)
                    continue
                parts = []
                # iterate raw BYTES, not decode_unicode=True: for text/event-stream
                # with no charset, requests defaults r.encoding to ISO-8859-1, which
                # mangles UTF-8 Chinese into mojibake. json.loads decodes UTF-8 bytes.
                for raw in r.iter_lines():
                    if not raw or not raw.startswith(b"data:"):
                        continue
                    payload = raw[5:].strip()
                    if not payload or payload == b"[DONE]":
                        continue
                    try:
                        ev = json.loads(payload)
                    except ValueError:
                        continue
                    if ev.get("type") == "response.output_text.delta":
                        content = ev.get("delta")
                        if isinstance(content, str):
                            parts.append(content)
                    elif ev.get("type") == "response.output_text.done" and not parts:
                        content = ev.get("text")
                        if isinstance(content, str):
                            parts.append(content)
                answer = "".join(parts).strip()
                if answer:
                    return answer, ""
                last_err = "空回答"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if attempt < attempts:
            time.sleep(backoff)
    return "", last_err


@app.post("/api/ask")
def ask(payload: dict = Body(...)):
    pid = str(payload.get("pid") or "").strip()
    q = str(payload.get("question") or "").strip()
    if not pid or not q:
        return JSONResponse({"error": "pid 和 question 必填"}, status_code=400)
    env = os.environ.copy()
    _load_env_file(env, REPO / "deepread.env")
    if not env.get("GPT_KEY"):
        return JSONResponse({"error": "relay 未配置"}, status_code=503)
    paper = digests.get_paper(pid)
    if "error" in paper:
        return JSONResponse({"error": "论文未找到"}, status_code=404)

    system = ASK_SYSTEM + "\n# 论文精读（6-section）\n" \
        + (paper.get("full_md") or "")[:ASK_DEEPREAD_CHARS]
    orig = _original_text(pid, paper)          # disk-cached; first ask fetches
    if orig:
        system += "\n\n# 论文原文全文（自动抓取，可能截断）\n" + orig[:ASK_FULLTEXT_CHARS]

    messages = _load_history(pid) + [{"role": "user", "content": q}]
    answer, err = _ask_relay_stream(system, messages)
    if not answer:
        return JSONResponse({"error": f"relay 失败: {err}"}, status_code=502)

    # append under a lock: concurrent same-pid asks must not clobber each other
    with _chat_lock:
        cur = _load_history(pid)
        cur += [{"role": "user", "content": q}, {"role": "assistant", "content": answer}]
        _save_history(pid, cur)
    return {"pid": pid, "answer": answer, "history": cur[-ASK_MAX_HISTORY:]}


_reclaim_stale_jobs()   # runs on import too (systemd ExecStart imports this module)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 7879)))
