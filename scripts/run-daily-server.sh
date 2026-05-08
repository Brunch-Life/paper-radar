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

  echo "$PROMPT" | timeout 600 claude --print \
    --allowedTools "Read Write WebFetch WebSearch Bash" \
    --max-budget-usd "$BUDGET_PER_PAPER" 2>&1 | tail -3

  # tiny pause for memory to release
  sleep 3
done

# ===== Stage 3 — rank top 10 → ranked.md =====
echo "  Stage 3: ranking…"
RANK_PROMPT="Read all .md files under $DEEPREADS_DIR/ . Drop any with 建议跳过 in section 1. Score remaining by stack_fit + insight_density + claim_strength + novelty + timing (each 1-5), pick top 10 by sum.

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

echo "$RANK_PROMPT" | timeout 600 claude --print \
  --allowedTools "Read Write Bash" \
  --max-budget-usd 2.00 2>&1 | tail -3

echo "==== $(date -Iseconds) END ===="
