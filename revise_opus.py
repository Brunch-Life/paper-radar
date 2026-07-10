#!/usr/bin/env python3
"""Targeted revision pass (方案 C): when the GPT audit scores a deep-read低,
feed the flagged issues + the ORIGINAL full text back to opus and fix ONLY
those items — correct, delete, or re-label as 「未溯源」. Everything else is
kept verbatim (figure line, tables, structure). Caller re-runs review_gpt.py
afterwards so the review card reflects the revised text.

Usage: revise_opus.py <pid_or_arxiv_id> <deepread_md> <review_json>
Env:   RELAY_BASE, SONNET_KEY  (same as deepread_opus; streamed SSE call)

Exit codes: 0 revised & written · 1 revision failed (original kept) · 2 skip
(no issues to fix / no original text).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/root/paper-radar")
import deepread_opus as dr
import review_gpt as rg


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    pid, md_path, rj_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    md = md_path.read_text(encoding="utf-8")
    review = json.loads(rj_path.read_text(encoding="utf-8"))
    issues = [item for item in (review.get("issues") or []) if str(item).strip()]
    if not issues:
        print(f"[{pid}] no issues listed — skip revision")
        sys.exit(2)
    text = rg.fetch_original(pid)
    if len(text) < 4000:
        print(f"[{pid}] no original text — skip revision")
        sys.exit(2)

    issue_lines = "\n".join(f"- {item}" for item in issues)
    prompt = (
        f"# 论文原文全文（已抓好，事实以此为准）：\n{text[:300000]}\n\n---\n\n"
        f"# 一篇基于该论文的中文精读（待修订）：\n{md}\n\n---\n\n"
        f"# 事实核查指出的问题：\n{issue_lines}\n\n"
        "请输出修订后的**完整**精读 markdown。规则：\n"
        "1. 只处理上面被点名的问题：对照原文改正；原文查无依据的删掉，"
        "或如果是有价值的外部知识/推断，改为挂「外部先验（非原文信息）」或「未溯源」标签保留。\n"
        "2. 其他所有内容（含开头的图片行、表格、标题层级、措辞）一律原样保留，不要顺手润色。\n"
        "3. 只输出修订后的 markdown，不要任何解释、前言或代码块包裹。"
    )
    for attempt in (1, 2):
        try:
            md2, usage = dr.call_opus(prompt)
        except Exception as ex:  # noqa: BLE001
            print(f"[{pid}] revise attempt {attempt} ERROR {type(ex).__name__}: {ex}", flush=True)
            continue
        md2 = dr._clean(md2)
        if len(md2) > max(1000, len(md) // 2):
            md_path.write_text(md2, encoding="utf-8")
            print(
                f"[{pid}] revised: {len(md)}→{len(md2)} chars, fixed {len(issues)} issues, "
                f"usage={usage.get('input_tokens')}→{usage.get('output_tokens')} tok "
                f"(attempt {attempt})",
                flush=True,
            )
            sys.exit(0)
        print(f"[{pid}] revise attempt {attempt} too short ({len(md2)} chars)", flush=True)
    print(f"[{pid}] revision FAILED — original kept", flush=True)
    sys.exit(1)


if __name__ == "__main__":
    main()
