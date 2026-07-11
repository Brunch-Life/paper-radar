#!/usr/bin/env python3
"""Targeted correction pass via GPT-5.6-Sol."""
import json
import sys
from pathlib import Path

import review_gpt as review_source
from compare_gpt56_sol import call_retry
from deepread_opus import _clean


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    pid, md_path, review_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    markdown = md_path.read_text(encoding="utf-8")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    issues = [str(item).strip() for item in review.get("issues", []) if str(item).strip()]
    original = review_source.fetch_original(pid)
    if not issues or len(original) < 4000:
        return 2
    prompt = (
        f"# 论文原文\n{original[:300000]}\n\n# 待修订精读\n{markdown}\n\n"
        f"# 被点名的事实问题\n" + "\n".join(f"- {item}" for item in issues) + "\n\n"
        "输出修订后的完整 markdown。只处理上述问题；其余内容原样保留。"
        "原文无依据的内容删除，或明确标为外部先验/未溯源。不要解释。"
    )
    revised, usage = call_retry(prompt, 16000)
    revised = _clean(revised)
    if len(revised) <= max(1000, len(markdown) // 2):
        return 1
    md_path.write_text(revised, encoding="utf-8")
    print(f"[{pid}] gpt-5.6-sol revised {len(markdown)}→{len(revised)}; usage={usage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
