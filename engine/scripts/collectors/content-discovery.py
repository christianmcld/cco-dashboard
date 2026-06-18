#!/usr/bin/env python3
"""
Content Discovery Collector — BuzzSumo replacement using Exa API.

Finds most-discussed content about AI tools, Claude Code, AI automation
using Exa's neural search with highlights. Also cross-references URLs
from Reddit and HN data to identify content shared across platforms.

Outputs: ~/content-engine/trending-data/sources/content-discovery.json

Usage:
  cd ~/content-engine && uv run python3 scripts/collectors/content-discovery.py
"""

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Centralized API key loader
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests")
    sys.exit(1)

BASE_DIR = Path.home() / "content-engine"
OUTPUT_DIR = BASE_DIR / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "content-discovery.json"
SOURCES_DIR = OUTPUT_DIR  # For cross-referencing Reddit/HN data

# Exa API configuration — key loaded from ~/.ai_secrets.json via lib/secrets.py
EXA_API_KEY = get_key("EXA_API_KEY")
EXA_BASE_URL = "https://api.exa.ai"

# Search queries for content discovery
SEARCH_QUERIES = [
    {
        "query": "Claude Code AI development workflow",
        "category": "claude_code",
        "label": "Claude Code",
    },
    {
        "query": "AI automation tools for business productivity",
        "category": "ai_business",
        "label": "AI Business Tools",
    },
    {
        "query": "AI agent workflow automation 2026",
        "category": "ai_agents",
        "label": "AI Agents",
    },
    {
        "query": "best AI tools for developers coding",
        "category": "dev_tools",
        "label": "Developer AI Tools",
    },
    {
        "query": "AI content creation automation marketing",
        "category": "content_ai",
        "label": "Content AI",
    },
    {
        "query": "Anthropic Claude new features capabilities",
        "category": "anthropic",
        "label": "Anthropic/Claude",
    },
]

# How many days back to search
LOOKBACK_DAYS = 7
# Results per query
RESULTS_PER_QUERY = 20
# Rate limit between Exa requests
RATE_LIMIT_SECONDS = 1


def exa_search(query, category="news", num_results=20, start_date=None):
    """
    Search Exa API with content highlights.

    Args:
        query: Search query string
        category: Exa category filter (news, company, research paper, etc.)
        num_results: Number of results to return
        start_date: ISO date string for filtering recent content

    Returns:
        List of result dicts with title, url, highlights, etc.
    """
    url = f"{EXA_BASE_URL}/search"
    headers = {
        "x-api-key": EXA_API_KEY,
        "Content-Type": "application/json",
    }

    body = {
        "query": query,
        "numResults": num_results,
        "contents": {
            "highlights": {
                "numSentences": 3,
                "highlightsPerUrl": 2,
            },
            "text": {
                "maxCharacters": 500,
            },
        },
    }

    # Add date filter if provided
    if start_date:
        body["startPublishedDate"] = start_date

    # Add category if it's a valid Exa category
    if category in ("news", "company", "research paper", "personal site"):
        body["category"] = category

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        body_text = e.response.text[:200] if e.response is not None else ""
        print(f"    Exa API error ({status}): {body_text}")
        return []
    except Exception as e:
        print(f"    Exa search error: {e}")
        return []


def exa_find_similar(url_to_match, num_results=10, start_date=None):
    """
    Find content similar to a given URL using Exa's find-similar endpoint.

    Args:
        url_to_match: URL to find similar content for
        num_results: Number of results
        start_date: ISO date filter

    Returns:
        List of similar result dicts
    """
    url = f"{EXA_BASE_URL}/findSimilar"
    headers = {
        "x-api-key": EXA_API_KEY,
        "Content-Type": "application/json",
    }

    body = {
        "url": url_to_match,
        "numResults": num_results,
        "contents": {
            "highlights": {
                "numSentences": 2,
                "highlightsPerUrl": 1,
            },
        },
    }

    if start_date:
        body["startPublishedDate"] = start_date

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("results", [])
    except Exception as e:
        print(f"    Exa find-similar error: {e}")
        return []


def normalize_url(url):
    """Normalize URL for deduplication (strip query params, trailing slash)."""
    if not url:
        return ""
    parsed = urlparse(url)
    # Keep scheme + netloc + path, strip query and fragment
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"
    return normalized.lower()


