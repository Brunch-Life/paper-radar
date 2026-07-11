"""paper-radar user signals + personalized re-rank. Clean rewrite.

Signals (votes / bookmarks / hidden / clicks / dwell) live in Redis db2
(db0/db1 belong to CRS). The hardened FastAPI service (user `paperradar`,
ProtectSystem=strict, IPAddressAllow=127.0.0.1) cannot write the filesystem
but CAN reach localhost redis — hence redis for all mutable state.

Keys (all keyed by pid):
    pr:vote     hash  pid → "up" | "down"
    pr:saved    set   bookmarked pids
    pr:hidden   set   deleted/hidden pids (worker also writes this)
    pr:clicks   hash  pid → count
    pr:dwell    hash  pid → total ms
    pr:chat:*   owned by the worker (ask-this-paper history)

Recommendation is a hand-rolled, explainable tag/author-affinity model:
score(paper) = Σ affinity[tag] + 0.5·Σ affinity[author], with affinity learned
from 👍(+)/👎(−)/bookmark(+)/click+dwell(weak +) and squashed by tanh.
"""
from __future__ import annotations

import math
import json
import time
from typing import Optional

from paper_radar.preferences import _matches as _preference_matches

try:
    import redis
except Exception:  # pragma: no cover
    redis = None

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
_client_cache = None

K_VOTE = "pr:vote"
K_SAVED = "pr:saved"
K_HIDDEN = "pr:hidden"
K_CLICKS = "pr:clicks"
K_DWELL = "pr:dwell"
K_INTEREST = "pr:interest"  # hash pid → {score:1..5, ts:unix}


