#!/usr/bin/env python3
"""
TikTok Creative Center Collector — pulls trending hashtags, songs, and
creators from TikTok's Creative Center via Apify actor.

Uses the clockworks/tiktok-trends-scraper Apify actor (no cookies needed,
100% success rate). Collects trending hashtags, songs, and creators
in the US market for AI/tech content intelligence.

Outputs: ~/content-engine/trending-data/sources/tiktok-creative.json

Usage:
  cd ~/content-engine && uv run python3 scripts/collectors/tiktok-creative.py

Requirements:
  - APIFY_TOKEN in ~/.ai_secrets.json
  - requests library (uv add requests)
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

BASE_DIR = Path.home() / "content-engine"
OUTPUT_DIR = BASE_DIR / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "tiktok-creative.json"

# Apify configuration — clockworks actor doesn't need cookies
APIFY_ACTOR = "clockworks/tiktok-trends-scraper"
APIFY_BASE_URL = "https://api.apify.com/v2"

# Default configuration
DEFAULT_COUNTRY = "US"
DEFAULT_PERIOD = "7"  # 7 days for freshest trends
RESULTS_LIMIT = 50  # Max ~100 for hashtags/songs, 500 for creators/videos
POLL_INTERVAL_SECONDS = 10
MAX_POLL_ATTEMPTS = 60  # 10 minutes max wait


def load_apify_token():
    """Load Apify API token from centralized secrets loader."""
    token = get_key("APIFY_TOKEN")
    if not token:
        print("  Warning: APIFY_TOKEN not found in ~/.ai_secrets.json")
        return None
    return token


def run_apify_actor(token, actor_input):
    """
    Run the TikTok Trends Scraper Apify actor and wait for results.

    Args:
        token: Apify API token
        actor_input: Dict of input parameters for the actor

    Returns:
        List of result items
    """
    actor_id = APIFY_ACTOR.replace("/", "~")
    url = f"{APIFY_BASE_URL}/acts/{actor_id}/runs"

    try:
        # Start the actor run
        resp = requests.post(
            url,
            json=actor_input,
            params={"token": token},
            timeout=30,
        )
        resp.raise_for_status()
        run_data = resp.json().get("data", {})
        run_id = run_data.get("id")
        dataset_id = run_data.get("defaultDatasetId")

        if not run_id:
            print(f"    No run ID returned")
            return []

        print(f"    Actor run started: {run_id}")

        # Poll for completion
        status = ""
        status_resp = None
        for attempt in range(MAX_POLL_ATTEMPTS):
            time.sleep(POLL_INTERVAL_SECONDS)

            status_resp = requests.get(
                f"{APIFY_BASE_URL}/actor-runs/{run_id}",
                params={"token": token},
                timeout=15,
            )
            status_resp.raise_for_status()
            run_status = status_resp.json().get("data", {})
            status = run_status.get("status", "")

            if status == "SUCCEEDED":
                dataset_id = run_status.get("defaultDatasetId", dataset_id)
                print(f"    Run completed successfully")
                break
            elif status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"    Run failed with status: {status}")
                return []
            else:
                if attempt % 3 == 0:
                    print(f"    Waiting... status: {status}")
        else:
            print(f"    Run timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s")
            return []

        # Get results from dataset
        if not dataset_id:
            print(f"    No dataset ID found")
            return []

        items_resp = requests.get(
            f"{APIFY_BASE_URL}/datasets/{dataset_id}/items",
            params={"token": token, "format": "json"},
            timeout=30,
        )
        items_resp.raise_for_status()
        items = items_resp.json()

        return items if isinstance(items, list) else []

    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response is not None else "unknown"
        body = e.response.text[:200] if e.response is not None else ""
        print(f"    Apify API error ({status_code}): {body}")
        return []
    except Exception as e:
        print(f"    Apify error: {e}")
        return []


def safe_int(val, default=0):
    """Safely convert a value to int."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def normalize_hashtag_item(raw):
    """Normalize a hashtag item from clockworks actor output.

    Clockworks fields: id, name, countryCode, isPromoted, rank, rankDiff,
    markedAsNew, trendingHistogram, type, url, videoCount, viewCount, industryName
    """
    name = raw.get("name", "").lstrip("#")

    views = safe_int(raw.get("viewCount", 0))
    posts = safe_int(raw.get("videoCount", 0))
    rank = safe_int(raw.get("rank", 0))
    rank_diff = safe_int(raw.get("rankDiff", 0))
    is_new = raw.get("markedAsNew", False)
    industry = raw.get("industryName", "")
    histogram = raw.get("trendingHistogram", [])
    item_url = raw.get("url", f"https://www.tiktok.com/tag/{name}" if name else "")

    # Engagement score: views + weighted posts
    engagement = views + (posts * 100)

    # Determine trend direction from rankDiff
    if rank_diff > 0:
        trend_type = "rising"
    elif rank_diff < 0:
        trend_type = "falling"
    elif is_new:
        trend_type = "new"
    else:
        trend_type = "stable"

    return {
        "title": f"#{name}" if name else "",
        "url": item_url,
        "score": engagement,
        "source_detail": "tiktok_hashtag",
        "date": datetime.now(timezone.utc).isoformat(),
        "views": views,
        "posts": posts,
        "rank": rank,
        "rank_diff": rank_diff,
        "is_new_to_top_100": bool(is_new),
        "trend_type": trend_type,
        "industry": industry,
        "trending_histogram": histogram,
        "content_type": "hashtag",
    }