def calculate_discovery_score(result, cross_platform_count=0):
    """
    Calculate a content discovery score based on available signals.

    Factors:
    - Exa relevance (implicit in result ordering)
    - Highlight quality (has highlights = more relevant)
    - Cross-platform presence (appears in Reddit/HN too)
    - Recency
    """
    score = 50  # Base score

    # Has highlights (content extraction succeeded)
    highlights = result.get("highlights", [])
    if highlights:
        score += 10
        # Longer highlights suggest more substantial content
        total_chars = sum(len(h) for h in highlights)
        if total_chars > 200:
            score += 10

    # Has text content
    text = result.get("text", "")
    if len(text) > 100:
        score += 5

    # Cross-platform bonus (found in Reddit/HN too)
    score += cross_platform_count * 20

    # Recency bonus
    published = result.get("publishedDate", "")
    if published:
        try:
            pub_date = datetime.fromisoformat(published.replace("Z", "+00:00"))
            age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
            if age_hours < 24:
                score += 20  # Published today
            elif age_hours < 72:
                score += 15  # Last 3 days
            elif age_hours < 168:
                score += 10  # Last week
        except (ValueError, TypeError):
            pass

    return min(score, 100)


def load_existing_urls():
    """
    Load URLs from existing Reddit and HN source data to cross-reference.
    Returns a dict of normalized_url -> {sources, total_engagement}.
    """
    url_map = {}

    # Load Reddit data
    reddit_file = SOURCES_DIR / "reddit.json"
    if reddit_file.exists():
        try:
            data = json.loads(reddit_file.read_text())
            for item in data.get("items", []):
                raw_url = item.get("url", "")
                # Skip reddit self-posts
                if "reddit.com" in raw_url:
                    continue
                norm = normalize_url(raw_url)
                if norm:
                    if norm not in url_map:
                        url_map[norm] = {"sources": [], "total_engagement": 0}
                    url_map[norm]["sources"].append(f"r/{item.get('subreddit', 'unknown')}")
                    url_map[norm]["total_engagement"] += item.get("score", 0) + item.get("num_comments", 0)
        except Exception as e:
            print(f"  Warning: Could not load Reddit data: {e}")

    # Load HN data
    hn_file = SOURCES_DIR / "hackernews.json"
    if hn_file.exists():
        try:
            data = json.loads(hn_file.read_text())
            for item in data.get("items", []):
                raw_url = item.get("url", "")
                if not raw_url or "news.ycombinator.com" in raw_url:
                    continue
                norm = normalize_url(raw_url)
                if norm:
                    if norm not in url_map:
                        url_map[norm] = {"sources": [], "total_engagement": 0}
                    url_map[norm]["sources"].append("hackernews")
                    url_map[norm]["total_engagement"] += item.get("score", 0) + item.get("num_comments", 0)
        except Exception as e:
            print(f"  Warning: Could not load HN data: {e}")

    return url_map


def extract_top_shared_urls(url_map, limit=20):
    """
    From cross-referenced URL map, find URLs shared across multiple platforms
    or with highest total engagement.
    """
    # Filter to URLs appearing in 2+ sources or with high engagement
    multi_source = {
        url: info for url, info in url_map.items()
        if len(set(info["sources"])) >= 2 or info["total_engagement"] >= 100
    }

    # Sort by engagement
    sorted_urls = sorted(
        multi_source.items(),
        key=lambda x: (len(set(x[1]["sources"])), x[1]["total_engagement"]),
        reverse=True,
    )

    return sorted_urls[:limit]


