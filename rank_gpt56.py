#!/usr/bin/env python3
"""Build ranked.md from a directory of deep reads using GPT-5.6-Sol."""
import argparse
from pathlib import Path

from compare_gpt56_sol import call_retry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--deepreads", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    deepreads = Path(args.deepreads)
    documents = []
    for path in sorted(deepreads.glob("*.md")):
        documents.append(f"\n\n--- INPUT {path.stem} ---\n{path.read_text(encoding='utf-8')}")
    prompt = f"""你是 Paper Radar 的终审编辑。下面是 {args.date} 的全部 6-section 精读。
先删除第1节含“建议跳过”的论文，再按五项各1-5分：stack_fit、insight_density、
claim_strength、novelty、timing。只保留总分≥17，按总分降序，不凑数。

严格输出：
# Paper Radar — {args.date}

今天 N 篇值得读，按评分序。

---
# [#1 score=NN] <完整标题>
<完整精读正文>

依次类推。只输出最终 markdown，不解释。
""" + "".join(documents)
    ranked, usage = call_retry(prompt, 30000)
    Path(args.out).write_text(ranked, encoding="utf-8")
    print(f"ranked {len(documents)} inputs → {len(ranked)} chars; usage={usage}")


if __name__ == "__main__":
    main()
