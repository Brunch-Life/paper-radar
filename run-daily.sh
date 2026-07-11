#!/bin/bash
# paper-radar 每日 routine — 串行版（小内存 server 友好）
#
# 逻辑：
#   1. aggregate.py 拿 candidates.json
#   2. 串行循环 top-K，GPT-5.6-Sol 写 deepreads/<id>.md
#   3. 全部完成后，用 GPT-5.6-Sol 跑 rank → ranked.md
#
# 模型请求流式串行执行，避免 VPS 内存和上游并发压力。
# 时间预算：18 paper × 3 min = ~54 min + 5 min Stage 1 + 5 min rank ≈ 65 min。

set -uo pipefail
LOG=/root/paper-radar/run-daily.log
exec >> "$LOG" 2>&1

echo ""
echo "==== $(date -Iseconds) START daily routine ===="

# 周末跳过 (arxiv 周五晚 ET 是本周最后一个 announce, 周六/日 cron 抓不到新东西)
# date +%u: 1=Mon, ..., 6=Sat, 7=Sun. 强制运行用 FORCE_RUN=1 覆盖.
DOW=$(date +%u)
if [[ -z "${FORCE_RUN:-}" && "$DOW" -ge 6 ]]; then
  echo "  weekend (DOW=$DOW: $(date +%A)), arxiv 不发布,skip. FORCE_RUN=1 可绕过."
  echo "==== $(date -Iseconds) END (weekend skip) ===="
  exit 0
fi

DATE=${RUN_DATE:-$(date +%F)}   # RUN_DATE=YYYY-MM-DD for backfill
DIGESTS=${DIGESTS:-/root/code/paper_reading_walkstream/digests}   # env-overridable for isolated test runs
DIGEST_DIR=$DIGESTS/$DATE
DEEPREADS_DIR=$DIGEST_DIR/deepreads
K=${K:-18}                  # K papers, override via env
BUDGET_PER_PAPER=${BUDGET_PER_PAPER:-2.00}

mkdir -p "$DEEPREADS_DIR"

# ===== Stage 1 =====
cd /root/paper-radar
if [[ -n "${REUSE_CANDIDATES:-}" && -f "$DIGEST_DIR/candidates.json" ]]; then
  echo "  Stage 1: reuse existing candidates.json (REUSE_CANDIDATES=1)"
else
  PAPER_RADAR_DIGESTS_DIR=$DIGESTS \
  .venv/bin/python scripts/aggregate.py \
    --date "$DATE" --days 2 --min-score 2 --max-out 80 || exit 1
fi

CAND_JSON=$DIGEST_DIR/candidates.json
if [[ ! -f "$CAND_JSON" ]]; then
  echo "  no candidates.json written — abort"
  exit 1
