#!/usr/bin/env python3
"""Re-rank and assemble an annual digest from staged GPT-5.6 deep-reads."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

from compare_gpt56_sol import MODEL, call_retry


RUBRIC = (
    "stack_fit（与 Franka FR3、ManiSkill3、RLinf、VLA 微调和大规模真机 RL 的耦合度）、"
    "insight_density（可执行研究启发的数量与非平凡度）、claim_strength（证据硬度）、"
    "novelty（方法新意）、timing（半年内可执行性）"
)


def load(deepreads: Path) -> list[dict]:
    papers = []
    for path in sorted(deepreads.glob("*.md")):
        if not re.fullmatch(r"\d{4}\.\d{4,5}", path.stem):
            continue
        markdown = path.read_text(encoding="utf-8")
        match = re.match(r"^#\s+(.+)", markdown)
        title = match.group(1).strip() if match else path.stem
        title = re.sub(r"^\d{4}\.\d{4,5}\s*[—-]\s*", "", title)
        body = re.sub(r"^#\s+[^\n]*\n+", "", markdown, count=1)
        papers.append({"aid": path.stem, "title": title, "body": body})
    return papers


def prompt_for(papers: list[dict]) -> str:
    parts = [
        "你是 Paper Radar 的年度终审编辑。读者是清华本部 PhD，使用真机 Franka FR3、"
        "ManiSkill3、RLinf，研究 VLA 微调和大规模真机强化学习。下面是已经入选的 50 篇"
        "年度精读；不得删除任何一篇，只负责重新评分、排序、总结。\n\n"
        f"每篇按五项各打 1-5 分：{RUBRIC}，总分满分 25。同分时按对读者的边际价值排序。\n\n"
        "严格输出以下四段，不要附加解释：\n"
        "===RANKING===\n"
        "共 50 行，每行 `<arxiv_id> | <总分整数> | <一句话排序理由>`\n"
        "===SYNTHESIS===\n"
        "年度综述 markdown，用 ##/### 标题，不用一级标题；总结主线、分歧、证据缺口及未来半年重点。"
        "不要把 world model 当成读者兴趣。生造或小众术语首次出现时给一句白话解释。\n"
        "===TOP5===\n"
        "按边际价值挑 5 篇，markdown 中写 arXiv ID 和选择理由。\n"
        "===END===\n\n# 50 篇精读：\n"
    ]
    for paper in papers:
        parts.append(
            f"\n\n===== PAPER {paper['aid']} : {paper['title']} =====\n{paper['body']}"
        )
    return "".join(parts)


def parse(raw: str) -> tuple[list[tuple[str, int, str]], str, str]:
    ranking_match = re.search(r"===RANKING===\s*(.*?)\s*===SYNTHESIS===", raw, re.S)
    synthesis_match = re.search(r"===SYNTHESIS===\s*(.*?)\s*===TOP5===", raw, re.S)
    top5_match = re.search(r"===TOP5===\s*(.*?)\s*===END===", raw, re.S)
    rows = []
    if ranking_match:
        for line in ranking_match.group(1).splitlines():
            match = re.match(r"\s*`?(\d{4}\.\d{4,5})`?\s*\|\s*(\d+)\s*\|\s*(.+)", line)
            if match:
                rows.append((match.group(1), int(match.group(2)), match.group(3).strip()))
    return (
        rows,
        synthesis_match.group(1).strip() if synthesis_match else "",
        top5_match.group(1).strip() if top5_match else "",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--annual",
        default="/root/code/paper_reading_walkstream/digests/annual-2026-05-07",
    )
    parser.add_argument("--deepreads", required=True)
    parser.add_argument("--from-cache", action="store_true")
    args = parser.parse_args()

    annual = Path(args.annual)
    deepreads = Path(args.deepreads)
    papers = load(deepreads)
    if len(papers) != 50:
        raise SystemExit(f"expected exactly 50 staged papers, found {len(papers)}")
    by_aid = {paper["aid"]: paper for paper in papers}
    raw_path = annual / "annual_rank_gpt56_20260711.txt"
    if args.from_cache and raw_path.exists():
        raw = raw_path.read_text(encoding="utf-8")
        usage = {"cache": True}
    else:
        raw, usage = call_retry(prompt_for(papers), 16000)
        raw_path.write_text(raw, encoding="utf-8")
    rows, synthesis, top5 = parse(raw)
    unique_ids = [aid for aid, _, _ in rows if aid in by_aid]
    if len(rows) != 50 or len(set(unique_ids)) != 50 or set(unique_ids) != set(by_aid):
        raise SystemExit(f"invalid ranking: rows={len(rows)}, unique_known={len(set(unique_ids))}")
    if not synthesis or not top5:
        raise SystemExit("missing annual synthesis or Top 5; refusing to publish")

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    review_lines = [
        "# Paper Radar — Annual Review (past 365 days)\n",
        f"\nGPT-5.6-Sol 重跑与重排 {now} · 50 篇（{RUBRIC}）\n",
        "\n## 目录\n",
    ]
    for index, (aid, score, reason) in enumerate(rows, 1):
        review_lines.append(
            f"{index}. **[{score}/25]** [{by_aid[aid]['title']}](#p{index}) — `{aid}`\n"
        )
    review_lines.extend(["\n---\n", "\n# 📅 年度综述 · 过去一年\n\n", synthesis, "\n\n---\n"])
    for aid, score, reason in rows:
        paper = by_aid[aid]
        review_lines.append(
            f"\n# {paper['title']}\n\n**arxiv:** https://arxiv.org/abs/{aid} · "
            f"**年度评分:** {score}/25 — {reason}\n\n{paper['body']}\n\n---\n"
        )

    review_out = annual / "annual_review.gpt56-new.md"
    top5_out = annual / "top5.gpt56-new.md"
    review_out.write_text("".join(review_lines), encoding="utf-8")
    top5_out.write_text(
        "# Paper Radar — Annual TOP 5 (past 365 days) · GPT-5.6-Sol\n\n" + top5 + "\n",
        encoding="utf-8",
    )
    meta = {
        "model": MODEL,
        "papers": len(rows),
        "generated_at": now,
        "usage": usage,
        "annual_review": str(review_out),
        "top5": str(top5_out),
    }
    (annual / "annual_rebuild_gpt56_20260711.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"staged annual_review={review_out.stat().st_size}B top5={top5_out.stat().st_size}B usage={usage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
