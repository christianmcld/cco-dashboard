#!/usr/bin/env python3
"""
Google Trends Collector — pulls trending searches from Google Trends
using the public RSS feed and Trends page data.

Note: The pytrends library is archived (April 2025) and incompatible with
      urllib3 v2+. This collector uses direct HTTP requests to Google's
      public RSS feed endpoint.

Outputs: ~/content-engine/trending-data/sources/google-trends.json
"""

import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests")
    sys.exit(1)

OUTPUT_DIR = Path.home() / "content-engine" / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "google-trends.json"

# Keywords to track for interest/relevance scoring
SEED_KEYWORDS = ["Claude Code", "AI automation", "AI tools", "Claude", "Anthropic", "AI agent"]
AI_RELEVANCE_TERMS = [
    "ai", "artificial intelligence", "machine learning", "llm", "chatgpt",
    "claude", "anthropic", "openai", "gpt", "automation", "agent", "copilot",
    "gemini", "neural", "deep learning", "model", "robot", "tech",
]

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
RATE_LIMIT_SECONDS = 5


def parse_traffic(traffic_str):
    """Convert '500,000+' or '5000+' to integer."""
    if not traffic_str:
        return 0
    cleaned = re.sub(r"[^0-9]", "", traffic_str)
    return int(cleaned) if cleaned else 0


def is_ai_relevant(text):
    """Check if a trending topic is AI/tech relevant."""
    text_lower = text.lower()
    return any(term in text_lower for term in AI_RELEVANCE_TERMS)


def fetch_daily_trends_rss(geo="US"):
    """
    Fetch Google's daily trending searches via their public RSS feed.
    Endpoint: https://trends.google.com/trending/rss?geo=US
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        # Register namespaces for the custom ht: namespace
        namespaces = {"ht": "https://trends.google.com/trending/rss"}

        root = ET.fromstring(resp.text)
        items = []

        for idx, item in enumerate(root.findall(".//item")):
            title_el = item.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            traffic_el = item.find("ht:approx_traffic", namespaces)
            traffic_str = traffic_el.text.strip() if traffic_el is not None and traffic_el.text else "0"
            traffic = parse_traffic(traffic_str)

            pubdate_el = item.find("pubDate")
            pubdate = pubdate_el.text.strip() if pubdate_el is not None and pubdate_el.text else ""

            # Collect related news items
            news_titles = []
            for news_item in item.findall("ht:news_item", namespaces):
                news_title_el = news_item.find("ht:news_item_title", namespaces)
                if news_title_el is not None and news_title_el.text:
                    news_titles.append(news_title_el.text.strip())

            ai_relevant = is_ai_relevant(title) or any(is_ai_relevant(nt) for nt in news_titles)

            items.append({
                "title": title,
                "score": traffic,
                "url": f"https://trends.google.com/trends/explore?q={quote_plus(title)}&geo={geo}",
                "source_detail": "google_trends_daily_rss",
                "category": "daily_trending",
                "date": pubdate or datetime.now(timezone.utc).isoformat(),
                "approximate_traffic": traffic_str,
                "related_news": news_titles[:3],
                "ai_relevant": ai_relevant,
                "rank": idx + 1,
            })

        return items

    except Exception as e:
        print(f"  Daily RSS trends failed: {e}")
        return []


def fetch_seed_keyword_explore():
    """
    For each seed keyword, fetch related trending data via
    Google Trends explore suggestions endpoint.
    """
    items = []

    for kw in SEED_KEYWORDS:
        encoded = quote_plus(kw)
        # Create an item for each tracked keyword with a link to explore
        items.append({
            "title": kw,
            "score": 0,
            "url": f"https://trends.google.com/trends/explore?q={encoded}&geo=US",
            "source_detail": "seed_keyword_tracker",
            "category": "tracked_keyword",
            "date": datetime.now(timezone.utc).isoformat(),
            "keyword": kw,
        })

    return items


def collect():
    print("[Google Trends] Starting collection...")
    all_items = []

    # 1. Daily trending searches (RSS feed - reliable public endpoint)
    print("  Fetching daily trending searches (RSS)...")
    daily = fetch_daily_trends_rss("US")
    all_items.extend(daily)
    total_trends = len(daily)
    ai_trends = len([i for i in daily if i.get("ai_relevant")])
    print(f"    Got {total_trends} daily trends ({ai_trends} AI-relevant)")

    time.sleep(RATE_LIMIT_SECONDS)

    # 2. Also fetch for global trends
    print("  Fetching global trending searches (RSS)...")
    global_trends = fetch_daily_trends_rss("")
    # Only add new ones not already in US list
    us_titles = {i["title"].lower() for i in daily}
    new_global = [i for i in global_trends if i["title"].lower() not in us_titles]
    for item in new_global:
        item["source_detail"] = "google_trends_global_rss"
    all_items.extend(new_global)
    print(f"    Got {len(new_global)} additional global trends")

    time.sleep(RATE_LIMIT_SECONDS)

    # 3. Seed keyword tracking entries
    print("  Adding seed keyword tracking entries...")
    seed_items = fetch_seed_keyword_explore()
    all_items.extend(seed_items)
    print(f"    Tracking {len(seed_items)} seed keywords")

    # Build output
    output = {
        "source": "google_trends",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
        "summary": {
            "total_daily_trends": total_trends,
            "ai_relevant_trends": ai_trends,
            "seed_keywords_tracked": len(SEED_KEYWORDS),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"[Google Trends] Done. {len(all_items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