fi
N_CAND=$(/root/paper-radar/.venv/bin/python -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$CAND_JSON")
echo "  candidates: $N_CAND"
[[ $N_CAND -eq 0 ]] && { echo "  zero candidates, exit OK"; exit 0; }

# ===== Stage 2 — serial deep-reads (GPT-5.6-Sol, full pre-fetched text) =====
# API credentials for GPT generation/review/ranking.
set -a; . /root/paper-radar/deepread.env; set +a

# Top-K arxiv IDs sorted by score desc
TOPK_IDS=$(.venv/bin/python <<PY
import json
papers = json.load(open("$CAND_JSON"))
papers.sort(key=lambda p: -p.get("score", 0))
for p in papers[:$K]:
    print(p["arxiv_id"])
PY
)

i=0
for AID in $TOPK_IDS; do
  i=$((i+1))
  OUT=$DEEPREADS_DIR/$AID.md
  if [[ -f "$OUT" && $(wc -c < "$OUT") -gt 1000 ]]; then
    echo "  [$i/$K] $AID — deepread exists, skip generation"
    if [[ ! -f "${OUT%.md}.review.json" ]]; then
      echo "  [$i/$K] $AID — review missing, generating"
      REVIEW_MODEL=gpt-5.6-sol timeout 600 .venv/bin/python review_gpt.py "$AID" "$OUT" "${OUT%.md}.review.json" 2>&1 | tail -1 || true
    fi
    continue
  fi
  echo "  [$i/$K] $AID — running ($(date +%H:%M:%S))"
  DEGRADED=0

  # Primary: GPT-5.6-Sol reads the server-prefetched full paper text.
  timeout 1200 .venv/bin/python deepread_gpt56.py "$AID" "$OUT" 2>&1 | tail -3

  # No full text means no trustworthy deep-read; leave it absent instead of
  # silently falling back to the unavailable Claude browsing path.
  if [[ ! -f "$OUT" || $(wc -c < "$OUT" 2>/dev/null || echo 0) -lt 1000 ]]; then
    DEGRADED=1
    echo "  [$i/$K] $AID — no full-text GPT output; skip instead of browse fallback"
  fi

  # splice the paper's main arXiv figure into the deep-read (covers both paths)
  if [[ -f "$OUT" && $(wc -c < "$OUT" 2>/dev/null || echo 0) -gt 1000 ]]; then
    .venv/bin/python add_figure.py "$AID" "$OUT" 2>&1 | tail -1
    # record degraded (browse-fallback) state so the frontend can flag it
    if [[ "$DEGRADED" -eq 1 ]]; then : > "${OUT%.md}.degraded"; else rm -f "${OUT%.md}.degraded"; fi
  fi

  # post-generation audit: score + verdict sidecar (review card at article top)
  if [[ -f "$OUT" && $(wc -c < "$OUT" 2>/dev/null || echo 0) -gt 1000 && ! -f "${OUT%.md}.review.json" ]]; then
    REVIEW_MODEL=gpt-5.6-sol timeout 600 .venv/bin/python review_gpt.py "$AID" "$OUT" "${OUT%.md}.review.json" 2>&1 | tail -1 || true
  fi

  # review→revise loop (方案 C): score<7.5 且有具体 issues → 对照原文定向修订 → 重新审核。
  # 只跑一轮（新 review 直接覆盖），降级(browse)精读跳过——没有全文可对照。
  RJ=${OUT%.md}.review.json
  if [[ -f "$RJ" && -f "$OUT" && ! -f "${OUT%.md}.degraded" ]]; then
    NEEDS_REVISE=$(.venv/bin/python -c "import json,sys;d=json.load(open(sys.argv[1]));print(1 if float(d.get('score',10))<7.5 and d.get('issues') else 0)" "$RJ" 2>/dev/null || echo 0)
    if [[ "$NEEDS_REVISE" == "1" ]]; then
      echo "  [$i/$K] $AID — review<7.5, targeted revision ($(date +%H:%M:%S))"
      # pipefail 使管道继承 revise 的退出码：只有 exit 0（真的改写了）才重新审核
      if timeout 1200 .venv/bin/python revise_gpt56.py "$AID" "$OUT" "$RJ" 2>&1 | tail -2; then
        REVIEW_MODEL=gpt-5.6-sol timeout 600 .venv/bin/python review_gpt.py "$AID" "$OUT" "$RJ" 2>&1 | tail -1 || true
      fi
    fi
  fi

  # tiny pause for memory to release
  sleep 3
done

# ===== Stage 3 — rank by quality threshold (宁缺毋滥) → ranked.md =====
echo "  Stage 3: ranking (quality-thresholded, no fixed count)…"
RANK_PROMPT="Read all .md files under $DEEPREADS_DIR/ . First, drop any with \"建议跳过\" in section 1.

Then for each remaining paper, score it on 5 dims (each 1-5):
  stack_fit       — Franka FR3 / ManiSkill3 / RLinf / VLA fine-tune / 真机 RL 耦合度
  insight_density — 第 6 节有几条具体可借用？是否非平凡？
  claim_strength  — 证据是否过硬（真机 / 多 seed / 强 baseline）？
  novelty         — 真的新还是 A+B+C？
  timing          — 半年内可执行 RQ？

Sum each (max 25).

**只包含 sum ≥ 17 的论文**（即平均每维 ≥3.4 / 5；这是宁缺毋滥的硬门槛——不要为了凑数把质量低的塞进来）。

如果通过门槛的有 0 篇，ranked.md 只写 header + 一句 \"今天没值得读的，跳过\"。
如果通过 N 篇，按 sum 降序排列，全部纳入（无上限）。**不要强行凑 10**。

Write $DIGEST_DIR/ranked.md with this exact format:

# Paper Radar — $DATE

今天 N 篇值得读，按评分序。每篇直接是 paper-deep-read 6-section 全文。

---

# [#1 score=NN] <Title>
<full content of the deepread>

---
# [#2 score=NN] <Title>
...

The wrapper format \`# [#N score=NN] <title>\` is required — MCP server parses it.

After writing, just say done."

timeout 1800 .venv/bin/python rank_gpt56.py \
  --date "$DATE" --deepreads "$DEEPREADS_DIR" --out "$DIGEST_DIR/ranked.md" 2>&1 | tail -10

# Sanity check: 如果 rank 没写 ranked.md,显式记日志
if [[ ! -f "$DIGEST_DIR/ranked.md" ]]; then
  echo "  ⚠️ Stage 3 完成但 ranked.md 未生成 - 检查上面 rank 输出"
fi

# 推送(若配置了 notify.conf)
/root/paper-radar/notify-daily.sh "$DATE" || true

echo "==== $(date -Iseconds) END ===="
