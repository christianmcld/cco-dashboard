#!/usr/bin/env python3
"""
Account Analytics — pull Christian's own Instagram performance into the
dashboard's Analytics tab. Scrapes @christianmcld profile + reels and computes
followers, engagement, top content, and a posting timeline.

(Deeper native IG insights — reach, impressions, audience demographics — require
the Instagram Graph API on a connected business account; this v1 uses the
content metrics we can pull directly, and is structured to extend later.)

Usage: uv run python3 analytics.py
Requires: APIFY_TOKEN
"""

import json
import os
import sys
import urllib.request
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
OUT = DASHDIR / "src" / "data" / "analytics.json"
THUMB_DIR = DASHDIR / "public" / "analytics-thumbs"
HANDLE = "christianmcld"
REEL_ACTOR = "apify~instagram-reel-scraper"
SCRAPER = "apify~instagram-scraper"
BASE = "https://api.apify.com/v2/acts"


def apify(actor, payload, token, timeout=300):
    url = f"{BASE}/{actor}/run-sync-get-dataset-items?token={token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"  ! apify {actor} failed: {e}")
        return []


def dl_thumb(url, name):
    if not url:
        return ""
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / name
    if dest.exists():
        return f"/analytics-thumbs/{name}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return f"/analytics-thumbs/{name}"
    except Exception:
        return ""


def main():
    token = get_key("APIFY_TOKEN")
    now = datetime.now(timezone.utc)

    print(f"Fetching @{HANDLE} profile + reels...")
    prof = apify(SCRAPER, {"directUrls": [f"https://www.instagram.com/{HANDLE}/"],
                           "resultsType": "details", "resultsLimit": 1}, token)
    p = prof[0] if isinstance(prof, list) and prof else {}
    reels = apify(REEL_ACTOR, {"username": [HANDLE], "resultsLimit": 60}, token)
    reels = [r for r in reels if isinstance(r, dict) and r.get("videoPlayCount") is not None]

    def eng(r):
        return int((r.get("likesCount") or 0) + (r.get("commentsCount") or 0))

    def ago(ts):
        try:
            return (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days
        except Exception:
            return None

    plays = [int(r.get("videoPlayCount") or 0) for r in reels]
    engs = [eng(r) for r in reels]
    followers = p.get("followersCount")

    # top reels
    top = sorted(reels, key=lambda r: int(r.get("videoPlayCount") or 0), reverse=True)[:6]
    top_out = []
    for r in top:
        top_out.append({
            "url": r.get("url"),
            "caption": (r.get("caption") or "").split("\n")[0][:80],
            "plays": int(r.get("videoPlayCount") or 0),
            "likes": r.get("likesCount"), "comments": r.get("commentsCount"),
            "date": r.get("timestamp"),
            "thumbnail": dl_thumb(r.get("displayUrl"), f"{r.get('shortCode')}.jpg"),
        })

    # posting timeline (reels per month, last 12 buckets)
    months = Counter()
    for r in reels:
        ts = r.get("timestamp")
        if ts:
            months[ts[:7]] += 1
    timeline = [{"month": m, "count": c} for m, c in sorted(months.items())][-12:]

    dates = sorted([r.get("timestamp", "") for r in reels if r.get("timestamp")], reverse=True)
    days_since = ago(dates[0]) if dates else None
    posts_90 = sum(1 for d in dates if (ago(d) or 9999) <= 90)

    avg_plays = int(statistics.mean(plays)) if plays else 0
    avg_eng = int(statistics.mean(engs)) if engs else 0
    eng_rate = round(100 * avg_eng / avg_plays, 1) if avg_plays else 0
    eng_rate_followers = round(100 * avg_eng / followers, 2) if followers else 0

    OUT.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "handle": HANDLE,
        "profile": {
            "followers": followers,
            "following": p.get("followsCount"),
            "posts": p.get("postsCount"),
            "bio": p.get("biography", ""),
            "verified": p.get("verified", False),
        },
        "reels_analyzed": len(reels),
        "metrics": {
            "avg_plays": avg_plays,
            "avg_engagement": avg_eng,
            "engagement_rate_pct": eng_rate,
            "engagement_rate_followers_pct": eng_rate_followers,
            "best_plays": max(plays) if plays else 0,
        },
        "activity": {
            "days_since_last_post": days_since,
            "posts_last_90": posts_90,
            "dormant": posts_90 == 0,
        },
        "top_reels": top_out,
        "timeline": timeline,
    }, indent=2))
    print(f"Wrote analytics.json: {followers} followers, {len(reels)} reels, "
          f"avg {avg_plays:,} plays, {eng_rate}% eng rate, last post {days_since}d ago")


if __name__ == "__main__":
    main()
