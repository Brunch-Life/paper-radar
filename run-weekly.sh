#!/bin/bash
# 每周综述：读过去 7 天的 ranked deepreads → opus 综述 → digests/weekly/<date>.md
# claude 经 CRS relay（env 来自 daily.env）。cron：周日傍晚 JST。
set -uo pipefail
source /root/paper-radar/daily.env
LOG=/root/paper-radar/run-weekly.log
exec >> "$LOG" 2>&1

exec 9>/root/paper-radar/run-weekly.lock
flock -n 9 || { echo "$(date -Iseconds) another run-weekly holds the lock, skip"; exit 0; }

echo ""
echo "==== $(date -Iseconds) START weekly ===="

DIGESTS=/root/code/paper_reading_walkstream/digests
WEEKLY=$DIGESTS/weekly
mkdir -p "$WEEKLY"
DATE=$(date +%F)
OUT=$WEEKLY/$DATE.md

# 收集过去 ~10 天内最多 7 个有 ranked.md 的日子
TMP=$(mktemp)
n=0
for i in $(seq 0 11); do
  d=$(date -d "-$i day" +%F 2>/dev/null || date -v-"${i}"d +%F)
  rk="$DIGESTS/$d/ranked.md"
  if [ -f "$rk" ]; then
    { echo "===== $d ====="; cat "$rk"; echo ""; } >> "$TMP"
    n=$((n + 1))
  fi
  [ $n -ge 7 ] && break
done
echo "  collected $n daily ranked.md"
if [ $n -eq 0 ]; then
  echo "  no ranked.md in window, skip"
  echo "==== $(date -Iseconds) END (empty) ===="
  rm -f "$TMP"; exit 0
fi

WEEK_TEXT=$(head -c 400000 "$TMP")
rm -f "$TMP"

PROMPT="你是一位资深具身智能/强化学习研究员，在帮一位清华本部 PhD 一年级学生（真机 Franka FR3 + ManiSkill3 + RLinf，VLA fine-tune，大规模真机强化学习；GitHub https://github.com/Brunch-Life/）做本周论文综述。

下面是本周（过去 7 天）每日精读榜单的全文（每篇是 paper-deep-read 6-section）。产出一份**本周综述**，严格 markdown，结构如下：

# 本周综述 $DATE

## 一、本周主线
把这一周的论文归纳成 2-4 条研究主线。每条：一句话主线 + 涉及哪几篇（带标题）+ 这条线在往哪走。敢下判断，不要罗列摘要。

## 二、最该追的方向（给这位学生）
结合他的 setting，2-3 条：哪个方向/方法值得他这周深挖或复现，为什么，具体到能落地的下一步。

## 三、谁在动 / 值得盯
本周最强的 1-2 篇 + 一句话为什么；以及哪些 lab/作者在密集出货。

## 四、一句话本周总结

写作要求：**生造概念/方法名/领域黑话第一次出现，先给一句白话解释再用术语**（如「多银行事件记忆」先说\"按时间粒度分层的外部存储\"），别堆黑话让人猜。
只输出这份 markdown，不要前后多余的话。

# 本周每日榜单全文：
$WEEK_TEXT"

echo "  running opus synthesis…"
SYN=$(mktemp)
echo "$PROMPT" | timeout 900 claude --print --model opus --allowedTools "Read" --max-budget-usd 5.00 > "$SYN" 2>>"$LOG"
if [ -s "$SYN" ] && [ "$(wc -c <"$SYN")" -gt 200 ]; then
  mv "$SYN" "$OUT"
  echo "  wrote $OUT ($(wc -c <"$OUT") bytes)"
else
  echo "  ⚠️ synthesis empty/failed"
  rm -f "$SYN"
fi
echo "==== $(date -Iseconds) END weekly ===="
