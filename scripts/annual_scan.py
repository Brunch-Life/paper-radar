#!/usr/bin/env python3
"""
paper-radar annual scan
=======================

Scans the past 365 days of HuggingFace Daily Papers, scores each by stack
relevance + author boost + HF persistence/votes, outputs the top N to
digests/annual-YYYY-MM-DD/candidates.json for the daily pipeline's Stage 2.

Run: python3 scripts/annual_scan.py [--days 365] [--top 50]

Output: digests/annual-YYYY-MM-DD/candidates.{json,md}
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

import os
SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = SKILL_ROOT / "data"
# Output dir: env override first, then ~/code/paper_reading_walkstream/digests, then in-skill fallback.
_default_digests = Path.home() / "code" / "paper_reading_walkstream" / "digests"
DIGESTS_DIR = Path(os.environ.get("PAPER_RADAR_DIGESTS_DIR", str(_default_digests)))
DIGESTS_DIR.mkdir(parents=True, exist_ok=True)

# Single source of truth for topic / author / HF scoring. See
# paper_radar/scoring.py — Tier S/A/B/C design replaces flat TOPIC_KW
# (was duplicated verbatim from aggregate.py).
sys.path.insert(0, str(SKILL_ROOT))
from paper_radar.scoring import topic_score, author_match, hf_bonus, HF_UPVOTE_CAP


def fetch_hf_daily_archive(days_back=365, sleep=0.05):
    """Fetch HF Daily Papers for each day in past `days_back`. Dedupe by arxiv_id."""
    papers = {}
    today = datetime.now()
    failures = 0

    for d in range(days_back):
        date = (today - timedelta(days=d)).strftime('%Y-%m-%d')
        url = f"https://huggingface.co/api/daily_papers?date={date}"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code != 200:
                failures += 1
                continue
            data = r.json()
        except Exception as e:
            failures += 1
            continue

        for item in data:
            paper = item.get('paper', {})
            aid = paper.get('id')
            if not aid:
                continue

            existing = papers.get(aid)
            upvotes = paper.get('upvotes', 0)
            authors = [a.get('name', '') for a in paper.get('authors', [])]

            if existing:
                # Persistent paper — accumulate signal
                existing['hf_seen_days'] += 1
                existing['hf_upvotes'] = max(existing['hf_upvotes'], upvotes)
                if date not in existing['hf_seen_dates']:
                    existing['hf_seen_dates'].append(date)
            else:
                papers[aid] = {
                    'arxiv_id': aid,
                    'title': paper.get('title', '').strip(),
                    'abstract': paper.get('summary', '').strip().replace('\n', ' '),
                    'authors': authors,
                    'published': paper.get('publishedAt', ''),
                    'hf_upvotes': upvotes,
                    'hf_seen_days': 1,
                    'hf_seen_dates': [date],
                    'abs_url': f"https://arxiv.org/abs/{aid}",
                    'pdf_url': f"https://arxiv.org/pdf/{aid}",
                    'source': ['hf-daily-archive'],
                }

        if d % 30 == 0:
            print(f"  day {d}/{days_back} ({date}): {len(papers)} unique papers, {failures} fetch failures", file=sys.stderr)

        time.sleep(sleep)  # be polite to HF

    print(f"  Total: {len(papers)} unique papers, {failures} fetch failures", file=sys.stderr)
    return list(papers.values())


def score_paper(p, boost_table, persistence_weight=2):
    """Annual-scan score = topic (Tier S/A/B/C) + author boost +
    HF upvotes (capped via paper_radar.scoring.HF_UPVOTE_CAP) +
    HF persistence bonus (×N for each daily-archive day the paper appeared).
    """
    t_score, t_labels = topic_score(p['title'], p.get('abstract', ''))
    a_score, a_hits = author_match(p['authors'], boost_table)
    hf = hf_bonus(p.get('hf_upvotes', 0))
    persistence = p.get('hf_seen_days', 1) * persistence_weight
    total = t_score + a_score + hf + persistence

    p['score'] = total
    p['score_breakdown'] = {
        'topic': t_score,
        'topic_labels': t_labels,
        'authors': a_score,
        'author_hits': a_hits,
        'hf_upvotes': p.get('hf_upvotes', 0),
        'hf_bonus_applied': hf,
        'hf_cap': HF_UPVOTE_CAP,
        'hf_seen_days': p.get('hf_seen_days', 1),
        'persistence_score': persistence,
    }
    return p


def write_outputs(papers, digest_dir, top_n):
    digest_dir.mkdir(parents=True, exist_ok=True)
    with open(digest_dir / 'candidates.json', 'w', encoding='utf-8') as f:
        json.dump(papers, f, ensure_ascii=False, indent=1)

    md = [
        f"# Paper Radar — Annual Scan ({datetime.now().strftime('%Y-%m-%d')})",
        f"",
        f"**{len(papers)} candidates** in past 365 days from HuggingFace Daily Papers archive.",
        f"Top {top_n} kept after scoring.",
        f"",
        f"Scoring: topic-keyword + author-boost + HF upvotes (cap 50) + HF persistence (×2 per day appeared).",
        f"",
        f"---",
        f"",
    ]
    for p in papers[:top_n]:
        sb = p['score_breakdown']
        labels = ' '.join(f'`{l}`' for l in sb['topic_labels']) if sb['topic_labels'] else '-'
        ah = ', '.join(sb['author_hits']) if sb['author_hits'] else '-'
        md.append(f"## [{p['score']}] {p['title']}")
        md.append(f"")
        md.append(f"- arxiv: {p['abs_url']}")
        md.append(f"- authors: {', '.join(p['authors'][:6])}{'...' if len(p['authors'])>6 else ''}")
        md.append(f"- topic-hits: {labels}")
        md.append(f"- author-hits: {ah}")
        md.append(f"- HF: 🤗+{sb['hf_upvotes']} (seen {sb['hf_seen_days']} days)")
        md.append(f"")
        md.append(f"  > {p.get('abstract','')[:400]}{'...' if len(p.get('abstract',''))>400 else ''}")
        md.append(f"")

    with open(digest_dir / 'candidates.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(md))
    print(f"\nWrote {digest_dir/'candidates.json'}", file=sys.stderr)
    print(f"Wrote {digest_dir/'candidates.md'}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=365)
    ap.add_argument('--top', type=int, default=50)
    ap.add_argument('--min-score', type=int, default=5)
    ap.add_argument('--out-tag', default=None,
                    help="Suffix for digest dir (default: annual-YYYY-MM-DD)")
    args = ap.parse_args()

    boost_table = json.load(open(DATA_DIR / 'author_boost.json'))

    print(f"Fetching HF Daily Papers archive for past {args.days} days...", file=sys.stderr)
    papers = fetch_hf_daily_archive(days_back=args.days)

    print(f"\nScoring {len(papers)} papers...", file=sys.stderr)
    scored = [score_paper(p, boost_table) for p in papers]

    kept = [p for p in scored if p['score'] >= args.min_score]
    kept.sort(key=lambda p: -p['score'])

    print(f"After filter (score>={args.min_score}): {len(kept)} candidates", file=sys.stderr)
    print(f"Top score range: {kept[0]['score'] if kept else 0} → {kept[args.top-1]['score'] if len(kept)>=args.top else 'N/A'}", file=sys.stderr)

    tag = args.out_tag or f"annual-{datetime.now().strftime('%Y-%m-%d')}"
    digest_dir = DIGESTS_DIR / tag
    write_outputs(kept, digest_dir, args.top)

    print(f"\n=== TOP 10 ===", file=sys.stderr)
    for p in kept[:10]:
        print(f"[{p['score']}] {p['title'][:75]}", file=sys.stderr)
        print(f"      🤗+{p['hf_upvotes']} ({p['hf_seen_days']}d) | {p['abs_url']}", file=sys.stderr)


if __name__ == '__main__':
    main()