def _client():
    """Redis on db2, or None when unavailable (signals degrade gracefully)."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    if redis is None:
        return None
    try:
        c = redis.Redis(host="127.0.0.1", port=6379, db=2,
                        decode_responses=True, socket_timeout=1.5)
        c.ping()
        _client_cache = c            # redis-py reconnects on its own after blips
        return c
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------
def set_vote(pid: str, vote: Optional[str]) -> dict:
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        if vote in ("up", "down"):
            c.hset(K_VOTE, pid, vote)
        else:                        # None → clear (3-state toggle)
            c.hdel(K_VOTE, pid)
    except Exception:
        return {"ok": False}
    return {"ok": True, "arxiv_id": pid, "vote": vote if vote in ("up", "down") else None}


def set_save(pid: str, saved: bool) -> dict:
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        (c.sadd if saved else c.srem)(K_SAVED, pid)
    except Exception:
        return {"ok": False}
    return {"ok": True, "arxiv_id": pid, "saved": bool(saved)}


def set_hidden(pid: str, hidden: bool) -> dict:
    """Soft hide/restore (the hard delete lives in the worker)."""
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        (c.sadd if hidden else c.srem)(K_HIDDEN, pid)
    except Exception:
        return {"ok": False}
    return {"ok": True, "arxiv_id": pid, "hidden": bool(hidden)}


def record_click(pid: str) -> dict:
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        c.hincrby(K_CLICKS, pid, 1)
    except Exception:
        return {"ok": False}
    return {"ok": True}


def record_dwell(pid: str, ms) -> dict:
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        ms = max(0, min(int(ms), 3_600_000))    # clamp 0..1h
    except Exception:
        return {"ok": False}
    if ms:
        try:
            c.hincrby(K_DWELL, pid, ms)
        except Exception:
            return {"ok": False}
    return {"ok": True}


def set_interest(pid: str, score, tags=None) -> dict:
    """Persist an explicit 1–5 research-interest rating; null/0 clears it."""
    c = _client()
    if not c or not pid:
        return {"ok": False}
    try:
        if score in (None, "", 0, "0"):
            c.hdel(K_INTEREST, pid)
            value = None
        else:
            value = int(score)
            if not 1 <= value <= 5:
                return {"ok": False, "error": "score must be 1..5"}
            clean_tags = sorted({str(tag).strip().lower() for tag in (tags or [])
                                 if str(tag).strip()})[:30]
            c.hset(K_INTEREST, pid, json.dumps({
                "score": value, "ts": time.time(), "tags": clean_tags,
            }, ensure_ascii=False))
    except Exception:
        return {"ok": False}
    return {"ok": True, "pid": pid, "score": value}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
_EMPTY = {"votes": {}, "saved": [], "clicks": {}, "dwell": {}, "hidden": [],
          "interests": {},
          "available": False}
_purged = False


def get_signals() -> dict:
    c = _client()
    if not c:
        return dict(_EMPTY)
    global _purged
    if not _purged:   # one-time hygiene: drop "" garbage from an old bug
        try:
            c.hdel(K_VOTE, ""); c.srem(K_SAVED, "")
            c.hdel(K_CLICKS, ""); c.hdel(K_DWELL, ""); c.srem(K_HIDDEN, "")
            c.hdel(K_INTEREST, "")
        except Exception:
            pass
        _purged = True
    try:
        interests = {}
        for pid, raw in (c.hgetall(K_INTEREST) or {}).items():
            try:
                item = json.loads(raw)
                score = int(item.get("score", 0))
                if 1 <= score <= 5:
                    interests[pid] = {"score": score, "ts": float(item.get("ts", 0)),
                                      "tags": item.get("tags", [])}
            except Exception:
                continue
        return {
            "votes": c.hgetall(K_VOTE) or {},
            "saved": sorted(c.smembers(K_SAVED) or []),
            "clicks": {k: int(v) for k, v in (c.hgetall(K_CLICKS) or {}).items()},
            "dwell": {k: int(v) for k, v in (c.hgetall(K_DWELL) or {}).items()},
            "hidden": sorted(c.smembers(K_HIDDEN) or []),
            "interests": interests,
            "available": True,
        }
    except Exception:
        return dict(_EMPTY)


# ---------------------------------------------------------------------------
# Affinity / personalization
# ---------------------------------------------------------------------------
W_UP = 1.0
W_DOWN = -1.2
W_SAVE = 0.8
W_CLICK = 0.15            # per click, weak
DWELL_FULL_MS = 90_000    # dwell that counts as ~one weak positive
W_DWELL = 0.4             # max weak positive from dwell
W_INTEREST = 1.25         # each step away from neutral (3) is strong evidence
INTEREST_HALF_LIFE_DAYS = 180


def _engagement_weight(pid: str, clicks: dict, dwell: dict) -> float:
    w = 0.0
    if pid in clicks:
        w += W_CLICK * min(clicks[pid], 5)
    if pid in dwell:
        w += W_DWELL * min(dwell[pid] / DWELL_FULL_MS, 1.0)
    return w


def compute_affinity(paper_tags: dict, paper_authors: dict) -> dict:
    """Learn per-tag / per-author affinity from current signals.
    → {"tags": {tag: w}, "authors": {author: w}, "available": bool}"""
    sig = get_signals()
    if not sig["available"]:
        return {"tags": {}, "authors": {}, "available": False}
    votes, clicks, dwell = sig["votes"], sig["clicks"], sig["dwell"]

    tag_w: dict[str, float] = {}
    auth_w: dict[str, float] = {}

    def credit(pid: str, weight: float):
        for t in paper_tags.get(pid, []):
            tag_w[t] = tag_w.get(t, 0.0) + weight
        for a in paper_authors.get(pid, []):
            auth_w[a] = auth_w.get(a, 0.0) + weight * 0.5

    for pid, v in votes.items():                       # explicit
        credit(pid, W_UP if v == "up" else W_DOWN)
    now = time.time()
    for pid, item in sig.get("interests", {}).items():
        # 1→-2.5, 2→-1.25, 3→0, 4→+1.25, 5→+2.5; slowly decay old taste.
        age_days = max(0.0, (now - float(item.get("ts", now))) / 86400)
        decay = 0.5 ** (age_days / INTEREST_HALF_LIFE_DAYS)
        weight = (int(item["score"]) - 3) * W_INTEREST * decay
        chosen = item.get("tags", [])
        if chosen:
            # Structured choices are precise: only learn what the user picked.
            for term in chosen:
                tag_w[term] = tag_w.get(term, 0.0) + weight
        else:
            # A bare rating retains the v1 behavior for backward compatibility.
            credit(pid, weight)
    for pid in sig["saved"]:
        credit(pid, W_SAVE)
    for pid in set(list(clicks) + list(dwell)):        # implicit, skip downvoted
        if votes.get(pid) == "down":
            continue
        credit(pid, _engagement_weight(pid, clicks, dwell))

    # squash so no single tag dominates
    return {
        "tags": {t: math.tanh(w / 3.0) for t, w in tag_w.items()},
        "authors": {a: math.tanh(w / 3.0) for a, w in auth_w.items()},
        "available": True,
    }


def personalize(papers: list, paper_tags: dict, paper_authors: dict,
                base_key: str = "base", lam: float = 1.0) -> list:
    """Re-rank papers by base score + λ·affinity; annotate pscore/affinity/why."""
    aff = compute_affinity(paper_tags, paper_authors)
    tw, aw = aff["tags"], aff["authors"]
    out = []
    for p in papers:
        pid = p.get("pid") or p.get("arxiv_id", "")
        tags = paper_tags.get(pid, p.get("tags", []))
        authors = paper_authors.get(pid, p.get("authors", []))
        s, contribs = 0.0, []
        for t in tags:
            if t in tw and abs(tw[t]) > 1e-3:
                s += tw[t]
                contribs.append((t, tw[t]))
        searchable = (f"{p.get('title', '')} {p.get('tagline', '')} "
                      f"{' '.join(tags)}").lower().replace('-', ' ')
        for term, weight in tw.items():
            if term not in tags and _preference_matches(term, searchable, authors):
                s += weight
                contribs.append((term, weight))
        for a in authors:
            if a in aw and abs(aw[a]) > 1e-3:
                s += 0.5 * aw[a]
        base = float(p.get(base_key, 0) or 0)
        why = sorted(contribs, key=lambda x: -abs(x[1]))[:3]
        out.append({**p, "pscore": base + lam * s,
                    "affinity": round(s, 3),
                    "why": [{"tag": t, "w": round(w, 2)} for t, w in why]})
    out.sort(key=lambda x: -x["pscore"])
    return out


# ---------------------------------------------------------------------------
# Annotations (highlighter + margin notes), keyed by pid
# ---------------------------------------------------------------------------
# pr:annot:<pid> → json list of {id, quote, note, ts}. The quote is the exact
# selected article text; the frontend re-anchors it by text search on render.
import hashlib as _hashlib
import json as _json
import time as _time

K_ANNOT_PREFIX = "pr:annot:"


def _annot_key(pid: str) -> str:
    return K_ANNOT_PREFIX + pid


def list_annotations(pid: str) -> dict:
    c = _client()
    if not c or not pid:
        return {"pid": pid, "annotations": []}
    try:
        raw = c.get(_annot_key(pid))
        return {"pid": pid, "annotations": _json.loads(raw) if raw else []}
    except Exception:
        return {"pid": pid, "annotations": []}


def _save_annotations(c, pid: str, items: list) -> None:
    c.set(_annot_key(pid), _json.dumps(items[-200:], ensure_ascii=False))


def add_annotation(pid: str, quote: str, note: str = "") -> dict:
    quote = (quote or "").strip()[:2000]
    note = (note or "").strip()[:2000]
    c = _client()
    if not c or not pid or not quote:
        return {"ok": False}
    entry = {
        "id": _hashlib.sha1(f"{quote}{_time.time()}".encode()).hexdigest()[:8],
        "quote": quote, "note": note, "ts": _time.time(),
    }
    try:
        items = list_annotations(pid)["annotations"]
        items.append(entry)
        _save_annotations(c, pid, items)
    except Exception:
        return {"ok": False}
    return {"ok": True, "annotation": entry}


def update_annotation(pid: str, aid: str, note: str) -> dict:
    c = _client()
    if not c or not pid or not aid:
        return {"ok": False}
    try:
        items = list_annotations(pid)["annotations"]
        for it in items:
            if it.get("id") == aid:
                it["note"] = (note or "").strip()[:2000]
                _save_annotations(c, pid, items)
                return {"ok": True, "annotation": it}
    except Exception:
        pass
    return {"ok": False}


def delete_annotation(pid: str, aid: str) -> dict:
    c = _client()
    if not c or not pid or not aid:
        return {"ok": False}
    try:
        items = list_annotations(pid)["annotations"]
        kept = [it for it in items if it.get("id") != aid]
        _save_annotations(c, pid, kept)
        return {"ok": True, "removed": len(items) - len(kept)}
    except Exception:
        return {"ok": False}
