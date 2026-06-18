#!/usr/bin/env python3
"""
Generate Competitor Research pages (cco-dashboard/src/data/research/{slug}.json)
for the tracked FieldWork creators, from scraped reels + an Apify profile-details
fetch. Replaces the stale christianmcld / cooper-simson research.

Usage: uv run python3 generate_research.py
Requires: APIFY_TOKEN
"""

import json
import os
import re
import sys
import urllib.request
import statistics
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
RAW = ROOT / "output" / "outliers-raw.json"
RESEARCH_DIR = DASHDIR / "src" / "data" / "research"
THUMB_DIR = DASHDIR / "public" / "research-thumbs"
APIFY = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"


def download_img(url, name):
    """Download an IG CDN image locally (CDN URLs expire / block hotlinking).
    Returns the public web path, or '' on failure."""
    if not url:
        return ""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / name
    web = f"/research-thumbs/{name}"
    if dest.exists():
        return web
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if data:
            dest.write_bytes(data)
            return web
    except Exception as e:
        print(f"    img download failed ({name}): {e}")
    return ""


def engagement(r):
    return int((r.get("source_likes") or 0) + (r.get("source_comments") or 0))


def fetch_profiles(handles, token):
    payload = {
        "directUrls": [f"https://www.instagram.com/{h}/" for h in handles],
        "resultsType": "details",
        "resultsLimit": 1,
    }
    req = urllib.request.Request(
        f"{APIFY}?token={token}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  ! profile fetch failed: {e}")
        return {}
    out = {}
    for it in (data if isinstance(data, list) else []):
        u = it.get("username")
        if u:
            out[u] = it
    return out


def hashtags(caption):
    return re.findall(r"#(\w+)", caption or "")


def build(handle, reels, profile):
    reels = sorted(reels, key=engagement, reverse=True)
    durations = defaultdict(lambda: [0, 0])
    dur_defs = [("<30s", 0, 30), ("30-45s", 30, 45), ("45-60s", 45, 60), ("60-90s", 60, 90), (">90s", 90, 1e9)]
    for r in reels:
        d = r.get("video_duration")
        if d is None:
            continue
        for label, lo, hi in dur_defs:
            if lo <= d < hi:
                durations[label][0] += engagement(r)
                durations[label][1] += 1
    best_fmt = max(durations.items(), key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 0, default=("n/a", [0, 0]))[0]

    tags = Counter()
    for r in reels:
        tags.update(hashtags(r.get("source_caption", "")))
    themes = [t for t, _ in tags.most_common(6)] or ["(no hashtags)"]

    # posting frequency
    ts = sorted([r["timestamp"] for r in reels if r.get("timestamp")])
    freq = "n/a"
    if len(ts) >= 2:
        days = (datetime.fromisoformat(ts[-1]) - datetime.fromisoformat(ts[0])).days or 1
        freq = f"{round(len(ts) / (days / 7), 1)} reels/week"

    viral = [{
        "hook": (r.get("source_caption") or "").split("\n")[0][:90] or "(no caption)",
        "caption": r.get("source_caption", ""),
        "likes": r.get("source_likes"),
        "comments": r.get("source_comments"),
        "engagement": engagement(r),
        "date": r.get("timestamp"),
        "url": r.get("source_url"),
        "thumbnail": download_img(r.get("display_url"), f"{r.get('source_video_id')}.jpg"),
    } for r in reels[:12]]

    return {
        "query": f"@{handle}",
        "slug": handle,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "social_profiles": {
            "instagram": {
                "handle": handle,
                "full_name": profile.get("fullName") or handle,
                "followers": profile.get("followersCount"),
                "following": profile.get("followsCount"),
                "posts_count": profile.get("postsCount"),
                "bio": profile.get("biography", ""),
                "profile_pic": download_img(profile.get("profilePicUrlHD") or profile.get("profilePicUrl"), f"{handle}-profile.jpg"),
                "is_verified": profile.get("verified", False),
                "category": profile.get("businessCategoryName") or profile.get("category") or "",
            },
            "website": profile.get("externalUrl") or "",
        },
        "viral_posts": viral,
        "ads": [],
        "competitors": [],   # filled by caller (other tracked creators)
        "analysis": {
            "top_hooks": [v["hook"] for v in viral[:5]],
            "content_themes": themes,
            "best_performing_format": best_fmt,
            "posting_frequency": freq,
            "ad_strategy_notes": "No Meta ads tracked for this creator yet.",
        },
    }


def main():
    token = get_key("APIFY_TOKEN")
    raw = json.loads(RAW.read_text())
    reels_by_acct = defaultdict(list)
    for r in raw.get("reels", []):
        reels_by_acct[r["source_account"]].append(r)
    handles = list(reels_by_acct.keys())

    print(f"Fetching profile details for {handles}...")
    profiles = fetch_profiles(handles, token)

    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    # remove stale research
    for old in RESEARCH_DIR.glob("*.json"):
        if old.stem not in handles:
            old.unlink()
            print(f"  removed stale {old.name}")

    built = {}
    for handle, reels in reels_by_acct.items():
        built[handle] = build(handle, reels, profiles.get(handle, {}))
    # competitors = the other tracked creators
    for handle, doc in built.items():
        doc["competitors"] = [
            {"handle": h, "full_name": built[h]["social_profiles"]["instagram"]["full_name"]}
            for h in handles if h != handle
        ]
        (RESEARCH_DIR / f"{handle}.json").write_text(json.dumps(doc, indent=2))
        ig = doc["social_profiles"]["instagram"]
        print(f"  wrote {handle}.json (followers={ig['followers']}, {len(doc['viral_posts'])} viral posts)")


if __name__ == "__main__":
    main()
