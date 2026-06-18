#!/usr/bin/env python3
"""
Product Hunt Collector — pulls today's top products using either:
  1. Product Hunt GraphQL API v2 (requires API token)
  2. Fallback: Apify actor scraper (if API token not available)

API token setup:
  1. Go to https://api.producthunt.com/v2/oauth/applications
  2. Create an application
  3. Generate a Developer Token
  4. Add to ~/.ai_secrets.json as: {"producthunt_token": "YOUR_TOKEN"}

Outputs: ~/content-engine/trending-data/sources/producthunt.json
"""

import json
import sys
import os
from datetime import datetime, timezone, date
from pathlib import Path

# Centralized API key loader
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests")
    sys.exit(1)

OUTPUT_DIR = Path.home() / "content-engine" / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "producthunt.json"

GRAPHQL_URL = "https://api.producthunt.com/v2/api/graphql"
USER_AGENT = "CCO-Dashboard/1.0 (content-engine collector)"


def load_api_token():
    """Load Product Hunt API token from centralized secrets loader."""
    return get_key("PRODUCTHUNT_TOKEN")


def fetch_via_graphql(token):
    """Fetch today's top products via Product Hunt GraphQL API."""
    today = date.today().isoformat()

    query = """
    query {
      posts(order: VOTES, first: 20, postedAfter: "%s") {
        edges {
          node {
            id
            name
            tagline
            description
            votesCount
            commentsCount
            url
            website
            createdAt
            topics {
              edges {
                node {
                  name
                }
              }
            }
            thumbnail {
              url
            }
          }
        }
      }
    }
    """ % today

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    try:
        resp = requests.post(
            GRAPHQL_URL,
            json={"query": query},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            print(f"  GraphQL errors: {data['errors']}")
            return None

        items = []
        edges = data.get("data", {}).get("posts", {}).get("edges", [])

        for edge in edges:
            node = edge.get("node", {})
            topics = [
                t["node"]["name"]
                for t in node.get("topics", {}).get("edges", [])
            ]

            items.append({
                "title": node.get("name", ""),
                "score": node.get("votesCount", 0),
                "url": node.get("url", ""),
                "website": node.get("website", ""),
                "source_detail": "producthunt_api",
                "date": node.get("createdAt", ""),
                "tagline": node.get("tagline", ""),
                "description": node.get("description", "")[:200] if node.get("description") else "",
                "num_comments": node.get("commentsCount", 0),
                "topics": topics,
                "thumbnail": node.get("thumbnail", {}).get("url", ""),
            })

        return items

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            print("  API token invalid or expired")
        else:
            print(f"  HTTP error: {e}")
        return None
    except Exception as e:
        print(f"  GraphQL fetch failed: {e}")
        return None


def fetch_via_scrape():
    """
    Fallback: scrape Product Hunt homepage for today's products.
    Uses the public (no-auth) JSON feed.
    """
    print("  Trying public scrape fallback...")
    today_str = date.today().isoformat()

    # Product Hunt has a public feed at their website
    url = f"https://www.producthunt.com/frontend/graphql"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Origin": "https://www.producthunt.com",
        "Referer": "https://www.producthunt.com/",
    }

    query = {
        "operationName": "HomePage",
        "variables": {"cursor": None},
        "query": """
        query HomePage($cursor: String) {
          homefeed(first: 20, after: $cursor) {
            edges {
              node {
                ... on Post {
                  id
                  name
                  tagline
                  votesCount
                  commentsCount
                  slug
                  createdAt
                  topics(first: 5) {
                    edges {
                      node {
                        name
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """,
    }

    try:
        resp = requests.post(url, json=query, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        items = []
        edges = data.get("data", {}).get("homefeed", {}).get("edges", [])

        for edge in edges:
            node = edge.get("node", {})
            if not node.get("name"):
                continue
            topics = [
                t["node"]["name"]
                for t in node.get("topics", {}).get("edges", [])
            ]
            slug = node.get("slug", "")

            items.append({
                "title": node.get("name", ""),
                "score": node.get("votesCount", 0),
                "url": f"https://www.producthunt.com/posts/{slug}" if slug else "",
                "source_detail": "producthunt_scrape",
                "date": node.get("createdAt", today_str),
                "tagline": node.get("tagline", ""),
                "num_comments": node.get("commentsCount", 0),
                "topics": topics,
            })

        return items if items else None

    except Exception as e:
        print(f"  Public scrape failed: {e}")
        return None


def collect():
    print("[Product Hunt] Starting collection...")
    items = None

    # Try GraphQL API first
    token = load_api_token()
    if token:
        print("  Using GraphQL API with token...")
        items = fetch_via_graphql(token)
    else:
        print("  No API token found in ~/.ai_secrets.json (key: producthunt_token)")

    # Fallback to scraping
    if items is None:
        items = fetch_via_scrape()

    if items is None:
        print("  WARNING: Could not fetch Product Hunt data.")
        print("  To fix: add 'producthunt_token' to ~/.ai_secrets.json")
        print("  Get a token at: https://api.producthunt.com/v2/oauth/applications")
        items = []

    # Sort by votes
    items.sort(key=lambda x: x.get("score", 0), reverse=True)

    output = {
        "source": "producthunt",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"[Product Hunt] Done. {len(items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
