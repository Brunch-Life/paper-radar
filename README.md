# paper-radar (Claude Code skill)

Daily paper triage for embodied AI / robot learning research.
Surfaces today's must-read papers from arXiv + HuggingFace Daily Papers,
boosted by a 121-account X watchlist mined from a senior researcher's following list.

**Trigger**: in any Claude Code session, say `扫论文` / `today's papers` / `/paper-radar`.

## What it does

1. **Aggregate** — `scripts/aggregate.py` pulls today's arXiv (cs.RO/cs.LG/cs.AI/cs.CV) +
   HF Daily Papers, scores each against your stack (Franka FR3 + ManiSkill3 + RLinf + π0 +
   world model + RL) and the author-boost table.
2. **Triage** — Claude Code spawns parallel sub-agents that each invoke the `paper-deep-read`
   pattern on a candidate. Skill auto-skips obvious nos at section 1.
3. **Rank** — Top 10 keepers are concatenated into `digests/<date>/ranked.md` as raw
   6-section deep reads. No tier ranking, no take-home summary, no pipeline meta.

## First-run setup (one-time)

```bash
bash ~/.claude/skills/paper-radar/scripts/setup.sh
```

Creates `.venv/` inside the skill dir and installs `arxiv`, `feedparser`, `requests`, `Scweet`.

## Manual aggregator run

```bash
~/.claude/skills/paper-radar/.venv/bin/python \
  ~/.claude/skills/paper-radar/scripts/aggregate.py \
  --date 2026-05-07 --days 2 --min-score 2 --max-out 80
```

Outputs go to `~/.claude/skills/paper-radar/digests/2026-05-07/`.

## Refreshing the X watchlist (optional, weekly)

```bash
SCWEET_AUTH_TOKEN=<token> \
  bash ~/.claude/skills/paper-radar/scripts/refresh_watchlist.sh
```

Token: x.com → DevTools (F12) → Application → Cookies → x.com → `auth_token`. Revoke session afterward.

## Scoring (Stage 1)

| Signal | Weight | Notes |
|---|---|---|
| Topic keyword (Tier-A) | +4 | VLA / diffusion-policy / real-robot RL / RL post-train / sim platforms |
| Topic keyword (Tier-B) | +2 | manipulation / humanoid / imitation / teleop / foundation policy |
| Topic keyword (Tier-C) | +1 | LLM-for-robot / embodied / autonomous-driving |
| Author in 121 watchlist | +1 to +5 | RT-signal authors get +5, regular curated +1 |
| HuggingFace upvotes | min(votes, 10) | capped to prevent HF dominance |
| Drop-list match | -5 | bioinformatics, federated learning, medical imaging |

Defaults: keep `score >= 2`, top 80.

## Style preferences (locked in)

- Output `ranked.md` is the **direct concatenation** of top-10 deep reads' 6-section markdown.
- **No** tier B/C/D/E lists.
- **No** "today's time allocation" table.
- **No** "3 take-home insights" summary.
- **No** pipeline self-reflection.

The user explicitly rejected those packaging layers; only the raw skill outputs are wanted.

## File layout

```
~/.claude/skills/paper-radar/
├── SKILL.md                          # invocation logic
├── README.md                         # this file
├── scripts/{aggregate.py, setup.sh, refresh_watchlist.sh}
├── data/{following,relevant,author_boost,rt_authors,tweets}.json
├── data/FOLLOW_LIST.md
├── digests/YYYY-MM-DD/{candidates.json, candidates.md, deepreads/, ranked.md}
├── requirements.txt
├── .venv/                            # gitignored, set up by setup.sh
└── .gitignore
```

The 121-account watchlist derivation (Scweet scrape of `@gaofeng220`'s following + RT
signals → keyword filter → author normalization) is documented in commit history;
`scripts/refresh_watchlist.sh` rebuilds the boost table from a fresh Scweet scrape.

## Why "skill-first then filter" (rationale)

We deep-read every top-K candidate FIRST, then filter, rather than the obvious
"filter then deep-read" order. The reason:

- A title + abstract pre-filter is **noisy** — many papers have great-sounding
  abstracts but the actual technical contribution is shallow (or vice versa)
- The `paper-deep-read` skill has a built-in "建议跳过" exit at section 1
  that's much more accurate than keyword matching
- The 6-section structured output gives much richer signal for the final rank

The cost is real (~$1-2/day for K=18) but reading 1 wrong paper costs
20-30 min of your time, so it pays off after blocking even 1-2 dud papers.

## Known limitations / V0.2 backlog

- No Zotero co-authorship signal yet
- Scweet rate limits prevent daily X scraping; X signals are static (refresh weekly)
- HF Daily Papers API sometimes returns yesterday's set if the day is too fresh
- Drop-list is small; will accumulate as we see false positives in production
