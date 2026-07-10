#!/usr/bin/env python3
"""Stage-2 deep-read via claude-opus-4-8 (streamed messages API) + 1M-context beta.

Pre-fetches the paper's full text (arXiv HTML, up to 400k chars) and injects it,
so opus reads the WHOLE paper deterministically (no browsing, no truncation).
The 1M-context beta header is a safety net for papers whose text exceeds the
standard 200k-token window — it only changes pricing when input actually
exceeds 200k, otherwise it is a no-op.

Exit codes:
  0  wrote a good deep-read to <out_path>
  2  no full text could be fetched (caller should fall back to a browsing read)
  1  fetched text but the model call failed

Usage: deepread_opus.py <arxiv_id> <out_path>
Env:   RELAY_BASE, SONNET_KEY   (claude-bound cr_ key; serves opus too)
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

RELAY = os.environ.get("RELAY_BASE", "http://127.0.0.1:3000/api").rstrip("/")
KEY = os.environ.get("SONNET_KEY", "")   # claude-bound key serves opus; checked at call time
URL = RELAY + "/v1/messages"
BETA = "context-1m-2025-08-07"
SKILL = open(os.environ.get("DEEPREAD_SKILL", "/root/.claude/skills/paper-deep-read/SKILL.md")).read()

STUDENT = ("读者：清华 PhD year-1，真机 Franka FR3 + ManiSkill3 + RLinf，"
           "VLA fine-tune (π0/OpenVLA)。")


def _skill_lean() -> str:
    # Full text is injected below, so drop the Claude-web_fetch-specific 输入处理
    # section (it tells the model to go fetch — contradicts "text already given").
    return re.sub(r"\n## 输入处理\b.*?(?=\n## 输出结构)", "\n", SKILL, flags=re.S)


def fetch_text(aid: str) -> str:
    ua = {"User-Agent": "Mozilla/5.0 (paper-radar deepread)"}
    for v in ("", "v1", "v2", "v3"):
        try:
            req = urllib.request.Request(f"https://arxiv.org/html/{aid}{v}", headers=ua)
            html = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "ignore")
            if len(html) > 8000:
                html = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ",
                              html, flags=re.S | re.I)
                txt = re.sub(r"<[^>]+>", " ", html)
                txt = re.sub(r"&\w+;", " ", txt)
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) > 4000:
                    return txt[:400000]
        except Exception:
            continue
    # No usable arXiv HTML → fall back to the PDF. Downloading server-side has no
    # WebFetch 10MB limit, and pdftotext pulls the full body incl. tables/appendix.
    return fetch_pdf_text(aid)


_UA = {"User-Agent": "Mozilla/5.0 (paper-radar deepread)"}


def _pdf_bytes_to_text(data: bytes) -> str:
    if len(data) < 10000:
        return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(data)
            f.flush()
            out = subprocess.run(["pdftotext", "-q", f.name, "-"],
                                 capture_output=True, timeout=180
                                 ).stdout.decode("utf-8", "ignore")
    except Exception:
        return ""
    return re.sub(r"\s+", " ", out).strip()[:400000]


def _html_to_text(html: str) -> str:
    h = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    h = re.sub(r"&\w+;", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def fetch_pdf_text(aid: str) -> str:
    for v in ("", "v1", "v2", "v3"):
        try:
            data = urllib.request.urlopen(
                urllib.request.Request(f"https://arxiv.org/pdf/{aid}{v}", headers=_UA),
                timeout=120).read()
        except Exception:
            continue
        txt = _pdf_bytes_to_text(data)
        if len(txt) > 4000:
            return txt
    return ""


def fetch_url_text(url: str) -> str:
    """Full text for ANY paper URL (non-arXiv): a PDF link → pdftotext; an HTML
    page → strip its text AND download any linked .pdf (the full paper) and
    extract that too. Server-side download has no WebFetch 10MB limit."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=120) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read(80_000_000)
            final_url = r.geturl()
    except Exception:
        return ""
    if "pdf" in ctype or final_url.lower().split("?")[0].endswith(".pdf") or data[:5] == b"%PDF-":
        return _pdf_bytes_to_text(data)
    html = data.decode("utf-8", "ignore")
    page = _html_to_text(html)
    pdf_txt = ""
    m = re.search(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, re.I)
    if m:
        try:
            pdf_url = urllib.parse.urljoin(final_url, m.group(1))
            with urllib.request.urlopen(urllib.request.Request(pdf_url, headers=_UA), timeout=120) as r2:
                pdf_txt = _pdf_bytes_to_text(r2.read(80_000_000))
        except Exception:
            pdf_txt = ""
    combined = (pdf_txt + "\n\n=== 页面正文 ===\n" + page) if pdf_txt else page
    return combined[:400000]


