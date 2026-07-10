#!/usr/bin/env python3
"""gpt-5.5 as a BLIND 3-way judge: 版本一/二/三, rank best->worst.

Usage: judge_gpt3.py <arxiv_id> <m1> <m2> <m3>   # models for 版本一/二/三
Env: RELAY_BASE, GPT_KEY (codex-bound cr_ key)
Out: /root/paper-radar/cmp/<id>.judge3_gpt.txt
"""
import json
import os
import sys
import urllib.request

RELAY = os.environ.get("RELAY_BASE", "http://127.0.0.1:3000/api").rstrip("/")
GPT_KEY = os.environ["GPT_KEY"]
OUT = "/root/paper-radar/cmp"

PROMPT = """你是论文精读质量的盲评专家。下面是对**同一篇论文**做的**三份**「6-section 深度精读」（版本一/二/三）。读者是清华本部真机机器人 RL/VLA 博士生（真机 Franka FR3 + ManiSkill3 + RLinf 异步分布式训练 + π0/OpenVLA 微调 + 大规模真机强化学习；GitHub https://github.com/Brunch-Life/），有一条硬要求：**别整黑话**——生造概念/小众术语必须配一句白话解释。你**不知道**每份是谁写的，纯按质量盲评，别猜作者。

就这 6 个维度，每维给出**从好到差的排名**（如「版本二 > 版本一 > 版本三」）+ 一句**具体**理由（点到具体段落/说法）：
1. The Move 抓得准（讲清核心动作而非复述 abstract）
2. 挑刺深度 + 针对「真机 Franka / RLinf 异步训练」的具体失败模式
3. 可落地启发（能否真指导下一步）
4. 证据具体性 + 有没有看起来像**编造**的数字/事实
5. 白话 vs 黑话
6. 格式可读性

然后：
- **总排名**（从好到差）+ 置信度（高/中/低）+ 一句话总账。
- 有没有哪一版像「通用编程助手/Codex」写的、或跑偏/编造，指出哪版哪里。

最后**单独一行**只输出：`总排名：版本X > 版本Y > 版本Z`。

# ===== 版本一 =====
{v1}

# ===== 版本二 =====
{v2}

# ===== 版本三 =====
{v3}
"""


def call_gpt(prompt):
    url = RELAY.replace("/api", "/openai", 1) + "/v1/responses"
    body = json.dumps({"model": "gpt-5.5", "stream": True, "max_output_tokens": 16000,
                       "input": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + GPT_KEY, "content-type": "application/json"})
    txt, final = "", ""
    with urllib.request.urlopen(req, timeout=500) as resp:
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
    m1, m2, m3 = sys.argv[2], sys.argv[3], sys.argv[4]
    v1 = open(f"{OUT}/{aid}.{m1}.md").read()
    v2 = open(f"{OUT}/{aid}.{m2}.md").read()
    v3 = open(f"{OUT}/{aid}.{m3}.md").read()
    out = call_gpt(PROMPT.format(v1=v1, v2=v2, v3=v3))
    with open(f"{OUT}/{aid}.judge3_gpt.txt", "w") as f:
        f.write(f"[blind] 一={m1} 二={m2} 三={m3}\n\n{out}\n")
    print(f"[{aid}] gpt 3-way ok: {len(out)} chars (一={m1} 二={m2} 三={m3})", flush=True)


if __name__ == "__main__":
    main()