def collect():
    print("[Content Discovery] Starting collection...")
    all_items = []
    seen_urls = set()

    # Calculate date filter (past N days)
    start_date = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT00:00:00.000Z")

    # Load existing Reddit/HN URLs for cross-referencing
    print("  Loading cross-reference data from Reddit/HN...")
    url_map = load_existing_urls()
    print(f"    Found {len(url_map)} unique URLs from existing sources")

    # Phase 1: Exa API search for each query
    print("\n  Phase 1: Exa API content discovery")
    for search_config in SEARCH_QUERIES:
        query = search_config["query"]
        category = search_config["category"]
        label = search_config["label"]

        print(f"    Searching: '{label}'...")
        results = exa_search(
            query=query,
            num_results=RESULTS_PER_QUERY,
            start_date=start_date,
        )

        added = 0
        for result in results:
            url = result.get("url", "")
            norm_url = normalize_url(url)

            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)

            # Check cross-platform presence
            cross_info = url_map.get(norm_url, {"sources": [], "total_engagement": 0})
            cross_count = len(set(cross_info["sources"]))

            highlights = result.get("highlights", [])
            text_preview = result.get("text", "")

            discovery_score = calculate_discovery_score(result, cross_count)

            item = {
                "title": result.get("title", "Untitled"),
                "url": url,
                "score": discovery_score,
                "source_detail": f"exa_{category}",
                "date": result.get("publishedDate", datetime.now(timezone.utc).isoformat()),
                "highlights": highlights[:2] if highlights else [],
                "text_preview": text_preview[:300] if text_preview else "",
                "search_category": category,
                "search_label": label,
                "author": result.get("author", ""),
                "domain": urlparse(url).netloc if url else "",
                "cross_platform_sources": cross_info["sources"][:5],
                "cross_platform_engagement": cross_info["total_engagement"],
                "discovery_method": "exa_search",
            }

            all_items.append(item)
            added += 1

        print(f"      Got {len(results)} results, {added} new")
        time.sleep(RATE_LIMIT_SECONDS)

    # Phase 2: Find top shared URLs from Reddit/HN cross-reference
    print("\n  Phase 2: Cross-platform most-shared content")
    top_shared = extract_top_shared_urls(url_map, limit=20)
    print(f"    Found {len(top_shared)} multi-platform or high-engagement URLs")

    for norm_url, info in top_shared:
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        unique_sources = list(set(info["sources"]))

        item = {
            "title": f"Shared across {', '.join(unique_sources[:3])}",
            "url": norm_url,
            "score": min(50 + info["total_engagement"] // 10 + len(unique_sources) * 15, 100),
            "source_detail": "cross_platform",
            "date": datetime.now(timezone.utc).isoformat(),
            "highlights": [],
            "text_preview": "",
            "search_category": "cross_platform",
            "search_label": "Most Shared",
            "author": "",
            "domain": urlparse(norm_url).netloc if norm_url else "",
            "cross_platform_sources": unique_sources[:5],
            "cross_platform_engagement": info["total_engagement"],
            "discovery_method": "cross_platform_analysis",
        }

        all_items.append(item)

    # Phase 3: Use Exa find-similar on the top cross-platform URLs
    print("\n  Phase 3: Finding similar content to top shared URLs")
    similar_candidates = [
        url for url, info in top_shared[:3]
        if len(set(info["sources"])) >= 2
    ]

    for candidate_url in similar_candidates:
        print(f"    Finding content similar to: {urlparse(candidate_url).netloc}...")
        similar_results = exa_find_similar(
            url_to_match=candidate_url,
            num_results=5,
            start_date=start_date,
        )

        for result in similar_results:
            url = result.get("url", "")
            norm_url = normalize_url(url)

            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)

            highlights = result.get("highlights", [])

            item = {
                "title": result.get("title", "Untitled"),
                "url": url,
                "score": calculate_discovery_score(result, 0),
                "source_detail": "exa_similar",
                "date": result.get("publishedDate", datetime.now(timezone.utc).isoformat()),
                "highlights": highlights[:2] if highlights else [],
                "text_preview": result.get("text", "")[:300],
                "search_category": "similar_content",
                "search_label": "Similar Content",
                "author": result.get("author", ""),
                "domain": urlparse(url).netloc if url else "",
                "cross_platform_sources": [],
                "cross_platform_engagement": 0,
                "discovery_method": "exa_find_similar",
            }

            all_items.append(item)

        time.sleep(RATE_LIMIT_SECONDS)

    # Sort by discovery score
    all_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Build output
    output = {
        "source": "content_discovery",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
        "summary": {
            "total_discovered": len(all_items),
            "from_exa_search": len([i for i in all_items if i["discovery_method"] == "exa_search"]),
            "from_cross_platform": len([i for i in all_items if i["discovery_method"] == "cross_platform_analysis"]),
            "from_similar": len([i for i in all_items if i["discovery_method"] == "exa_find_similar"]),
            "search_queries": [q["label"] for q in SEARCH_QUERIES],
            "lookback_days": LOOKBACK_DAYS,
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n[Content Discovery] Done. {len(all_items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
