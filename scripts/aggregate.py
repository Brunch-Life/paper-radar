#!/usr/bin/env python3
"""
paper-radar daily aggregator
============================

Pulls candidate papers from multiple sources, scores each by how relevant it is
to the user's stack (Franka + ManiSkill3 + RLinf + VLA fine-tuning + real-robot
RL + world model + diffusion policy + sim2real), and dumps a candidates.json
+ candidates.md ready for the paper-deep-read skill to triage.

Sources:
  1. arXiv new submissions in cs.RO / cs.LG / cs.AI / cs.CV (last 24h)
  2. HuggingFace Daily Papers (community-voted)
  3. Author boost table from the 121-account watchlist (../data/author_boost.json)

Layout (when bundled inside the paper-radar skill):
  ~/.claude/skills/paper-radar/
  ├── scripts/aggregate.py    ← THIS FILE
  ├── data/                   ← reference data (watchlist, author boost)
  └── digests/YYYY-MM-DD/     ← outputs

Run: python3 scripts/aggregate.py [--date 2026-05-07] [--days 1]
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os
# Resolve skill root: scripts/aggregate.py → skill root one level up.
SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = SKILL_ROOT / "data"
# Output dir: env override first, then ~/code/paper_reading_walkstream/digests, then in-skill fallback.
_default_digests = Path.home() / "code" / "paper_reading_walkstream" / "digests"
DIGESTS_DIR = Path(os.environ.get("PAPER_RADAR_DIGESTS_DIR", str(_default_digests)))
DIGESTS_DIR.mkdir(parents=True, exist_ok=True)

# Single source of truth for topic / author / HF scoring. See
# paper_radar/scoring.py — Tier S/A/B/C design replaces flat TOPIC_KW.
sys.path.insert(0, str(SKILL_ROOT))
from paper_radar.scoring import topic_score, author_match, hf_bonus, HF_UPVOTE_CAP

# ARXIV_CATS: which arXiv categories to fetch
ARXIV_CATS = ['cs.RO', 'cs.LG', 'cs.AI', 'cs.CV']


def fetch_arxiv(days: int = 1, max_per_cat: int = 200,
                include_cross_list: bool = True) -> list[dict]:
    """Fetch newly-announced arxiv papers via the official RSS feed.

    Why RSS instead of the date-filtered API query (per zotero-arxiv-daily):
    arxiv announces papers at 20:00 ET (≈ 09:00 Beijing next day) Sun-Thu;
    `arxiv.org/api/query?sortBy=submittedDate` is timezone-confused at the
    boundary. The RSS feed at `rss.arxiv.org/atom/<query>` always reflects
    "the most recent announcement", so a daily run any time after 09:30 BJT
    deterministically gets that day's batch — no `--days` calendar math needed.

    Steps:
      1. Pull RSS atom for `cs.RO+cs.LG+cs.AI+cs.CV` and collect ids whose
         arxiv_announce_type ∈ {"new"} (or {"new","cross"} if include_cross_list).
      2. Hit arxiv API in batches of 20 to flesh out title/abstract/authors.

    The `days`/`max_per_cat` args are kept for CLI compatibility but ignored
    when the RSS path succeeds — RSS already gives "today's batch" exactly.
    """
    import time
    import arxiv
    import feedparser

    cats = ARXIV_CATS
    query = '+'.join(cats)
    rss_url = f"https://rss.arxiv.org/atom/{query}"
    print(f"  arxiv: RSS {rss_url}", file=sys.stderr)
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        print(f"  arxiv: RSS feed empty, falling back to API date query", file=sys.stderr)
        return _fetch_arxiv_api_fallback(cats, days, max_per_cat)

    allowed = {"new", "cross"} if include_cross_list else {"new"}
    arxiv_ids = []
    for e in feed.entries:
        ann = e.get("arxiv_announce_type", "new")
        if ann not in allowed:
            continue
        # id like "oai:arXiv.org:2511.01234"
        raw_id = e.id.removeprefix("oai:arXiv.org:")
        arxiv_ids.append(raw_id)
    print(f"  arxiv: RSS yielded {len(arxiv_ids)} new+cross papers", file=sys.stderr)

    if not arxiv_ids:
        return []

    # Hydrate via arxiv API in chunks of 20 (well within rate limits).
    out = {}
    client = arxiv.Client(page_size=100, delay_seconds=4, num_retries=5)
    for i in range(0, len(arxiv_ids), 20):
        chunk = arxiv_ids[i:i + 20]
        try:
            search = arxiv.Search(id_list=chunk)
            for result in client.results(search):
                aid = result.entry_id.rsplit('/', 1)[-1].split('v')[0]
                if aid in out:
                    continue
                out[aid] = {
                    'arxiv_id': aid,
                    'title': result.title.strip(),
                    'abstract': result.summary.strip().replace('\n', ' '),
                    'authors': [a.name for a in result.authors],
                    'published': result.published.isoformat(),
                    'categories': list(result.categories),
                    'pdf_url': result.pdf_url,
                    'abs_url': f"https://arxiv.org/abs/{aid}",
                    'source': ['arxiv-rss'],
                }
        except Exception as exc:
            print(f"    chunk {i}-{i+20} failed: {exc}", file=sys.stderr)

    print(f"  arxiv: hydrated {len(out)} papers", file=sys.stderr)
    return list(out.values())


def _fetch_arxiv_api_fallback(cats, days, max_per_cat) -> list[dict]:
    """Old date-filtered API path. Used only when RSS is empty (rare)."""
    import time
    import arxiv
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = {}
    client = arxiv.Client(page_size=100, delay_seconds=8, num_retries=5)
    for i, cat in enumerate(cats):
        if i > 0:
            time.sleep(15)
        search = arxiv.Search(
            query=f'cat:{cat}',
            max_results=max_per_cat,
            sort_by=arxiv.SortCriterion.SubmittedDate,
            sort_order=arxiv.SortOrder.Descending,
        )
        try:
            for result in client.results(search):
                if result.published < cutoff:
                    break
                aid = result.entry_id.rsplit('/', 1)[-1].split('v')[0]
                if aid in out:
                    out[aid]['categories'].append(cat)
                    continue
                out[aid] = {
                    'arxiv_id': aid,
                    'title': result.title.strip(),
                    'abstract': result.summary.strip().replace('\n', ' '),
                    'authors': [a.name for a in result.authors],
                    'published': result.published.isoformat(),
                    'categories': [cat] + [c for c in result.categories if c != cat],
                    'pdf_url': result.pdf_url,
                    'abs_url': f"https://arxiv.org/abs/{aid}",
                    'source': ['arxiv-api'],
                }
        except Exception as e:
            print(f"    cat {cat} fallback failed: {e}", file=sys.stderr)
    return list(out.values())


def fetch_hf_papers(date_str: str) -> list[dict]:
    """Fetch HuggingFace daily papers for the given date (YYYY-MM-DD)."""
    import requests

    out = []
    url = f"https://huggingface.co/api/daily_papers?date={date_str}"
    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  hf: status {r.status_code}, trying without date param", file=sys.stderr)
            r = requests.get("https://huggingface.co/api/daily_papers", timeout=30)
        data = r.json()
    except Exception as e:
        print(f"  hf: failed {e}", file=sys.stderr)
        return []

    for item in data:
        paper = item.get('paper', {})
        arxiv_id = paper.get('id')
        if not arxiv_id:
            continue
        out.append({
            'arxiv_id': arxiv_id,
            'title': paper.get('title', '').strip(),
            'abstract': paper.get('summary', '').strip().replace('\n', ' '),
            'authors': [a.get('name', '') for a in paper.get('authors', [])],
            'published': paper.get('publishedAt', ''),
            'categories': [],
            'pdf_url': f"https://arxiv.org/pdf/{arxiv_id}",
            'abs_url': f"https://arxiv.org/abs/{arxiv_id}",
            'source': ['hf-daily'],
            'hf_upvotes': item.get('paper', {}).get('upvotes', 0),
            'hf_summary': item.get('summary', '').strip()[:500],
        })

    print(f"  hf: kept {len(out)} papers", file=sys.stderr)
    return out


def merge_papers(*lists) -> list[dict]:
    """Merge papers from different sources by arxiv_id."""
    merged = {}
    for lst in lists:
        for p in lst:
            aid = p['arxiv_id']
            if aid in merged:
                merged[aid]['source'] = list(set(merged[aid]['source'] + p['source']))
                # Keep richer fields
                if not merged[aid].get('hf_upvotes') and p.get('hf_upvotes'):
                    merged[aid]['hf_upvotes'] = p['hf_upvotes']
                    merged[aid]['hf_summary'] = p.get('hf_summary', '')
                if p.get('categories') and not merged[aid].get('categories'):
                    merged[aid]['categories'] = p['categories']
            else:
                merged[aid] = p
    return list(merged.values())


def score_paper(p: dict, boost_table: dict) -> dict:
    t_score, t_labels = topic_score(p['title'], p.get('abstract', ''))
    a_score, a_hits = author_match(p['authors'], boost_table)
    hf = hf_bonus(p.get('hf_upvotes', 0))  # cap from paper_radar.scoring
    total = t_score + a_score + hf

    p['score'] = total
    p['score_breakdown'] = {
        'topic': t_score,
        'topic_labels': t_labels,
        'authors': a_score,
        'author_hits': a_hits,
        'hf_upvotes': p.get('hf_upvotes', 0),
        'hf_bonus_applied': hf,
        'hf_cap': HF_UPVOTE_CAP,
    }
    return p


def write_outputs(papers: list[dict], digest_dir: Path, date_str: str):
    digest_dir.mkdir(parents=True, exist_ok=True)
    # JSON
    with open(digest_dir / 'candidates.json', 'w', encoding='utf-8') as f:
        json.dump(papers, f, ensure_ascii=False, indent=1)
    # Markdown
    md = [
        f"# Paper Radar — {date_str}",
        f"",
        f"**{len(papers)} candidates** (sources: arXiv {ARXIV_CATS} + HuggingFace Daily Papers)",
        f"",
        f"Scoring: topic-keyword + author-boost (from 121 watchlist) + HF upvotes (capped at 10).",
        f"Negative score = drop keywords matched.",
        f"",
        f"Use the `paper-deep-read` skill on each candidate. The skill auto-skips obvious nos at section 1.",
        f"",
        f"---",
        f"",
    ]
    for p in papers:
        sb = p['score_breakdown']
        sources = ' '.join(f'[{s}]' for s in p['source'])
        labels = ' '.join(f'`{l}`' for l in sb['topic_labels']) if sb['topic_labels'] else '-'
        ah = ', '.join(sb['author_hits']) if sb['author_hits'] else '-'
        hf = f" 🤗+{sb['hf_upvotes']}" if sb['hf_upvotes'] else ''
        md.append(f"## [{p['score']}] {p['title']}")
        md.append(f"")
        md.append(f"- arxiv: {p['abs_url']} {sources}{hf}")
        md.append(f"- authors: {', '.join(p['authors'][:6])}{'...' if len(p['authors'])>6 else ''}")
        md.append(f"- topic-hits: {labels}")
        md.append(f"- author-hits: {ah}")
        md.append(f"")
        md.append(f"  > {p.get('abstract','')[:400]}{'...' if len(p.get('abstract',''))>400 else ''}")
        md.append(f"")
        md.append(f"  **deep-read cmd:** `/paper-deep-read {p['abs_url']}`")
        md.append(f"")
    with open(digest_dir / 'candidates.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    print(f"\nWrote {digest_dir/'candidates.json'}", file=sys.stderr)
    print(f"Wrote {digest_dir/'candidates.md'}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'),
                    help="Digest date (default: today)")
    ap.add_argument('--days', type=int, default=2,
                    help="How many days back to fetch from arXiv (default 2 for buffer)")
    ap.add_argument('--min-score', type=int, default=2,
                    help="Drop candidates with score below this (default 2)")
    ap.add_argument('--max-out', type=int, default=80,
                    help="Cap total candidates after sort (default 80)")
    args = ap.parse_args()

    boost_table = json.load(open(DATA_DIR / 'author_boost.json'))

    print(f"Fetching arXiv (last {args.days}d)...", file=sys.stderr)
    arxiv_papers = fetch_arxiv(days=args.days)

    print(f"\nFetching HF daily papers ({args.date})...", file=sys.stderr)
    hf_papers = fetch_hf_papers(args.date)

    merged = merge_papers(arxiv_papers, hf_papers)
    print(f"\nMerged: {len(merged)} unique papers", file=sys.stderr)

    scored = [score_paper(p, boost_table) for p in merged]
    # Filter
    kept = [p for p in scored if p['score'] >= args.min_score]
    kept.sort(key=lambda p: -p['score'])
    if len(kept) > args.max_out:
        kept = kept[:args.max_out]

    print(f"After filter (score>={args.min_score}): {len(kept)} candidates", file=sys.stderr)

    digest_dir = DIGESTS_DIR / args.date
    write_outputs(kept, digest_dir, args.date)

    # Print top 5 to stdout
    print("\n=== TOP 5 by score ===")
    for p in kept[:5]:
        print(f"[{p['score']}] {p['title'][:80]}")
        print(f"      {p['abs_url']} | labels: {p['score_breakdown']['topic_labels']}")


if __name__ == '__main__':
    main()