def build_prompt(src_url: str, text: str) -> str:
    # Long document goes FIRST — per Anthropic long-context guidance, place the
    # full paper above the instructions/skill/query, not below.
    return (f"# 论文全文（来源 {src_url}，已抓好，直接基于它精读，不要再抓取）：\n{text}\n\n"
            f"---\n\n"
            f"# Paper Deep-Read SKILL（逐条执行）：\n{_skill_lean()}\n\n{STUDENT}\n\n"
            f"请对上面这篇论文做精读。严格输出 6-section markdown（开头一行 `# <完整标题>`），"
            f"只输出 markdown，不要写文件、不要任何额外解释或前后缀。")


def call_opus(prompt: str, timeout: int = 300):
    """Streamed SSE call; streaming prevents proxy idle timeouts on long jobs."""
    if not KEY:
        raise RuntimeError("SONNET_KEY not set (source deepread.env)")
    body = json.dumps({"model": "claude-opus-4-8", "max_tokens": 16000, "stream": True,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "x-api-key": KEY, "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA, "content-type": "application/json"})
    parts, usage = [], {}
    resp = urllib.request.urlopen(req, timeout=timeout)
    try:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except ValueError:
                continue
            etype = ev.get("type")
            if etype == "content_block_delta":
                delta = ev.get("delta", {})
                if "text" in delta:
                    parts.append(delta["text"])
            elif etype == "message_start":
                usage.update((ev.get("message", {}) or {}).get("usage", {}) or {})
            elif etype == "message_delta":
                usage.update(ev.get("usage", {}) or {})
            elif etype == "error":
                raise RuntimeError(f"stream error: {ev.get('error')}")
    finally:
        resp.close()
    return "".join(parts).strip(), usage


def _clean(md: str) -> str:
    md = md.strip()
    if md.startswith("```"):
        md = re.sub(r"^```[a-zA-Z]*\n", "", md)
        md = re.sub(r"\n```$", "", md).strip()
    return md


def _unhide(aid: str) -> None:
    """A freshly (re)generated deep-read clears the delete/hide mark for the paper."""
    try:
        import redis
        redis.Redis(host="127.0.0.1", port=6379, db=2,
                    decode_responses=True, socket_timeout=1.5).srem("pr:hidden", aid)
    except Exception:
        pass


def main():
    arg, out = sys.argv[1], sys.argv[2]
    if re.match(r"^https?://", arg):
        text = fetch_url_text(arg)        # any paper URL (research page / PDF)
        src, aid = arg, ""
    else:
        text = fetch_text(arg)            # arXiv id (HTML → PDF fallback)
        src, aid = f"https://arxiv.org/abs/{arg}", arg
    if len(text) < 4000:
        print(f"[{arg}] no full text fetched ({len(text)} chars) — signal browse fallback",
              flush=True)
        sys.exit(2)
    prompt = build_prompt(src, text)
    attempts = 4
    for attempt in range(1, attempts + 1):
        backoff = min(60, 5 * 2 ** (attempt - 1))
        try:
            md, usage = call_opus(prompt)
        except Exception as ex:
            print(f"[{arg}] opus attempt {attempt}/{attempts} ERROR {type(ex).__name__}: {ex}", flush=True)
            if attempt < attempts:
                time.sleep(backoff)
            continue
        md = _clean(md)
        if len(md) > 1000:
            open(out, "w", encoding="utf-8").write(md)
            if aid:
                _unhide(aid)
            print(f"[{arg}] ok: {len(md)} chars out | in={len(text)} chars, "
                  f"usage={usage.get('input_tokens')}→{usage.get('output_tokens')} tok "
                  f"(attempt {attempt})", flush=True)
            return
        print(f"[{arg}] opus attempt {attempt}/{attempts} short ({len(md)} chars)", flush=True)
        if attempt < attempts:
            time.sleep(backoff)
    print(f"[{arg}] FAILED (had {len(text)} chars text)", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
