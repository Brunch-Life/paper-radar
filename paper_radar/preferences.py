"""Explicit-interest preference bonus for new candidate papers.

Ratings are stored by the web API in Redis db2 as JSON records containing a
1–5 score, timestamp, and the rated paper's tags. This module is deliberately
optional: cron scoring behaves exactly as before when Redis is unavailable or
no ratings exist.
"""
from __future__ import annotations

import json
import math
import re
import time

K_INTEREST = "pr:interest"
HALF_LIFE_DAYS = 180
MAX_BONUS = 6.0


def _records() -> list[dict]:
    try:
        import redis
        client = redis.Redis(host="127.0.0.1", port=6379, db=2,
                             decode_responses=True, socket_timeout=1.0)
        return [json.loads(raw) for raw in (client.hgetall(K_INTEREST) or {}).values()]
    except Exception:
        return []


def _feature(tag: str) -> tuple[str, str]:
    prefix, sep, value = tag.partition(":")
    return (prefix, value) if sep and prefix in {
        "author", "direction", "method", "experiment", "custom"
    } else ("term", tag)


def _matches(tag: str, text: str, authors: list[str]) -> bool:
    kind, value = _feature(tag)
    if kind == "author":
        wanted = re.sub(r"\s+", " ", value.lower()).strip()
        return any(re.sub(r"\s+", " ", author.lower()).strip() == wanted
                   for author in authors)
    structured = globals().get("_OPTION_PATTERNS", {}).get(f"{kind}:{value}")
    if structured:
        return bool(re.search(structured, text, re.I))
    term = re.sub(r"[-_/]+", " ", value.lower()).strip()
    if len(term) < 4:
        return False
    pattern = r"\b" + r"[-\s_/]*".join(map(re.escape, term.split())) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def preference_bonus(title: str, abstract: str,
                     authors: list[str] | None = None) -> tuple[float, list[str]]:
    """Return a bounded learned-interest bonus and explainable matched tags."""
    text = f"{title}\n{abstract}"
    now = time.time()
    weights: dict[str, float] = {}
    for item in _records():
        try:
            score = int(item.get("score", 3))
            age = max(0.0, (now - float(item.get("ts", now))) / 86400)
        except Exception:
            continue
        weight = (score - 3) * (0.5 ** (age / HALF_LIFE_DAYS))
        for tag in item.get("tags", []):
            tag = str(tag).strip().lower()
            if tag:
                weights[tag] = weights.get(tag, 0.0) + weight
    hits = [(tag, weight) for tag, weight in weights.items()
            if abs(weight) >= 0.25 and _matches(tag, text, authors or [])]
    raw = sum(weight for _, weight in hits)
    bonus = MAX_BONUS * math.tanh(raw / 4.0)
    labels = [f"pref:{tag}:{weight:+.1f}" for tag, weight in
              sorted(hits, key=lambda pair: -abs(pair[1]))[:5]]
    return round(bonus, 2), labels


_DIRECTION_LABELS = {
    "pi-family": "π0 / Physical Intelligence",
    "openvla-rt": "OpenVLA / RT 系列",
    "gr00t": "NVIDIA GR00T",
    "hot-vla-2026": "前沿 VLA",
    "rl-framework": "RL 训练框架",
    "VLA-or-equiv": "VLA / 通用机器人策略",
    "real-robot-RL": "真机强化学习",
    "post-train-policy": "策略后训练",
    "diffusion-flow-policy": "Diffusion / Flow Policy",
    "sim-platform": "机器人仿真平台",
    "sim2real": "Sim-to-Real",
    "imitation-data": "模仿学习与数据采集",
    "manipulation-modes": "机器人操作",
    "humanoid": "人形与运动控制",
    "lab-robots": "实验室机器人平台",
}

_METHODS = [
    ("flow-matching", "Flow Matching", r"\bflow[- ]?matching\b|\brectified flow\b|流匹配"),
    ("diffusion-policy", "Diffusion Policy", r"\bdiffusion polic|扩散策略"),
    ("vla", "VLA", r"\bVLA\b|vision[- ]language[- ]action|视觉语言动作"),
    ("rl-post-training", "RL Post-training", r"post[- ]?train|reinforcement learning|强化学习|\bPPO\b|\bGRPO\b"),
    ("temporal-memory", "时序记忆", r"memory|temporal|history|记忆|时序"),
    ("action-chunking", "Action Chunking", r"action chunk|动作块|chunked action"),
    ("3d-geometry", "3D 几何", r"\b3D\b|geometry|point cloud|几何|点云"),
    ("imitation-learning", "模仿学习", r"imitation learning|behavior cloning|模仿学习|行为克隆"),
    ("world-action-model", "世界动作模型", r"world action model|\bWAM\b|世界动作模型"),
]