def normalize_song_item(raw):
    """Normalize a song/sound item from clockworks actor output.

    Clockworks fields: id, name, countryCode, isPromoted, rankDiff,
    markedAsNew, rank, trendingHistogram, type, url, audioLink,
    coverUrl, durationSec, author
    """
    title = raw.get("name", "")
    artist = raw.get("author", "")
    clip_id = raw.get("id", "")
    rank = safe_int(raw.get("rank", 0))
    rank_diff = safe_int(raw.get("rankDiff", 0))
    is_new = raw.get("markedAsNew", False)
    item_url = raw.get("url", "")
    duration = safe_int(raw.get("durationSec", 0))
    histogram = raw.get("trendingHistogram", [])

    display_title = f"{title} - {artist}" if artist and title else (title or artist or "Unknown")

    if rank_diff > 0:
        trend_type = "rising"
    elif rank_diff < 0:
        trend_type = "falling"
    elif is_new:
        trend_type = "new"
    else:
        trend_type = "stable"

    return {
        "title": display_title,
        "url": item_url,
        "score": max(101 - rank, 1) if rank > 0 else 50,
        "source_detail": "tiktok_song",
        "date": datetime.now(timezone.utc).isoformat(),
        "artist": artist,
        "song_name": title,
        "clip_id": str(clip_id),
        "rank": rank,
        "rank_diff": rank_diff,
        "is_new_to_top_100": bool(is_new),
        "trend_type": trend_type,
        "duration_sec": duration,
        "trending_histogram": histogram,
        "content_type": "song",
    }


def normalize_creator_item(raw):
    """Normalize a creator item from clockworks actor output.

    Clockworks fields: id, avatar, countryCode, followerCount,
    likedCount, name, type, url, rank, relatedVideos
    """
    name = raw.get("name", "")
    item_url = raw.get("url", "")
    followers = safe_int(raw.get("followerCount", 0))
    liked = safe_int(raw.get("likedCount", 0))
    rank = safe_int(raw.get("rank", 0))
    avatar = raw.get("avatar", "")

    # Extract unique_id from URL if available
    unique_id = ""
    if item_url and "/@" in item_url:
        unique_id = item_url.split("/@")[-1].rstrip("/")

    display_name = f"@{unique_id}" if unique_id else name

    return {
        "title": display_name,
        "url": item_url,
        "score": followers,
        "source_detail": "tiktok_creator",
        "date": datetime.now(timezone.utc).isoformat(),
        "nickname": name,
        "unique_id": unique_id,
        "followers": followers,
        "liked_count": liked,
        "rank": rank,
        "avatar_url": avatar,
        "content_type": "creator",
    }


