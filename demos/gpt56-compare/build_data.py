#!/usr/bin/env python3
"""Build the public comparison data file from private experiment artifacts."""
import argparse
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--baseline-deepreads", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("data.json"))
    args = parser.parse_args()

    raw = json.loads((args.experiment / "summary.json").read_text(encoding="utf-8"))
    papers = []
    for result in raw["results"]:
        aid = result["aid"]
        opus = (args.baseline_deepreads / f"{aid}.md").read_text(encoding="utf-8")
        gpt = (
            args.experiment / "deepreads" / "gpt-5.6-sol" / f"{aid}.md"
        ).read_text(encoding="utf-8")
        title = aid
        for markdown in (gpt, opus):
            match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
            if match:
                candidate = re.sub(r"!\[[^]]*\]\([^)]*\)", "", match.group(1)).strip()
                if candidate:
                    title = candidate
                    break
        figure_url = ""
        for markdown in (opus, gpt):
            image = re.search(r"!\[[^]]*\]\((https://arxiv\.org/html/[^)]+)\)", markdown)
            if image:
                figure_url = image.group(1)
                break
        papers.append({
            "aid": aid,
            "title": title,
            "figure_url": figure_url,
            "mapping": result["mapping"],
            "judge": result["judge"],
            "outputs": {"claude-opus-4-8": opus, "gpt-5.6-sol": gpt},
        })
    payload = {
        "summary": raw["summary"],
        "papers": papers,
        "caveat": "GPT-5.6-Sol 同时是参赛者与评委，盲化不能完全消除自偏好。",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {len(papers)} papers to {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
