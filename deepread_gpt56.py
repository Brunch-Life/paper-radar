#!/usr/bin/env python3
"""Production full-text deep-read via the GPT-5.6-Sol streaming endpoint."""
import re
import sys
import time

import deepread_opus as source
from compare_gpt56_sol import MODEL, call_retry


def call_gpt(prompt: str):
    return call_retry(prompt, 16000)


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: deepread_gpt56.py <arxiv_id_or_url> <out_path>")
        return 2
    target, out = sys.argv[1:]
    if re.match(r"^https?://", target):
        text = source.fetch_url_text(target)
        src, aid = target, ""
    else:
        text = source.fetch_text(target)
        src, aid = f"https://arxiv.org/abs/{target}", target
    if len(text) < 4000:
        print(f"[{target}] no full text fetched ({len(text)} chars)")
        return 2
    prompt = source.build_prompt(src, text)
    for attempt in (1, 2):
        try:
            markdown, usage = call_gpt(prompt)
            markdown = source._clean(markdown)
            if len(markdown) > 1000:
                open(out, "w", encoding="utf-8").write(markdown)
                if aid:
                    source._unhide(aid)
                print(
                    f"[{target}] {MODEL} ok: {len(markdown)} chars | "
                    f"usage={usage.get('prompt_tokens')}→{usage.get('completion_tokens')} tok"
                )
                return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[{target}] {MODEL} attempt {attempt} failed: {exc}", flush=True)
            time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main())