def normalize_video_item(raw):
    """Normalize a trending video item from clockworks actor output."""
    title = raw.get("title", raw.get("text", raw.get("desc", "")))
    video_id = raw.get("id", raw.get("video_id", raw.get("videoId", "")))
    views = raw.get("vv", raw.get("views", raw.get("play_count", 0)))
    likes = raw.get("like", raw.get("likes", raw.get("digg_count", 0)))
    comments = raw.get("comment", raw.get("comments", raw.get("comment_count", 0)))
    reposts = raw.get("repost", raw.get("share_count", 0))
    author = raw.get("author", raw.get("creator", ""))

    try:
        views_num = int(views) if views else 0
    except (ValueError, TypeError):
        views_num = 0
    try:
        likes_num = int(likes) if likes else 0
    except (ValueError, TypeError):
        likes_num = 0

    display_title = title[:100] if title else f"TikTok Video {video_id}"

    return {
        "title": display_title,
        "url": f"https://www.tiktok.com/@/video/{video_id}" if video_id else "",
        "score": views_num,
        "source_detail": "tiktok_video",
        "date": datetime.now(timezone.utc).isoformat(),
        "views": views_num,
        "likes": likes_num,
        "comments": comments,
        "reposts": reposts,
        "author": author if isinstance(author, str) else "",
        "content_type": "video",
    }


def collect():
    print("[TikTok Creative Center] Starting collection...")
    all_items = []

    # Load Apify token
    token = load_apify_token()
    if not token:
        print("  ERROR: APIFY_TOKEN required for TikTok collection")
        print("  Saving empty result file")
        output = {
            "source": "tiktok_creative",
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "items": [],
            "summary": {"error": "APIFY_TOKEN not found"},
        }
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(output, indent=2))
        return output

    # Run actor with hashtags + songs + creators in one call
    # The clockworks actor supports enabling multiple data types per run
    print("  Starting Apify actor (hashtags + songs + creators)...")
    actor_input = {
        # Hashtags
        "adsScrapeHashtags": True,
        "adsCountryCode": DEFAULT_COUNTRY,
        "adsTimeRange": DEFAULT_PERIOD,
        "adsHashtagIndustry": "Tech & Electronics",
        "resultsPerPage": RESULTS_LIMIT,
        # Songs
        "adsScrapeSounds": True,
        "adsSoundsCountryCode": DEFAULT_COUNTRY,
        "adsRankType": "popular",
        # Creators
        "adsScrapeCreators": True,
        "adsCreatorsCountryCode": DEFAULT_COUNTRY,
        "adsSortCreatorsBy": "engagement",
        # Videos
        "adsScrapeVideos": False,  # Skip videos to save cost
    }

    raw_items = run_apify_actor(token, actor_input)
    print(f"  Got {len(raw_items)} raw items from Apify")

    # Categorize and normalize items using the 'type' field from clockworks actor
    for raw in raw_items:
        item_type = raw.get("type", "")

        if item_type == "hashtag":
            item = normalize_hashtag_item(raw)
        elif item_type == "sound":
            item = normalize_song_item(raw)
        elif item_type == "creator":
            item = normalize_creator_item(raw)
        elif item_type == "video":
            item = normalize_video_item(raw)
        else:
            # Fallback detection by field presence
            if "viewCount" in raw and "videoCount" in raw:
                item = normalize_hashtag_item(raw)
            elif "followerCount" in raw:
                item = normalize_creator_item(raw)
            elif "audioLink" in raw or "durationSec" in raw:
                item = normalize_song_item(raw)
            else:
                continue

        if item.get("title"):
            all_items.append(item)

    # Count by type
    type_counts = {}
    for item in all_items:
        ct = item.get("content_type", "unknown")
        type_counts[ct] = type_counts.get(ct, 0) + 1

    print(f"\n  Normalized items by type:")
    for ct, count in sorted(type_counts.items()):
        print(f"    {ct}: {count}")

    # Build output
    output = {
        "source": "tiktok_creative",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "items": all_items,
        "summary": {
            "total_items": len(all_items),
            "hashtags": type_counts.get("hashtag", 0),
            "songs": type_counts.get("song", 0),
            "creators": type_counts.get("creator", 0),
            "videos": type_counts.get("video", 0),
            "data_source": "apify",
            "apify_actor": APIFY_ACTOR,
            "country": DEFAULT_COUNTRY,
            "period_days": int(DEFAULT_PERIOD),
        },
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n[TikTok Creative Center] Done. {len(all_items)} items saved to {OUTPUT_FILE}")
    return output


if __name__ == "__main__":
    collect()
