#!/usr/bin/env python3
"""Isolated Opus vs gpt-5.6-sol deep-read comparison.

Uses an existing day's candidates and production Opus deep-reads, generates a
second set with gpt-5.6-sol from the same full text/prompt, then asks
gpt-5.6-sol to judge randomized anonymous A/B pairs against the paper text.
Nothing under the production digest date is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import deepread_opus as dr


MODEL = os.environ.get("COMPARE_MODEL", "gpt-5.6-sol")
RELAY = os.environ.get("RELAY_BASE", "").rstrip("/")
KEY = os.environ.get("GPT_KEY", "")
URL = os.environ.get("COMPARE_GPT_URL", "").strip() or (
    RELAY.replace("/api", "/openai", 1) + "/v1/responses"
)
URL = URL.replace("/v1/chat/completions", "/v1/responses")

JUDGE_PROMPT = """你是论文精读质量盲评专家。下面给出论文原文和两份匿名中文精读 A/B。
你不知道作者或模型。必须逐份对照原文，不能因文风、模型猜测或自我偏好加分。

按六项分别给 A、B 打 0-10 分：
1. factuality：数字、归因、机制、实验结论是否忠于原文
2. move：是否抓准论文真正的新动作
3. evidence：证据强度和图表引用是否具体准确
4. critique：挑刺是否有原文靶点、没有歪曲原文
5. usefulness：对 Franka FR3、ManiSkill3、RLinf、VLA 微调和大规模真机 RL 是否有可执行价值
6. clarity：结构、术语白话解释和信息密度

