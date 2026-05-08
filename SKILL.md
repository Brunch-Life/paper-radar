---
name: paper-radar
description: Daily paper triage pipeline. Aggregates candidates from arXiv (cs.RO/cs.LG/cs.AI/cs.CV) + HuggingFace Daily Papers + 121-account X watchlist author-boost, then invokes paper-deep-read on each, then synthesizes a top-10 fast-read list. Use when user says "扫论文", "今天的 paper radar", "today's papers", "/paper-radar", "看看今天值得读什么", or wants the curated daily reading list.
---

# Paper Radar — Daily Reading List

A 3-stage pipeline that takes the firehose of new arXiv submissions and surfaces today's must-read papers for a PhD student in real-robot RL post-training (Franka FR3 + ManiSkill3 + RLinf), VLA fine-tuning (π0 / OpenVLA family), and world model + RL.

```
arXiv (cs.RO/cs.LG/cs.AI/cs.CV) ─┐
HuggingFace Daily Papers ────────┼─→ aggregate.py ─→ candidates.json (~50-100)
121 X watchlist (author boost) ──┘                        │
                                                          ↓
                              Stage 2: paper-deep-read on each (parallel Agent calls)
                              Skill auto-skips obvious nos at section 1
                                                          │
                                                          ↓
                              Stage 3: cat top-10 deepreads/*.md → ranked.md
```

## Skill layout (everything is bundled here)

```
~/.claude/skills/paper-radar/
├── SKILL.md                          ← this file
├── README.md                         ← human-readable doc
├── scripts/
│   ├── aggregate.py                  ← Stage 1 (arXiv + HF + author boost)
│   ├── setup.sh                      ← one-time venv setup
│   └── refresh_watchlist.sh          ← weekly Scweet refresh (optional)
├── data/                             ← reference data (small JSON)
│   ├── following.json                ← 1206 of @gaofeng220's followings
│   ├── relevant.json                 ← 535 filtered for embodied AI
│   ├── author_boost.json             ← 132 weighted authors (Stage 1 input)
│   ├── rt_authors.json               ← 50 RT signal handles
│   ├── tweets.json                   ← 60 recent tweets (signal)
│   └── FOLLOW_LIST.md                ← human-readable watchlist
├── digests/                          ← symlinks to ~/code/paper_reading_walkstream/digests/
│   ├── YYYY-MM-DD          → ~/code/paper_reading_walkstream/digests/YYYY-MM-DD
│   └── annual-YYYY-MM-DD   → ~/code/paper_reading_walkstream/digests/annual-YYYY-MM-DD
│
│   Real outputs live at ~/code/paper_reading_walkstream/digests/<tag>/:
│     ├── candidates.json
│     ├── candidates.md
│     ├── deepreads/<arxiv_id>.md     ← Stage 2 outputs
│     ├── ranked.md                   ← daily Stage 3 deliverable
│     ├── annual_review.md            ← annual concat deliverable
│     └── top5.md                     ← annual TOP 5 highlight
│   Override location with PAPER_RADAR_DIGESTS_DIR=/path env var.
├── requirements.txt
└── .venv/                            ← created by setup.sh, gitignored
```

All paths in this skill are absolute (`~/.claude/skills/paper-radar/...`)—the project no longer depends on `~/code/paper-radar/`.

## Trigger

User says any of: "扫论文", "今天的 paper radar", "today's papers", "/paper-radar", "看看今天值得读什么", "做今天的论文雷达". Or pastes a date and asks "show me that day's digest".

## Steps

### Step 0 — First-run setup (skip if `.venv/` exists)

```bash
bash ~/.claude/skills/paper-radar/scripts/setup.sh
```

This creates `.venv/` inside the skill dir and installs `arxiv`, `feedparser`, `requests`, `Scweet`. Idempotent.

### Step 1 — Aggregate (Python script)

```bash
SKILL=~/.claude/skills/paper-radar
DIGEST_DATE=${1:-$(date +%F)}
DIGEST_DIR=$SKILL/digests/$DIGEST_DATE

if [[ ! -f $DIGEST_DIR/candidates.json ]]; then
  $SKILL/.venv/bin/python $SKILL/scripts/aggregate.py \
    --date $DIGEST_DATE --days 2 --min-score 2 --max-out 80
fi
```

Tell the user how many candidates were generated.

### Step 2 — Deep-read top K (parallel Agent calls)

Read `$SKILL/digests/<date>/candidates.json`. **Default K=18** —— 这是 over-recall 数，给 deep-read 的"建议跳过"机制留 buffer。最终输出 **top 10**（约 50% 通过率）。配置：用户可以说 "扫前 30 篇" → K=30。

Sort by `score` descending. For each of the top K, spawn an Agent in parallel (single message with multiple Agent tool calls). Each Agent gets the prompt below.

**Agent prompt template:**

