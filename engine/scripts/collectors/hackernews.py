#!/usr/bin/env python3
"""
Hacker News Collector — pulls top stories mentioning AI/Claude topics
using the Algolia HN Search API (free, no auth required).

Outputs: ~/content-engine/trending-data/sources/hackernews.json
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
OUTPUT_FILE = OUTPUT_DIR / "hackernews.json"

# Search queries to pull stories for
SEARCH_QUERIES = [
    "Claude",
    "AI agent",
    "AI automation",
    "Claude Code",
    "Anthropic",
    "LLM",
]

BASE_URL = "https://hn.algolia.com/api/v1"
USER_AGENT = "CCO-Dashboard/1.0 (content-engine collector)"
RATE_LIMIT_SECONDS = 2


def fetch_stories(query, num_results=25):
    """Search HN stories via Algolia API."""
    url = f"{BASE_URL}/search"
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": num_results,
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        stories = []
        for hit in data.get("hits", []):
            points = hit.get("points", 0) or 0
            num_comments = hit.get("num_comments", 0) or 0
            created_at = hit.get("created_at", "")
            story_url = hit.get("url", "")
            hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

            stories.append({
                "title": hit.get("title", ""),
                "score": points,
                "url": story_url if story_url else hn_url,
                "hn_url": hn_url,
                "source_detail": f"hn_search_{query.replace(' ', '_').lower()}",
                "date": created_at,
                "num_comments": num_comments,
                "author": hit.get("author", ""),
                "search_query": query,
            })

        return stories

    except Exception as e:
        print(f"    Query '{query}': Error: {e}")
        return []


def fetch_front_page():
    """Fetch current HN front page stories."""
    url = f"{BASE_URL}/search"
    params = {
        "tags": "front_page",
        "hitsPerPage": 30,
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        stories = []
        for hit in data.get("hits", []):
            points = hit.get("points", 0) or 0
            num_comments = hit.get("num_comments", 0) or 0
            created_at = hit.get("created_at", "")
            story_url = hit.get("url", "")
            hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

            stories.append({
                "title": hit.get("title", ""),
                "score": points,
                "url": story_url if story_url else hn_url,
                "hn_url": hn_url,
                "source_detail": "hn_front_page",
                "date": created_at,
                "num_comments": num_comments,
                "author": hit.get("author", ""),
                "search_query": "_front_page",
            })

        return stories

    except Exception as e:
        print(f"    Front page fetch failed: {e}")
        return []


def collect():
    print("[Hacker News] Starting collection...")
    all_items = []
    seen_ids = set()

    # 1. Front page stories
    print("  Fetching front page stories...")
    front_page = fetch_front_page()
    for story in front_page:
        story_id = story.get("hn_url", "")
        if story_id not in seen_ids:
            seen_ids.add(story_id)
            all_items.append(story)
    print(f"    Got {len(front_page)} front page stories")
    time.sleep(RATE_LIMIT_SECONDS)

    # 2. Search for each query
    for query in SEARCH_QUERIES:
        print(f"  Searching for '{query}'...")
        stories = fetch_stories(query)
        added = 0
        for story in stories:
            story_id = story.get("hn_url", "")
            if story_id not in seen_ids:
                seen_ids.add(story_id)
                all_items.append(story)
                added += 1
        print(f"    Got {len(stories)} results, {added} new")
        time.sleep(RATE_LIMIT_SECONDS)

    # Sort by score (highest first)
    all_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    output = {
        "source": "hackernews",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"[Hacker News] Done. {len(all_items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