先独立评分，再给 overall=A/B/tie。事实性权重最高；若一方有明确编造，必须在 issues 点出。
只输出 JSON，不要 markdown：
{"scores":{"A":{"factuality":0,"move":0,"evidence":0,"critique":0,"usefulness":0,"clarity":0},"B":{"factuality":0,"move":0,"evidence":0,"critique":0,"usefulness":0,"clarity":0}},"overall":"A","confidence":0.0,"issues":{"A":[],"B":[]},"reason":"≤150字中文理由"}
"""


def call_chat(prompt: str, max_tokens: int, timeout: int = 900) -> tuple[str, dict]:
    if not KEY or not RELAY:
        raise RuntimeError("source deepread.env first (RELAY_BASE/GPT_KEY required)")
    body = json.dumps({
        "model": MODEL,
        "max_output_tokens": max_tokens,
        "stream": True,
        "input": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    })
    chunks: list[str] = []
    final = ""
    usage: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw in response:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == b"[DONE]":
                    continue
                event = json.loads(payload)
                event_type = event.get("type", "")
                if event_type == "response.output_text.delta":
                    chunks.append(event.get("delta", ""))
                elif event_type == "response.output_text.done":
                    final = event.get("text", "") or final
                elif event_type == "response.completed":
                    usage = (event.get("response") or {}).get("usage") or usage
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    return (final or "".join(chunks)).strip(), usage


def call_retry(prompt: str, max_tokens: int) -> tuple[str, dict]:
    for attempt in range(1, 5):
        try:
            text, usage = call_chat(prompt, max_tokens)
            if len(text) >= 200:
                return text, usage
            raise ValueError(f"short response: {len(text)} chars")
        except Exception as exc:  # noqa: BLE001
            print(f"  attempt {attempt}/4 failed: {type(exc).__name__}: {exc}", flush=True)
            if attempt == 4:
                raise
            time.sleep(5 * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


def parse_json(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.S)
    if not match:
        raise ValueError(f"no JSON: {raw[:200]}")
    return json.loads(match.group(0))


def top_ids(candidates: Path, count: int) -> list[str]:
    papers = json.loads(candidates.read_text(encoding="utf-8"))
    papers.sort(key=lambda paper: -paper.get("score", 0))
    return [paper["arxiv_id"] for paper in papers[:count]]


def blinded_order(aid: str, opus: str, sol: str) -> tuple[str, str, dict]:
    seed = int(hashlib.sha256((aid + "paper-radar-ab-v1").encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    if rng.randrange(2):
        return sol, opus, {"A": MODEL, "B": "claude-opus-4-8"}
    return opus, sol, {"A": "claude-opus-4-8", "B": MODEL}


def summarize(results: list[dict]) -> dict:
    wins = {MODEL: 0, "claude-opus-4-8": 0, "tie": 0, "error": 0}
    dimensions: dict[str, dict[str, list[float]]] = {}
    for result in results:
        if result.get("error"):
            wins["error"] += 1
            continue
        winner = result["judge"].get("overall", "tie")
        winner_model = result["mapping"].get(winner, "tie") if winner in ("A", "B") else "tie"
        wins[winner_model] = wins.get(winner_model, 0) + 1
        for label, model in result["mapping"].items():
            for dim, score in result["judge"].get("scores", {}).get(label, {}).items():
                dimensions.setdefault(dim, {}).setdefault(model, []).append(float(score))
    means = {
        dim: {model: round(sum(values) / len(values), 3) for model, values in by_model.items() if values}
        for dim, by_model in dimensions.items()
    }
    return {"model": MODEL, "judge_model": MODEL, "n": len(results), "wins": wins, "mean_scores": means}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-07-08")
    parser.add_argument("--count", type=int, default=18)
    parser.add_argument("--digests", default="/root/code/paper_reading_walkstream/digests")
    parser.add_argument("--out", default="/root/paper-radar/experiments")
    args = parser.parse_args()

    digest = Path(args.digests) / args.date
    experiment = Path(args.out) / f"{args.date}-opus-vs-{MODEL}"
    sol_dir = experiment / "deepreads" / MODEL
    judge_dir = experiment / "judgments"
    sol_dir.mkdir(parents=True, exist_ok=True)
    judge_dir.mkdir(parents=True, exist_ok=True)
    ids = top_ids(digest / "candidates.json", args.count)
    results: list[dict] = []

    for index, aid in enumerate(ids, 1):
        print(f"[{index}/{len(ids)}] {aid}", flush=True)
        opus_path = digest / "deepreads" / f"{aid}.md"
        sol_path = sol_dir / f"{aid}.md"
        judgment_path = judge_dir / f"{aid}.json"
        if not opus_path.exists():
            results.append({"aid": aid, "error": "missing production Opus deep-read"})
            continue
        try:
            original = dr.fetch_text(aid)
            if len(original) < 4000:
                raise RuntimeError(f"full text unavailable ({len(original)} chars)")
            if not sol_path.exists() or sol_path.stat().st_size < 1000:
                prompt = dr.build_prompt(f"https://arxiv.org/abs/{aid}", original)
                generated, usage = call_retry(prompt, 16000)
                generated = dr._clean(generated)
                sol_path.write_text(generated, encoding="utf-8")
                (sol_path.with_suffix(".usage.json")).write_text(
                    json.dumps(usage, ensure_ascii=False), encoding="utf-8"
                )
            if judgment_path.exists():
                result = json.loads(judgment_path.read_text(encoding="utf-8"))
            else:
                opus = opus_path.read_text(encoding="utf-8")
                sol = sol_path.read_text(encoding="utf-8")
                a_text, b_text, mapping = blinded_order(aid, opus, sol)
                judge_prompt = (
                    f"{JUDGE_PROMPT}\n\n# 论文原文\n{original[:250000]}\n\n"
                    f"# 匿名精读 A\n{a_text[:40000]}\n\n# 匿名精读 B\n{b_text[:40000]}"
                )
                raw, usage = call_retry(judge_prompt, 5000)
                result = {
                    "aid": aid,
                    "mapping": mapping,
                    "judge": parse_json(raw),
                    "judge_model": MODEL,
                    "usage": usage,
                }
                judgment_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append(result)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {type(exc).__name__}: {exc}", flush=True)
            results.append({"aid": aid, "error": f"{type(exc).__name__}: {exc}"})

        summary = summarize(results)
        (experiment / "summary.json").write_text(
            json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  current wins: {summary['wins']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