```
Fetch this arXiv paper and produce a structured deep read in the same 6-section format the paper-deep-read skill uses. Output in 中文.

URL: <abs_url>
Title: <title>
Abstract preview: <abstract first 300 chars>

PRE-LOADED CONTEXT — the user is a Tsinghua Shenzhen PhD year-1 working on:
- Real-robot RL post-training (Franka FR3 + ManiSkill3 + RLinf, 500-1000 Hz control)
- VLA fine-tuning (π0 / OpenVLA family)
- World model + RL

REQUIRED OUTPUT (strict 6 sections):
1. 一句话定位 (≤30字) — 子领域、相对于谁、做了什么。
   ⚠️ 如果论文不值得深读，section 1 后写"**建议跳过**：<一句话理由>"并立即停止，不要硬凑后面 5 节。
2. The Move — 真正的关键动作（不是"本文提出了 X"这种描述层）
3. 证据强度 — setup / 最强数字或图 / 消融缺什么 / 是否真机
4. 挑刺模式 — 审稿人姿态。最危险假设、缺失 baseline、搬到 Franka FR3 + ManiSkill3 + RLinf 真机 RL setup 会在哪崩
5. 领域地图位置 — 继承哪 2-3 篇前作 / 反对哪 1-2 篇并行工作 / 下一年谁会推进
6. 对我的启发 (≥3 条) — 可借用的技术 / 值得跑的 RQ / 应避免的 pitfall

收尾："如果只读这篇一次"——一句话写出半年后该记住什么。

风格：中文、不复述 abstract、不用综述腔、敢下判断。

如果 fetch 失败（403/timeout），明确说"仅基于 abstract"，不要假装读过全文。
```

**Save each Agent's output to `$SKILL/digests/<date>/deepreads/<arxiv_id>.md`** (write file at the end of the agent run by passing back the markdown and the main session writes it).

Run K agents in **one message with K Agent tool blocks** for true parallelism.

### Step 3 — Rank for fast read

After all K agents return, read every deep-read markdown.

**Drop:** Any with "建议跳过" in section 1.

**Score the rest** by these dimensions (1-5 each):
- **stack_fit** — 跟 Franka + ManiSkill3 + RLinf + VLA fine-tuning + 真机 RL 的耦合度
- **insight_density** — 第 6 节有几条具体可借用？是否非平凡？
- **claim_strength** — 证据是否过硬（真机数据 / 多 seed / 强 baseline）
- **novelty** — 是否真的新，还是 A+B+C
- **timing** — 是否对应你近期能做的 RQ（半年内可执行）

**Pick top 10 by sum**（按 score 排序取前 10；如果 K 通过率低于 10 篇就有几篇出几篇）。

**输出格式（重要）**：用户偏好极简——`ranked.md` 是 top 10 篇 deep-read 6-section 全文的**直接拼接**，**不要 tier 排名、不要 take-home 总结、不要 pipeline 元数据、不要包装**。

```bash
cd $SKILL/digests/<date>
cat > ranked.md <<HEADER
# Paper Radar — <date>

今天 10 篇值得读，按评分序。每篇直接是 paper-deep-read 的 6-section 全文。

---

HEADER
for aid in <top_arxiv_ids>; do
  cat deepreads/${aid}.md >> ranked.md
  echo "" >> ranked.md
  echo "---" >> ranked.md
  echo "" >> ranked.md
done
```

显示 `ranked.md` 时直接 `cat` 全文给用户，不要二次摘要。

**绝对不要做**：tier B/C/D/E 列表、"今日时间分配建议"表、"3 条 take-home 洞察"、pipeline self-reflection——这些都被用户明确拒绝过。

## Configuration / common variants

- "前 N 篇" → K=N (default 18, max 50)
- "只看 VLA" → filter candidates by `topic_labels` containing 'VLA' or 'VLA-named-model'
- "只看 RL" → filter by `RL-post-train` or `real-robot-RL`
- "重新跑" → delete digest dir and rerun
- "看 X 月 Y 日" → pass --date

## When NOT to use

- User asks about ONE specific paper they pasted → use `paper-deep-read` directly, not this pipeline
- User asks for a literature survey → use `research-lit` or `arxiv` skills
- User has zero new candidates today (rare) → just say "今天 arXiv 里没找到值得读的，建议歇一天"

## Failure modes & honest reporting

- **arXiv 429**: aggregator has retry but if it still fails after 5 attempts, fall back to HF Daily only and report this in the output
- **Agent fetch fails**: each agent's prompt instructs it to admit "仅基于 abstract" — surface this in ranked.md
- **All top K skipped**: report "今天 K=18 全被淘汰" and suggest re-running with K=30 or relaxing min-score

## Refreshing the X watchlist (optional, weekly)

The 121-account watchlist gradually drifts as the senior researcher follows new people. To refresh:

```bash
SCWEET_AUTH_TOKEN=<token from x.com cookies> \
  bash ~/.claude/skills/paper-radar/scripts/refresh_watchlist.sh
```

Token: x.com → DevTools (F12) → Application → Cookies → x.com → `auth_token` value. **Revoke the session at x.com → Settings → Sessions** after the refresh completes.

## Cost note

Each run = 1 aggregator (cheap) + K parallel deep-reads (each ~$0.05-0.10) + 1 synthesis. K=18 ≈ $1-2/day. Worth it if it saves you 1 hour of triage.
