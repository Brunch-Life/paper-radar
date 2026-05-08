# paper-radar

Daily paper triage pipeline for embodied AI / robot learning research.

Self-contained Claude Code skill at `~/.claude/skills/paper-radar/`. Pulls candidates from arXiv RSS + HuggingFace Daily Papers + a 121-account X watchlist (mined from a senior researcher's following list), invokes `paper-deep-read` on the top K, ranks the top 10, **pushes 1 overview + 10 detail cards to Feishu**, and exposes everything via an MCP server so any Claude Code session can pull a specific paper into context for follow-up Q&A.

**Repo**: https://github.com/Brunch-Life/paper-radar (private)

---

## End-to-end flow

```
                                    ┌─────────────────┐
                                    │  arXiv RSS feed │
                                    │  (cs.RO/LG/AI/CV)│
                                    └────────┬────────┘
                                             ↓
                       Stage 1 ─→  scripts/aggregate.py  ─→  candidates.json
                       (<1 min,    + HF Daily upvotes
                        free)      + 121-author boost
                                             ↓
                       Stage 2 ─→  K=18 parallel paper-deep-read agents
                       (~$1-2,     (each agent embeds full SKILL.md verbatim
                        ~20 min)   so subagents can use the skill)
                                             ↓
                       Stage 3 ─→  rank → top 10 → ranked.md (concat)
                                             ↓
                       Push   ─→  scripts/notify.py
                                  ├─ push_feishu.py: 1 overview + N detail cards
                                  └─ push_email.py: SMTP fallback (optional)
                                             ↓
                                   📚 Feishu / Hotmail
                                             ↓
                       Q&A    ─→  any Claude Code session
                                  @paper-radar:get_paper(arxiv_id)
                                  → full 6-section markdown loaded
                                  → ask anything about the paper
```

---

## Daily routine (auto, no action needed)

A `mcp__scheduled-tasks` routine runs the full pipeline every day:

- **TaskId**: `paper-radar-daily`
- **Cron**: `33 10 * * *` (10:33 BJT — well after arxiv's morning announcement at ~09:00 BJT)
- **Storage**: `~/.claude/scheduled-tasks/paper-radar-daily/SKILL.md`
- **Why 10:33**: arxiv announces new papers at 20:00 ET = 08:00–09:00 BJT depending on DST. 10:33 has comfortable buffer + scheduler jitter (`+8 min` deterministic).

Manage via `mcp__scheduled-tasks__update_scheduled_task` or the "Scheduled" sidebar in Claude Code.

---

## On-demand: `扫论文` in any Claude Code session

The `paper-radar` skill itself (this dir's `SKILL.md`) is auto-loaded by Claude Code. Triggering phrases: `扫论文`, `今天的 paper radar`, `today's papers`, `/paper-radar`, `看看今天值得读什么`. Skill does aggregate → 18 deep-reads → top-10 ranked.md → Feishu push.

---

## **Reading anywhere, asking in Claude Code** (the MCP)

The point of the MCP server: you can read papers wherever you like (Feishu cards, Obsidian vault on `~/code/paper_reading_walkstream/digests/`, raw markdown, anywhere) — and when you want to ask a follow-up about a paper's claim or method, jump back into Claude Code and the paper context is one tool call away.

### Tools exposed

| Tool | Purpose |
|---|---|
| `list_dates(limit=14)` | recent digest dates available |
| `list_papers(date=None)` | top-10 ranked papers for a date (default: latest) |
| `get_paper(arxiv_id, date=None)` | full 6-section deep-read markdown (loads into context) |
| `search_papers(query, limit=20)` | fuzzy search title + tagline + arxiv_id across all digests, including the 250-paper annual scan |
| `get_top5_annual(year_tag=None)` | the TOP 5 highlight from the annual review |
| `get_candidates(date=None, top_n=30)` | raw Stage-1 candidates (when ranked.md isn't ready yet) |

### How to use it (any Claude Code session)

```
你: 帮我看下 2605.04649 那篇怎么用力矩 baseline 做触觉 grounding 的
[Claude internally calls paper-radar:get_paper("2605.04649")]
[6-section deep-read loaded as context]
Claude: <answers based on full paper deep-read>
```

Or:

```
你: 前几天那篇用 world model 当 reward 的 paper 是哪一篇？
[Claude internally calls paper-radar:search_papers("world model reward")]
Claude: 2510.00406 VLA-RFT…
你: 它的 GRPO 设计具体跟 SimpleVLA-RL 有啥不一样？
[Claude calls get_paper for both, compares]
Claude: <comparison>
```

The MCP is registered at **user scope** (every Claude Code session sees it):

```bash
$ claude mcp list | grep paper-radar
paper-radar: /Users/.../paper-radar/.mcp-venv/bin/python /Users/.../paper-radar/scripts/mcp_server.py - ✓ Connected
```

Server source: `scripts/mcp_server.py` (built on `mcp.server.fastmcp.FastMCP`, Python 3.11 in `.mcp-venv`).

---

## Output paths

| What | Where |
|---|---|
| Daily digest | `~/code/paper_reading_walkstream/digests/YYYY-MM-DD/` |
| Annual digest | `~/code/paper_reading_walkstream/digests/annual-YYYY-MM-DD/` |
| Per-paper deep-reads | `<digest>/deepreads/<arxiv_id>.md` |
| Daily ranked.md (top 10) | `<digest>/ranked.md` |
| Annual review (50) | `<annual>/annual_review.md` |
| Annual TOP 5 highlight | `<annual>/top5.md` |
| Skill-dir back-symlinks | `~/.claude/skills/paper-radar/digests/<tag>` → above paths |

Override location: `PAPER_RADAR_DIGESTS_DIR=/path` env var (read by aggregate, annual_scan, notify, mcp_server).

### Suggestion: open this dir as an Obsidian vault

`~/code/paper_reading_walkstream/digests/` is pure markdown — open it in Obsidian and you get full-text search, backlinks, tagging on top of the deep-reads. Daily/annual files become navigable, and you can highlight + comment without breaking anything (just don't rename arxiv-id files).

---

## Push channels

### Feishu webhook (primary, working)

- Bot's "自定义关键词" = `Papers` — every payload contains the literal "Papers" string
- Webhook URL stored in `data/webhook.txt` (gitignored, file mode 600)
- Sends 1 **overview card** + N **detail cards** (one per paper, full 6-section markdown)
- Card content split if >25KB into multiple `markdown` elements within the same card
- Rate-limited to 0.3s between sends (well under Feishu's 100 msg/min cap)

### Email SMTP fallback (optional)

- Triggers only if Feishu push fails AND `data/email_config.json` has `smtp_user`/`smtp_password`
- Designed for Hotmail/Outlook (`smtp-mail.outlook.com:587` STARTTLS)
- Get an Outlook app password at account.microsoft.com → Security → App passwords
- Force email always-on by setting `ALWAYS_EMAIL=1`

### Manual push from any digest

```bash
~/.claude/skills/paper-radar/.venv/bin/python \
  ~/.claude/skills/paper-radar/scripts/notify.py 2026-05-08
```

---

## First-time setup (clone-and-run)

```bash
git clone git@github.com:Brunch-Life/paper-radar.git ~/.claude/skills/paper-radar
cd ~/.claude/skills/paper-radar

# Stage-1/2 venv (Python 3.9+ OK)
bash scripts/setup.sh

# MCP server venv (Python 3.10+ required for mcp SDK)
uv venv .mcp-venv --python 3.11
uv pip install --python .mcp-venv/bin/python mcp

# Secrets (gitignored — fill in your own)
echo "https://open.feishu.cn/open-apis/bot/v2/hook/<your-webhook>" > data/webhook.txt
chmod 600 data/webhook.txt

# (Optional) email fallback
cp data/email_config.json.example data/email_config.json   # fill smtp_user/password

# Register MCP server at user scope
claude mcp add -s user paper-radar \
  $(pwd)/.mcp-venv/bin/python $(pwd)/scripts/mcp_server.py

# Register the daily routine
# (do this from inside Claude Code — see scheduled-tasks/paper-radar-daily/SKILL.md
#  or use mcp__scheduled-tasks__create_scheduled_task with cron `33 10 * * *`)
```

---

## Watchlist refresh (optional, weekly)

The 121-account watchlist drifts as the senior researcher follows new people. Refresh:

```bash
SCWEET_AUTH_TOKEN=<token> bash scripts/refresh_watchlist.sh
```

Token: x.com → DevTools (F12) → Application → Cookies → x.com → `auth_token`. **Revoke session after the refresh** (x.com → Settings → Sessions).

---

## Scoring (Stage 1)

| Signal | Weight | Notes |
|---|---|---|
| Tier-A topic keyword | +4 | VLA / diffusion-policy / real-robot RL / RL post-train / sim platforms |
| Tier-B topic keyword | +2 | manipulation / humanoid / imitation / teleop / foundation policy |
| Tier-C topic keyword | +1 | LLM-for-robot / embodied / autonomous-driving |
| Author in 121 watchlist | +1 to +5 | RT-signal authors get +5, regular curated +1 |
| HuggingFace upvotes | min(votes, 10) | capped to prevent HF brigading |
| Drop-list match | -5 | bioinformatics, federated learning, medical imaging |

Defaults: keep `score >= 2`, top 80. Per `data/KEYWORD_SOURCE_ANALYSIS.md` there's a planned upgrade to a Tier S (named-models like π0.5/GR00T-N1.5/MolmoAct2) + tighter Tier-C gating + HF cap reduction (50 → 5).

---

## Style preferences (locked in)

- `ranked.md` is the **direct concatenation** of top-10 deep-reads' 6-section markdown
- **No** tier B/C/D/E lists
- **No** "today's time allocation" table
- **No** "3 take-home insights" summary
- **No** pipeline self-reflection

User explicitly rejected those packaging layers; only raw skill outputs are wanted.

---

## File layout

```
~/.claude/skills/paper-radar/
├── SKILL.md                          # invocation logic for `扫论文`
├── README.md                         # this file
├── scripts/
│   ├── aggregate.py                  # Stage 1 (arxiv RSS + HF + author boost)
│   ├── annual_scan.py                # past-365-day backfill (HF Daily archive)
│   ├── push_feishu.py                # 1 overview + N detail cards
│   ├── push_email.py                 # SMTP fallback (optional)
│   ├── notify.py                     # orchestrator: Feishu → email
│   ├── mcp_server.py                 # paper-radar MCP server (FastMCP)
│   ├── setup.sh                      # one-time venv setup
│   └── refresh_watchlist.sh          # weekly Scweet refresh (optional)
├── data/
│   ├── following.json                # 1206 of @gaofeng220's followings
│   ├── relevant.json                 # 535 filtered for embodied AI
│   ├── author_boost.json             # 132 weighted authors (Stage 1 input)
│   ├── rt_authors.json               # 50 RT signal handles
│   ├── tweets.json                   # 60 recent tweets (signal)
│   ├── FOLLOW_LIST.md                # human-readable watchlist
│   ├── KEYWORD_SOURCE_ANALYSIS.md    # diagnosis of TOPIC_KW + signal sources
│   ├── webhook.txt                   # gitignored: Feishu webhook URL
│   └── email_config.json             # gitignored: SMTP creds (optional)
├── digests/                          # symlinks to ~/code/paper_reading_walkstream/digests/
├── requirements.txt
├── .venv/                            # gitignored, set up by setup.sh
├── .mcp-venv/                        # gitignored, Python 3.11 for MCP
└── .gitignore
```

---

## Why "skill-first then filter" (rationale)

Deep-read every top-K candidate FIRST, then filter, rather than the obvious "filter then deep-read":

- A title + abstract pre-filter is **noisy** — many papers have great-sounding abstracts but the actual technical contribution is shallow (or vice versa)
- The `paper-deep-read` skill has a built-in "建议跳过" exit at section 1 that's more accurate than keyword matching
- The 6-section structured output gives much richer signal for the final rank

The cost is real (~$1-2/day for K=18 on subscription) but reading 1 wrong paper costs 20-30 min of your time, so it pays off after blocking 1-2 dud papers.

---

## Known limitations / V0.2 backlog

- TOPIC_KW Tier S/A/B/C revamp pending (per KEYWORD_SOURCE_ANALYSIS.md)
- HF upvote cap should drop 50 → 5 to defang brigading
- Watchlist-author arxiv RSS not yet a separate signal (currently only `author_boost` table)
- Semantic Scholar citation-graph signal not wired in
- Feishu cloud-doc (not just chat cards) requires a Feishu App — not yet set up
- Daily routine doesn't dedupe against earlier same-day pushes if Run-now is hit twice