_EXPERIMENTS = [
    ("real-robot", "真机实验", r"真机|real[- ]?robot|on[- ]?robot|Franka|Unitree|机器人实验"),
    ("simulation", "仿真实验", r"仿真|simulation|simulator|ManiSkill|LIBERO|MetaWorld|Isaac"),
    ("ablation", "消融实验", r"消融|ablation"),
    ("statistics", "多次试验 / 统计显著", r"multi[- ]?seed|多个种子|显著|p\s*[=<]|置信区间"),
    ("strong-baseline", "强基线对比", r"strong baseline|强基线|基线|baseline"),
    ("real-time", "实时与延迟", r"实时|延迟|latency|\bFPS\b|\bHz\b"),
    ("large-scale-data", "大规模数据", r"大规模|million|billion|百万|数据规模"),
    ("open-source", "开源与复现", r"开源|open[- ]?source|reproduc|复现"),
]

_DIRECTION_PATTERNS = {
    "pi-family": r"π0|pi[- ]?0|physical intelligence",
    "openvla-rt": r"OpenVLA|RT-[12X]",
    "gr00t": r"GR00T",
    "hot-vla-2026": r"VLA|robot policy|机器人策略",
    "rl-framework": r"RLinf|verl|TRL|LeRobot",
    "vla-or-equiv": r"\bVLA\b|vision[- ]language[- ]action|robot foundation model",
    "real-robot-rl": r"real[- ]?robot|on[- ]?robot|真机.*强化学习|强化学习.*真机",
    "post-train-policy": r"post[- ]?train|fine[- ]?tun.*policy|策略后训练",
    "diffusion-flow-policy": r"diffusion polic|flow[- ]?match.*polic|扩散策略|流匹配",
    "sim-platform": r"ManiSkill|LIBERO|MetaWorld|Isaac|仿真|simulation",
    "sim2real": r"sim[- ]?to[- ]?real|sim2real",
    "imitation-data": r"imitation learning|behavior cloning|teleop|模仿学习|遥操作",
    "manipulation-modes": r"manipulation|grasp|bimanual|操作|抓取",
    "humanoid": r"humanoid|legged|人形|腿足",
    "lab-robots": r"Franka|FR3|Unitree|xArm|UR5",
}

_OPTION_PATTERNS = {
    **{f"direction:{key}": pattern for key, pattern in _DIRECTION_PATTERNS.items()},
    **{f"method:{key}": pattern for key, _label, pattern in _METHODS},
    **{f"experiment:{key}": pattern for key, _label, pattern in _EXPERIMENTS},
}


def interest_options(meta: dict, full_md: str) -> dict:
    """Build paper-specific, structured choices for the rating picker."""
    text = f"{meta.get('title', '')}\n{meta.get('tagline', '')}\n{full_md}"
    authors = [str(a).strip() for a in meta.get("authors", []) if str(a).strip()][:10]
    directions = []
    for tag in meta.get("tags", []):
        label = _DIRECTION_LABELS.get(tag, tag.replace("-", " "))
        directions.append({"value": f"direction:{tag.lower()}", "label": label})

    def inferred(kind: str, rules: list[tuple[str, str, str]]) -> list[dict]:
        return [{"value": f"{kind}:{key}", "label": label}
                for key, label, pattern in rules if re.search(pattern, text, re.I)]

    return {"pid": meta.get("pid", ""), "groups": [
        {"key": "author", "label": "作者", "options": [
            {"value": f"author:{author}", "label": author} for author in authors]},
        {"key": "direction", "label": "研究方向", "options": directions},
        {"key": "method", "label": "方法", "options": inferred("method", _METHODS)},
        {"key": "experiment", "label": "实验与证据", "options": inferred("experiment", _EXPERIMENTS)},
    ]}
