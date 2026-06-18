#!/usr/bin/env python3
"""
Reddit Trending Collector — pulls hot posts from AI/tech subreddits
using Reddit's public JSON API (no auth required).

Outputs: ~/content-engine/trending-data/sources/reddit.json
"""

import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests")
    sys.exit(1)

OUTPUT_DIR = Path.home() / "content-engine" / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "reddit.json"

SUBREDDITS = [
    "ClaudeCode",
    "ChatGPT",
    "artificial",
    "MachineLearning",
    "vibecoding",
    "ClaudeAI",
    "LocalLLaMA",
]

POSTS_PER_SUB = 25
USER_AGENT = "CCO-Dashboard/1.0 (content-engine collector; contact: admin@cmndshft.com)"
RATE_LIMIT_SECONDS = 3  # Reddit's public JSON API is lenient, 2-3s between requests is fine


def calculate_engagement_score(score, num_comments, created_utc):
    """
    Calculate engagement score based on upvotes, comments, and recency.
    Higher scores for more engagement and more recent posts.
    """
    now = datetime.now(timezone.utc).timestamp()
    age_hours = max((now - created_utc) / 3600, 1)

    # Engagement = (score + 2 * comments) / age_hours^0.8
    # Comments weighted 2x because they signal deeper engagement
    engagement = (score + 2 * num_comments) / (age_hours ** 0.8)
    return round(engagement, 2)


def fetch_subreddit(subreddit):
    """Fetch hot posts from a subreddit using public JSON API."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json"
    params = {"limit": POSTS_PER_SUB, "raw_json": 1}
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        posts = []
        children = data.get("data", {}).get("children", [])

        for child in children:
            post = child.get("data", {})

            # Skip stickied/pinned posts
            if post.get("stickied", False):
                continue

            score = post.get("score", 0)
            num_comments = post.get("num_comments", 0)
            created_utc = post.get("created_utc", 0)

            posts.append({
                "title": post.get("title", ""),
                "score": score,
                "url": f"https://reddit.com{post.get('permalink', '')}",
                "source_detail": f"r/{subreddit}",
                "date": datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat(),
                "num_comments": num_comments,
                "engagement_score": calculate_engagement_score(score, num_comments, created_utc),
                "subreddit": subreddit,
                "author": post.get("author", "[deleted]"),
                "flair": post.get("link_flair_text", ""),
            })

        return posts

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            print(f"    r/{subreddit}: Private or quarantined subreddit (403)")
        elif e.response is not None and e.response.status_code == 404:
            print(f"    r/{subreddit}: Subreddit not found (404)")
        else:
            print(f"    r/{subreddit}: HTTP error: {e}")
        return []
    except Exception as e:
        print(f"    r/{subreddit}: Error: {e}")
        return []


def collect():
    print("[Reddit] Starting collection...")
    all_items = []

    for sub in SUBREDDITS:
        print(f"  Fetching r/{sub}...")
        posts = fetch_subreddit(sub)
        all_items.extend(posts)
        print(f"    Got {len(posts)} posts")
        time.sleep(RATE_LIMIT_SECONDS)

    # Sort by engagement score (highest first)
    all_items.sort(key=lambda x: x.get("engagement_score", 0), reverse=True)

    output = {
        "source": "reddit",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"[Reddit] Done. {len(all_items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
