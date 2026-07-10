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


def _matches(tag: str, text: str) -> bool:
    term = re.sub(r"[-_/]+", " ", tag.lower()).strip()
    if len(term) < 4:
        return False
    pattern = r"\b" + r"[-\s_/]*".join(map(re.escape, term.split())) + r"\b"
    return bool(re.search(pattern, text, re.IGNORECASE))


def preference_bonus(title: str, abstract: str) -> tuple[float, list[str]]:
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
            if abs(weight) >= 0.25 and _matches(tag, text)]
    raw = sum(weight for _, weight in hits)
    bonus = MAX_BONUS * math.tanh(raw / 4.0)
    labels = [f"pref:{tag}:{weight:+.1f}" for tag, weight in
              sorted(hits, key=lambda pair: -abs(pair[1]))[:5]]
    return round(bonus, 2), labels
