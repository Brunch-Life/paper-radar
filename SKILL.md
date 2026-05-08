---
name: paper-radar
description: Daily paper triage pipeline. Aggregates candidates from arXiv (cs.RO/cs.LG/cs.AI/cs.CV) + HuggingFace Daily Papers + 121-account X watchlist author-boost, then invokes paper-deep-read on each, then synthesizes a top-10 fast-read list. Use when user says "扫论文", "今天的 paper radar", "today's papers", "/paper-radar", "看看今天值得读什么", or wants the curated daily reading list.
---

# Paper Radar — Daily Reading List

A 3-stage pipeline that takes the firehose of new arXiv submissions and surfaces today's must-read papers for a PhD student in real-robot RL post-training (Franka FR3 + ManiSkill3 + RLinf), VLA fine-tuning (π0 / OpenVLA family), and world model + RL.

```
arXiv RSS (cs.RO/cs.LG/cs.AI/cs.CV) ─┐
HuggingFace Daily Papers ────────────┼─→ aggregate.py ─→ candidates.json (~50-100)
121 X watchlist (author boost) ──────┘                        │
                                                              ↓
                                  Stage 2: paper-deep-read on top K=18 (parallel Agent calls)
                                  Skill auto-skips obvious nos at section 1
                                                              │
                                                              ↓
                                  Stage 3: cat top-10 deepreads → ranked.md
                                                              │
                                                              ↓
                                  Cowork Live Artifact 自取（通过 paper-radar MCP server）
                                  本 skill 不再做任何主动推送
```

## Skill layout

```
~/.claude/skills/paper-radar/
├── SKILL.md                          ← this file
├── README.md
├── paper_radar/
│   ├── __init__.py
│   └── scoring.py                    ← Tier S/A/B/C 评分（single source of truth）
├── scripts/
│   ├── aggregate.py                  ← Stage 1（arxiv RSS + HF + author boost）
│   ├── annual_scan.py                ← past-365-day backfill
│   ├── mcp_server.py                 ← paper-radar MCP server（Cowork artifact 通过这个拉数据）
│   ├── setup.sh                      ← one-time venv setup
│   └── refresh_watchlist.sh          ← weekly Scweet refresh (optional)
├── data/
│   ├── author_boost.json             ← 132 weighted authors (Stage 1 input)
│   ├── following.json / relevant.json / rt_authors.json / tweets.json   ← refresh 时用
│   └── FOLLOW_LIST.md
├── digests/                          ← symlinks to ~/code/paper_reading_walkstream/digests/
├── requirements.txt
└── .venv/                            ← created by setup.sh, gitignored
```

Real outputs at `~/code/paper_reading_walkstream/digests/<tag>/`：
- `candidates.{json,md}` — Stage 1
- `deepreads/<arxiv_id>.md` — Stage 2
- `ranked.md` — Stage 3 daily deliverable
- `annual_review.md` + `top5.md` — annual scan deliverables

Override location with `PAPER_RADAR_DIGESTS_DIR=/path` env var.

## Trigger

User says any of: "扫论文", "今天的 paper radar", "today's papers", "/paper-radar", "看看今天值得读什么", "做今天的论文雷达". Or pastes a date and asks "show me that day's digest".

## Steps

### Step 0 — First-run setup (skip if `.venv/` exists)

```bash
bash ~/.claude/skills/paper-radar/scripts/setup.sh
```

Creates `.venv/` and installs `arxiv`, `feedparser`, `requests`, `Scweet`. Idempotent.

### Step 1 — Aggregate

```bash
SKILL=~/.claude/skills/paper-radar
DIGEST_DATE=${1:-$(date +%F)}

$SKILL/.venv/bin/python $SKILL/scripts/aggregate.py \
  --date $DIGEST_DATE --days 2 --min-score 2 --max-out 80
```

Output to `~/code/paper_reading_walkstream/digests/<date>/candidates.{json,md}`. Tell the user how many candidates were generated. If 0, stop here.

