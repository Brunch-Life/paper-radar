#!/usr/bin/env python3
"""gpt-5.5 as a BLIND judge of two deep-reads (版本一 vs 版本二).

Reads /root/paper-radar/cmp/<id>.<ver1>.md as 版本一 and <ver2>.md as 版本二,
asks gpt-5.5 to score them blind, writes <id>.judge_gpt.txt with the blind map
recorded on the first line.

Usage: judge_gpt.py <arxiv_id> <ver1-model>   # ver1-model in {sonnet, gpt}
Env: RELAY_BASE, GPT_KEY (codex-bound cr_ key)
"""
import json
import os
import sys
import urllib.request

RELAY = os.environ.get("RELAY_BASE", "http://127.0.0.1:3000/api").rstrip("/")
GPT_KEY = os.environ["GPT_KEY"]
OUT = "/root/paper-radar/cmp"

PROMPT = """你是论文精读质量的盲评专家。下面是两份对**同一篇论文**做的「6-section 深度精读」。读者是清华本部的真机机器人 RL/VLA 博士生（真机 Franka FR3 + ManiSkill3 + RLinf 异步分布式训练 + π0/OpenVLA 微调 + 大规模真机强化学习；GitHub https://github.com/Brunch-Life/）。你**不知道**每份是哪个模型写的，请纯按质量盲评，不要去猜作者。

读者有一条明确要求：**别整黑话**——生造概念/小众术语必须配一句白话解释。

请就下面 6 个维度逐一比较【版本一】和【版本二】，每维判出胜者（版本一 / 版本二 / 平手）并给**一句具体理由**（要点到具体段落或具体说法，不要空泛）：
1. The Move 抓得准不准（有没有真讲清这篇的核心"动作"，而不是复述 abstract）
2. 挑刺深度 + 有没有给出针对「真机 Franka / RLinf 异步训练」这个**具体场景**的失败模式
3. 对读者可落地的启发（能不能真指导他下一步做什么）
4. 证据具体性 + 有没有看起来像**编造**的数字/事实
5. 白话 vs 黑话（是否符合"别整黑话"）
6. 格式与可读性

然后给：
- 总体哪一版更好、置信度（高/中/低）、一句话总账。
- 有没有哪一版读起来像「通用编程助手 / Codex 那类工具」写的、有没有跑偏或答非所问的痕迹（有就指出是哪版、在哪）。

最后**单独一行**只输出其一：`总体优胜：版本一` / `总体优胜：版本二` / `总体优胜：平手`。

# ===== 版本一 =====
{v1}

# ===== 版本二 =====
{v2}
"""


def call_gpt(prompt):
    url = RELAY.replace("/api", "/openai", 1) + "/v1/responses"
    body = json.dumps({"model": "gpt-5.5", "stream": True, "max_output_tokens": 16000,
                       "input": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + GPT_KEY, "content-type": "application/json"})
    txt, final = "", ""
    with urllib.request.urlopen(req, timeout=400) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            p = line[5:].strip()
            if not p or p == "[DONE]":
                continue
            try:
                e = json.loads(p)
            except Exception:
                continue
            t = e.get("type", "")
            if t == "response.output_text.delta":
                txt += e.get("delta", "")
            elif t == "response.output_text.done":
                final = e.get("text", "") or final
    return (final or txt).strip()


def main():
    aid = sys.argv[1]
    ver1 = sys.argv[2]
    ver2 = "gpt" if ver1 == "sonnet" else "sonnet"
    v1 = open(f"{OUT}/{aid}.{ver1}.md").read()
    v2 = open(f"{OUT}/{aid}.{ver2}.md").read()
    out = call_gpt(PROMPT.format(v1=v1, v2=v2))
    with open(f"{OUT}/{aid}.judge_gpt.txt", "w") as f:
        f.write(f"[blind map] 版本一={ver1}  版本二={ver2}\n\n{out}\n")
    print(f"[{aid}] gpt judge ok: {len(out)} chars (版本一={ver1})", flush=True)


if __name__ == "__main__":
    main()
