#!/usr/bin/env python3
"""
SocialBlade Collector — pulls Instagram analytics for monitored accounts.

Uses the SocialBlade Business API if credentials are available.
Falls back to scraping SocialBlade pages via Apify/Firecrawl.

API setup:
  1. Register at https://socialblade.com/developers
  2. Get your client_id and token from the Developer Console
  3. Add to ~/.ai_secrets.json as:
     {"socialblade_client_id": "...", "socialblade_token": "..."}

Outputs: ~/content-engine/trending-data/sources/socialblade.json
"""

import json
import sys
import time
from datetime import datetime, timezone
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
OUTPUT_FILE = OUTPUT_DIR / "socialblade.json"

# Instagram accounts to monitor (AI/tech creators in the content engine)
MONITORED_ACCOUNTS = [
    "openai",
    "anthropicai",
    "googleai",
    "nvidia",
    "huggingface",
    "ycombinator",
    "techmeme",
    "firstround",
    "a16z",
    "sequoia",
    "productschool",
    "reaborneai",
    "sababorneai",
    "ericsiu",
    "garyvee",
    "dhaborneai",
    "commandshift.ai",
    "christianmccloud",
]

API_BASE = "https://matrix.sbapis.com/b/instagram/statistics"
USER_AGENT = "CCO-Dashboard/1.0 (content-engine collector)"
RATE_LIMIT_SECONDS = 2


def load_secrets():
    """Load SocialBlade credentials from centralized secrets loader."""
    return {
        "client_id": get_key("SOCIALBLADE_CLIENT_ID"),
        "token": get_key("SOCIALBLADE_TOKEN"),
    }


def fetch_via_api(username, client_id, token):
    """Fetch Instagram stats from SocialBlade Business API."""
    params = {
        "query": username,
        "clientid": client_id,
        "token": token,
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(API_BASE, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if not data or "error" in data:
            return None

        # Extract key metrics from the API response
        return {
            "title": f"@{username}",
            "username": username,
            "score": data.get("followers", 0),
            "url": f"https://www.instagram.com/{username}/",
            "source_detail": "socialblade_api",
            "date": datetime.now(timezone.utc).isoformat(),
            "followers": data.get("followers", 0),
            "following": data.get("following", 0),
            "media_count": data.get("media", 0),
            "engagement_rate": data.get("engagement_rate", 0),
            "follower_growth_30d": data.get("followers_30d", 0),
            "follower_growth_7d": data.get("followers_7d", 0),
            "follower_growth_1d": data.get("followers_1d", 0),
            "avg_likes": data.get("avg_likes", 0),
            "avg_comments": data.get("avg_comments", 0),
        }
    except Exception as e:
        print(f"    API fetch failed for @{username}: {e}")
        return None


def fetch_via_scrape(username):
    """
    Fallback: scrape SocialBlade's public page for basic stats.
    Uses a simple HTTP request to the SocialBlade user page.
    Returns limited data compared to the API.
    """
    url = f"https://socialblade.com/instagram/user/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)

        # SocialBlade blocks most scrapers — this is expected to fail
        # If it works, we'd parse the HTML for stats
        if resp.status_code == 200 and "followers" in resp.text.lower():
            # Basic parsing — SocialBlade HTML is not well structured for scraping
            # This is a best-effort fallback
            return {
                "title": f"@{username}",
                "username": username,
                "score": 0,
                "url": f"https://www.instagram.com/{username}/",
                "source_detail": "socialblade_scrape",
                "date": datetime.now(timezone.utc).isoformat(),
                "note": "Limited data — scrape fallback. Add SocialBlade API creds for full data.",
            }
        return None

    except Exception:
        return None


def fetch_via_instagram_public(username):
    """
    Minimal fallback: use Instagram's public data endpoint.
    This returns very basic profile info without authentication.
    """
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "X-IG-App-ID": "936619743392459",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None

        data = resp.json()
        user = data.get("data", {}).get("user", {})
        if not user:
            return None

        followers = user.get("edge_followed_by", {}).get("count", 0)
        following = user.get("edge_follow", {}).get("count", 0)
        media_count = user.get("edge_owner_to_timeline_media", {}).get("count", 0)

        return {
            "title": f"@{username}",
            "username": username,
            "score": followers,
            "url": f"https://www.instagram.com/{username}/",
            "source_detail": "instagram_public_api",
            "date": datetime.now(timezone.utc).isoformat(),
            "followers": followers,
            "following": following,
            "media_count": media_count,
            "full_name": user.get("full_name", ""),
            "biography": (user.get("biography", "") or "")[:100],
            "is_verified": user.get("is_verified", False),
        }

    except Exception:
        return None


def collect():
    print("[SocialBlade] Starting collection...")
    items = []

    secrets = load_secrets()
    has_api = bool(secrets["client_id"] and secrets["token"])

    if has_api:
        print("  Using SocialBlade Business API...")
    else:
        print("  No SocialBlade API credentials found.")
        print("  Falling back to Instagram public data (limited).")
        print("  For full data, add socialblade_client_id and socialblade_token to ~/.ai_secrets.json")

    for username in MONITORED_ACCOUNTS:
        print(f"  Fetching @{username}...")
        result = None

        if has_api:
            result = fetch_via_api(username, secrets["client_id"], secrets["token"])

        if result is None:
            result = fetch_via_instagram_public(username)

        if result is None:
            result = fetch_via_scrape(username)

        if result:
            items.append(result)
            print(f"    OK — {result.get('followers', '?')} followers")
        else:
            # Still record the account even if we couldn't fetch data
            items.append({
                "title": f"@{username}",
                "username": username,
                "score": 0,
                "url": f"https://www.instagram.com/{username}/",
                "source_detail": "unavailable",
                "date": datetime.now(timezone.utc).isoformat(),
                "note": "Could not fetch data. SocialBlade API or Instagram login required.",
            })
            print(f"    SKIP — could not fetch data")

        time.sleep(RATE_LIMIT_SECONDS)

    output = {
        "source": "socialblade",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
        "api_method": "socialblade_api" if has_api else "fallback",
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"[SocialBlade] Done. {len(items)} accounts processed, saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
