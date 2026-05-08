#!/usr/bin/env bash
# Weekly watchlist refresh — re-scrape @gaofeng220's following + recent tweets,
# rebuild author_boost.json. Requires a fresh X.com auth_token.
#
# Usage:
#   SCWEET_AUTH_TOKEN=<token> bash scripts/refresh_watchlist.sh
#
# Get auth_token from: x.com → DevTools (F12) → Application → Cookies → x.com → auth_token

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$SKILL_DIR/.venv"
DATA="$SKILL_DIR/data"
TARGET="${TARGET_X_HANDLE:-gaofeng220}"

if [[ -z "${SCWEET_AUTH_TOKEN:-}" ]]; then
  echo "ERROR: SCWEET_AUTH_TOKEN env var required."
  echo "Get token from x.com cookies (DevTools → Application → Cookies → auth_token)."
  exit 1
fi

if [[ ! -x "$VENV/bin/scweet" ]]; then
  echo "Setup not done. Run: bash scripts/setup.sh"
  exit 1
fi

CACHE="$DATA/cache"
mkdir -p "$CACHE"

echo "[refresh] Scraping recent tweets ($TARGET)"
"$VENV/bin/scweet" --auth-token "$SCWEET_AUTH_TOKEN" \
  profile-tweets "$TARGET" --limit 60 \
  --save --save-format json --save-dir "$CACHE"

echo "[refresh] Cooling down 15s before following scrape"
sleep 15

echo "[refresh] Scraping following list ($TARGET)"
"$VENV/bin/scweet" --auth-token "$SCWEET_AUTH_TOKEN" \
  following "$TARGET" --limit 1500 \
  --save --save-format json --save-dir "$CACHE"

echo "[refresh] Rebuilding author_boost.json"
"$VENV/bin/python" - <<'PY'
import json, re, os
from collections import Counter
from pathlib import Path

ROOT = Path(os.environ['SKILL_DIR']) / 'data'
CACHE = ROOT / 'cache'

# Load latest scrape
combined = json.load(open(CACHE / f"{os.environ['TARGET']}.json"))
tweets = [r for r in combined if r.get('tweet_id')]
following = [r for r in combined if r.get('type') == 'following']

# Extract RT authors
rt_authors = []
for t in tweets:
    text = t.get('text', '') or ''
    m = re.match(r'^RT @(\w+):', text)
    if m: rt_authors.append(m.group(1).lower())
    user = t.get('user', {})
    sn = (user.get('screen_name') or '').lower()
    if sn and sn != os.environ['TARGET'].lower():
        rt_authors.append(sn)
rt_count = Counter(rt_authors)
json.dump(dict(rt_count), open(ROOT / 'rt_authors.json', 'w'), ensure_ascii=False, indent=1)

# Build author boost
def normalize_name(n):
    if not n: return None
    n = re.sub(r'[^\w\s\-\.]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    parts = [p for p in n.split() if not re.match(r'^[\d\W]+$', p)]
    return ' '.join(parts) if len(parts) >= 2 else None

boost = {}
for entry in following:
    handle = (entry.get('username') or '').lower()
    name = normalize_name(entry.get('name') or '')
    if not name: continue
    score = 0
    notes = []
    if handle in rt_count:
        score += 3
        notes.append(f"RT×{rt_count[handle]}")
    desc = (entry.get('description') or '').lower()
    if any(kw in desc for kw in ['vla', 'world model', 'diffusion polic', 'reinforcement learning',
                                  'physical intelligence', 'foundation model', 'robot', 'manipulat',
                                  'embodied', 'policy', 'sim2real', 'sim-to-real']):
        score += 1
        notes.append('strong-bio')
    if score == 0: continue
    key = name.lower()
    if key not in boost or boost[key]['score'] < score:
        boost[key] = {'name': name, 'handle': handle, 'score': score, 'notes': notes}

json.dump(boost, open(ROOT / 'author_boost.json', 'w'), ensure_ascii=False, indent=1)

# Save raw following + tweets for reference
json.dump(following, open(ROOT / 'following.json', 'w'), ensure_ascii=False, indent=1)
json.dump(tweets, open(ROOT / 'tweets.json', 'w'), ensure_ascii=False, indent=1)

print(f"[refresh] Updated: {len(boost)} authors in boost table, {len(rt_count)} RT authors, {len(following)} following entries")
PY

echo "[refresh] Done. Auth token can now be revoked at x.com → Settings → Sessions."
