#!/usr/bin/env python3
"""Post-generation audit: gpt-5.5 checks a deep-read against the paper's
ORIGINAL full text — factual accuracy (numbers / attributions / claims) and
completeness (missed contributions / limitations / ablations) — and emits a
score + short verdict shown at the top of the article.

Usage: review_gpt.py <pid_or_arxiv_id> <deepread_md> <out_review_json>
Env:   RELAY_BASE, GPT_KEY  (codex-bound cr_ key, see deepread.env)

The original text comes from the shared papertext disk cache
(digests/ondemand/papertext/<pid>.txt — also used by ask-this-paper); a cache
miss triggers a server-side fetch via deepread_opus helpers and fills it.
Cross-model on purpose: the reviewer (gpt-5.5) is a different model family
from the writer (opus), so shared blind spots don't self-certify.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

RELAY = os.environ.get("RELAY_BASE", "http://127.0.0.1:3000/api").rstrip("/")
GPT_KEY = os.environ.get("GPT_KEY", "")
# chat/completions (not /responses): gpt-5.5 is a reasoning model whose
# /responses output can be reasoning-only with an empty message array; the
# chat/completions shape returns final text reliably in choices[].message.
URL = os.environ.get("REVIEW_GPT_URL", "").strip() \
    or (RELAY.replace("/api", "/openai", 1) + "/v1/chat/completions")

# provider switch: "gpt" (default, cross-model audit) or "anthropic" (fallback
# while no GPT channel is available — a separate opus instance does the audit)
PROVIDER = os.environ.get("REVIEW_PROVIDER", "gpt")
MODEL = os.environ.get("REVIEW_MODEL", "gpt-5.5" if PROVIDER == "gpt" else "claude-opus-4-8")
ANTHROPIC_KEY = os.environ.get("SONNET_KEY", "")

DIGESTS = Path(os.environ.get("PAPER_RADAR_DIGESTS_DIR",
                              "/root/code/paper_reading_walkstream/digests"))
PAPERTEXT = DIGESTS / "ondemand" / "papertext"

DEEPREAD_CAP = 40000
ORIG_CAP = 250000

PROMPT = """你是学术事实核查员。下面依次是:一篇论文的【原文全文】(可能截断)和据它写成的【中文精读】。
审核精读对原文的转述是否属实。**审核范围规则(先读)**:

以下内容【不在审核范围】,不列为 issue、不扣分:
a. 精读里已标注「未溯源」「外部先验」「论文没给」「推测」的内容;
b. 「作者 & 团队」节对作者是谁/招牌方向/机构风格的背景介绍——这些设计上就来自论文之外,你无法用原文验证;**只有当它与原文署名/机构信息直接矛盾时才算错误**;
c. 评价性判断(「对比不成立」「证据弱」「搬过去会崩」等)——这是精读的本职,判断本身不算失实;**只有当判断中对原文内容的转述有错时**,错的是那个转述。

1. **事实核对**(决定 score):精读对原文的每个转述——数字、引用出处、方法机制、实验设置——是否与原文一致?列出所有真实错误(错引、编造数字/页数、机制误述、把原文没说的当原文说的)。
2. **完整性**(不影响 score):原文的关键贡献、最强证据、重要 limitation/消融,精读漏了什么重要的?写进 completeness 字段。
3. 打分(0-10,一位小数,只衡量事实错误):9-10=转述全部属实;7-8.9=无明确错误但有轻微不精确;5-6.9=有明确事实错误;<5=多处错误或严重误导。

只输出一个 JSON 对象,不要任何其他文字:
{"score": <数字>, "verdict": "<≤80字中文简评,先说结论再说最主要的问题>", "issues": ["<≤40字/条,最多5条,只放范围内的真实错误;没有就空数组>"], "completeness": "<≤60字,最重要的遗漏;没有就空字符串>"}"""


def fetch_original(pid: str) -> str:
    """papertext cache → deepread_opus fetch (fills the shared cache)."""
    if not pid or "/" in pid or ".." in pid:
        return ""
    cache = PAPERTEXT / f"{pid}.txt"
    if cache.exists():
        try:
            t = cache.read_text(encoding="utf-8")
            if len(t) > 500:
                return t
        except Exception:
            pass
    aid = pid if re.fullmatch(r"\d{4}\.\d{4,5}", pid) else ""
    url = ""
    if not aid:
        sc = DIGESTS / "ondemand" / "deepreads" / f"{pid}.json"
        if sc.exists():
            try:
                url = json.loads(sc.read_text()).get("url") or ""
            except Exception:
                pass
    if not aid and not url:
        return ""
    try:
        sys.path.insert(0, "/root/paper-radar")
        import deepread_opus as dr
        text = dr.fetch_text(aid) if aid else dr.fetch_url_text(url)
    except Exception:
        return ""
    if text and len(text) > 500:
        try:
            PAPERTEXT.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".txt.tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, cache)
        except Exception:
            pass
    return text or ""


def call_gpt(text: str, timeout: int = 240) -> str:
    if not GPT_KEY:
        raise RuntimeError("GPT_KEY not set (source deepread.env)")
    # max_tokens generous: gpt-5.5 spends reasoning tokens from the same budget
    body = json.dumps({
        "model": MODEL, "max_tokens": 4000,
        "messages": [{"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": f"Bearer {GPT_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()


def call_anthropic(text: str, timeout: int = 240) -> str:
    if not ANTHROPIC_KEY:
        raise RuntimeError("SONNET_KEY not set (source deepread.env)")
    body = json.dumps({
        "model": MODEL, "max_tokens": 1200,
        "messages": [{"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request(RELAY + "/v1/messages", data=body, headers={
        "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text").strip()


def call_model(text: str) -> str:
    return call_anthropic(text) if PROVIDER == "anthropic" else call_gpt(text)


def parse_review(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        raise ValueError(f"no JSON in reply: {raw[:200]}")
    d = json.loads(m.group(0))
    score = round(float(d["score"]), 1)
    if not (0 <= score <= 10):
        raise ValueError(f"score out of range: {score}")
    return {
        "score": score,
        "verdict": str(d.get("verdict", "")).strip()[:200],
        "issues": [str(i).strip()[:80] for i in (d.get("issues") or [])][:5],
        "completeness": str(d.get("completeness", "")).strip()[:120],
    }


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    pid, md_path, out_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    md = md_path.read_text(encoding="utf-8")
    orig = fetch_original(pid)
    if not orig:
        print(f"[{pid}] no original text available — skip review")
        sys.exit(0)         # not an error: wk_/r_ papers have no original
    prompt = (f"{PROMPT}\n\n# 原文全文(可能截断)\n{orig[:ORIG_CAP]}\n\n"
              f"# 中文精读\n{md[:DEEPREAD_CAP]}")
    for attempt in (1, 2):
        try:
            review = parse_review(call_model(prompt))
            break
        except Exception as e:
            print(f"[{pid}] review attempt {attempt} failed: {e}")
            if attempt == 2:
                sys.exit(1)
            time.sleep(5)
    review.update({"model": MODEL, "ts": time.time()})
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(review, ensure_ascii=False))
    os.replace(tmp, out_path)
    print(f"[{pid}] review {review['score']}/10 — {review['verdict'][:60]}")


if __name__ == "__main__":
    main()
