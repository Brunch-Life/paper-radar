#!/bin/bash
# paper-radar 每日 routine — 串行版（小内存 server 友好）
#
# 逻辑：
#   1. aggregate.py 拿 candidates.json
#   2. 串行循环 top-K，每个 paper 起 1 个 claude --print 进程
#      跑 paper-deep-read，写 deepreads/<id>.md
#   3. 全部完成后，再起 1 个 claude --print 跑 rank → ranked.md
#
# 内存峰值 ≤ 1 个 claude 进程 ~800 MB，1 GB RAM + 4 GB swap 完全够。
# 时间预算：18 paper × 3 min = ~54 min + 5 min Stage 1 + 5 min rank ≈ 65 min。

set -uo pipefail
LOG=/root/paper-radar/run-daily.log
exec >> "$LOG" 2>&1

echo ""
echo "==== $(date -Iseconds) START daily routine ===="

DATE=$(date +%F)
DIGESTS=/root/code/paper_reading_walkstream/digests
DIGEST_DIR=$DIGESTS/$DATE
DEEPREADS_DIR=$DIGEST_DIR/deepreads
K=${K:-18}                  # K papers, override via env
BUDGET_PER_PAPER=${BUDGET_PER_PAPER:-0.30}

mkdir -p "$DEEPREADS_DIR"

# ===== Stage 1 =====
cd /root/paper-radar
PAPER_RADAR_DIGESTS_DIR=$DIGESTS \
.venv/bin/python scripts/aggregate.py \
  --date "$DATE" --days 2 --min-score 2 --max-out 80 || exit 1

CAND_JSON=$DIGEST_DIR/candidates.json
if [[ ! -f "$CAND_JSON" ]]; then
  echo "  no candidates.json written — abort"
  exit 1
fi
N_CAND=$(/root/paper-radar/.venv/bin/python -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" "$CAND_JSON")
echo "  candidates: $N_CAND"
[[ $N_CAND -eq 0 ]] && { echo "  zero candidates, exit OK"; exit 0; }

# ===== Stage 2 — serial deep-reads =====
SKILL_TEXT=$(cat /root/.claude/skills/paper-deep-read/SKILL.md)

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
    echo "  [$i/$K] $AID — already exists, skip"
    continue
  fi
  echo "  [$i/$K] $AID — running ($(date +%H:%M:%S))"

  PROMPT="Run paper-deep-read skill verbatim on https://arxiv.org/abs/$AID

# Paper Deep-Read SKILL (full text):
$SKILL_TEXT

学生：清华深圳 PhD year-1, 真机 Franka FR3 + ManiSkill3 + RLinf, VLA fine-tune (π0/OpenVLA), world model + RL. 500-1000 Hz 控制频率。

ARGUMENTS: https://arxiv.org/abs/$AID

After producing the 6-section markdown, Write the entire output (just the markdown, no extra wrapper) to: $OUT

Then exit."

  echo "$PROMPT" | timeout 600 claude --print --model sonnet \
    --allowedTools "Read Write WebFetch WebSearch Bash" \
    --max-budget-usd "$BUDGET_PER_PAPER" 2>&1 | tail -3

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

echo "$RANK_PROMPT" | timeout 600 claude --print --model sonnet \
  --allowedTools "Read Write Bash" \
  --max-budget-usd 2.00 2>&1 | tail -3

echo "==== $(date -Iseconds) END ===="