### Step 2 — Deep-read top K (parallel Agent calls)

Read `candidates.json`. **Default K=18** —— over-recall buffer for paper-deep-read 的"建议跳过" 出口；最终输出 top 10。配置：用户可以说"扫前 30 篇" → K=30。

Sort by `score` desc. Read `~/.claude/skills/paper-deep-read/SKILL.md` for the embed text.

Spawn K agents in **one message** with K Agent tool blocks for true parallelism. Each prompt embeds paper-deep-read SKILL.md verbatim (subagents can't directly invoke skills). Each agent should `Write` its output to `~/code/paper_reading_walkstream/digests/<date>/deepreads/<arxiv_id>.md` at end.

### Step 3 — Rank top 10 → ranked.md

After all K agents return, read every deep-read markdown.

**Drop:** any with "建议跳过" in section 1.

**Score the rest** by 5 dims (each 1-5):
- **stack_fit** — Franka + ManiSkill3 + RLinf + VLA fine-tune + 真机 RL 耦合度
- **insight_density** — 第 6 节几条具体可借用？是否非平凡？
- **claim_strength** — 证据是否过硬（真机 / 多 seed / 强 baseline）
- **novelty** — 真的新还是 A+B+C
- **timing** — 是否半年内可执行

**Pick top 10 by sum**（不到 10 篇有几篇出几篇）。

**输出格式（MCP 解析依赖）**：
```
# Paper Radar — <date>

今天 N 篇值得读，按评分序。每篇直接是 paper-deep-read 的 6-section 全文。

---

# [#1 score=NN] <title>

<paper full md>

---

# [#2 score=NN] <title>
...
```

每篇前的 wrapper `# [#N score=NN] <title>` 是固定格式——**不要改**，MCP server `list_papers()` 解析依赖它。

**绝对不要做**：tier B/C/D 列表、"今日时间分配建议"、"3 条 take-home"、pipeline self-reflection——用户明确拒绝过。

### Step 4 — 完成（无推送）

写完 `ranked.md` 即结束。**不再做任何推送**（飞书 / 邮件 / 通知都不要）。

UI 浏览全部交给 Cowork Live Artifact，artifact 通过 `paper-radar` MCP server 调用 `list_papers` / `get_paper` 等工具拿数据。详见 `HANDOFF_TO_COWORK.md`。

## Configuration / common variants

- "前 N 篇" → K=N (default 18, max 50)
- "只看 VLA" → filter candidates by `topic_labels` containing 'VLA' or 'VLA-named-model'
- "只看 RL" → filter by `RL-post-train` or `real-robot-RL`
- "重新跑" → delete digest dir and rerun
- "看 X 月 Y 日" → pass --date

## When NOT to use

- User asks about ONE specific paper they pasted → use `paper-deep-read` directly, not this pipeline
- User asks for a literature survey → use `research-lit` or `arxiv` skills

## Failure modes

- **arXiv 429**: aggregate has retry; if still fails after 5 attempts, fall back to HF Daily only
- **Agent fetch fails**: each agent prompt 自己说"仅基于 abstract"
- **All top K skipped**: report "今天 K=18 全被淘汰，建议 re-run with K=30 或 relax min-score"

## Refreshing the X watchlist (optional, weekly)

```bash
SCWEET_AUTH_TOKEN=<token> bash ~/.claude/skills/paper-radar/scripts/refresh_watchlist.sh
```

Token: x.com → DevTools (F12) → Application → Cookies → x.com → `auth_token`. **Revoke session after.**

## Cost note

K=18 deep-reads ≈ $1-2/day on Claude subscription. Worth it if it saves 1 hour of manual triage.

## Cowork agent 想用这套数据？

**Cowork sandbox 看不到 `~/.claude/skills/`**。让 Cowork agent 通过 `window.cowork.callMcpTool({server: 'paper-radar', tool: 'list_papers', ...})` 拉数据，不要让它自己重写后端。详见 `~/code/paper_reading_walkstream/HANDOFF_TO_COWORK.md`。
