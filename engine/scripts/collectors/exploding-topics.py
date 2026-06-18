#!/usr/bin/env python3
"""
Exploding Topics Collector — niche-aware trend detection system.

Emulates the core value of ExplodingTopics.com by detecting topics with
accelerating growth across multiple data sources, filtered by niche relevance.

Methodology (modeled on Exploding Topics):
  1. Define niche keywords (primary, secondary, broader)
  2. For each keyword cluster, query multiple sources to measure growth:
     - Source A: Google Trends RSS (daily trending + seed keyword tracking)
     - Source B: Reddit post volume (recent vs prior period)
     - Source C: Exa API semantic search (article volume growth)
     - Source D: Hacker News story frequency (recent vs historical)
  3. Calculate composite growth score per topic
  4. Classify: EXPLODING / GROWING / STABLE / DECLINING
  5. Filter by niche relevance score (0-1)
  6. Output ranked, classified, niche-relevant trends

Data sources & approach:
  - Exploding Topics uses AI + ML scanning across Google, Reddit, Amazon,
    YouTube, Twitter, Spotify, plus Google Search volume confirmation,
    plus human vetting. They classify as Exploding/Regular/Peaked.
  - We replicate this with: Google Trends RSS, Reddit JSON API,
    Exa neural search, and HN Algolia API — all free/keyed.

Outputs: ~/content-engine/trending-data/sources/exploding-topics.json

Usage:
  cd ~/content-engine && uv run python3 scripts/collectors/exploding-topics.py
"""

import json
import math
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote_plus

# Centralized API key loader
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: uv add requests")
    sys.exit(1)

# ─── Output paths ────────────────────────────────────────────────────────────
OUTPUT_DIR = Path.home() / "content-engine" / "trending-data" / "sources"
OUTPUT_FILE = OUTPUT_DIR / "exploding-topics.json"

# ─── Rate limiting ───────────────────────────────────────────────────────────
USER_AGENT = "CCO-Dashboard/1.0 (content-engine exploding-topics collector)"
RATE_LIMIT_SECONDS = 2

# ─── Exa API — key loaded from ~/.ai_secrets.json via lib/secrets.py ────────
EXA_API_URL = "https://api.exa.ai/search"
EXA_API_KEY = get_key("EXA_API_KEY")

# ─── Niche Configuration ─────────────────────────────────────────────────────
# Change these for different users/brands. This is the single config block
# that makes the entire system niche-aware.
NICHE_CONFIG = {
    "name": "AI automation for business owners",
    "primary_keywords": [
        "Claude Code",
        "AI automation",
        "AI agents",
        "AI for business",
    ],
    "secondary_keywords": [
        "content creation AI",
        "Instagram growth",
        "lead generation AI",
        "AI tools",
        "vibe coding",
    ],
    "broader_keywords": [
        "artificial intelligence",
        "no code",
        "SaaS",
        "solopreneur",
        "Anthropic",
        "ChatGPT",
    ],
    "subreddits": [
        "ClaudeCode",
        "ChatGPT",
        "artificial",
        "MachineLearning",
        "vibecoding",
        "ClaudeAI",
        "LocalLLaMA",
        "SaaS",
        "Entrepreneur",
    ],
    "exclude_topics": [
        "cryptocurrency",
        "bitcoin",
        "ethereum",
        "dating apps",
        "gaming",
        "esports",
        "sports",
        "nfl",
        "nba",
        "celebrity",
        "kardashian",
    ],
}

# ─── Relevance terms for scoring ─────────────────────────────────────────────
# Built from the niche config at module level
_ALL_NICHE_TERMS = []
for _kw_list in [
    NICHE_CONFIG["primary_keywords"],
    NICHE_CONFIG["secondary_keywords"],
    NICHE_CONFIG["broader_keywords"],
]:
    for _kw in _kw_list:
        _ALL_NICHE_TERMS.extend(_kw.lower().split())
# Deduplicate
_ALL_NICHE_TERMS = list(set(_ALL_NICHE_TERMS))


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE A: Google Trends RSS
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_google_trends_rss(geo="US"):
    """
    Fetch daily trending searches from Google Trends public RSS feed.
    Returns list of (topic, approximate_traffic) tuples.
    """
    url = f"https://trends.google.com/trending/rss?geo={geo}"
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        namespaces = {"ht": "https://trends.google.com/trending/rss"}
        root = ET.fromstring(resp.text)
        topics = []

        for item in root.findall(".//item"):
            title_el = item.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

            traffic_el = item.find("ht:approx_traffic", namespaces)
            traffic_str = traffic_el.text.strip() if traffic_el is not None and traffic_el.text else "0"
            traffic = int(re.sub(r"[^0-9]", "", traffic_str) or "0")

            # Collect related queries from news items for context
            news_titles = []
            for news_item in item.findall("ht:news_item", namespaces):
                news_title_el = news_item.find("ht:news_item_title", namespaces)
                if news_title_el is not None and news_title_el.text:
                    news_titles.append(news_title_el.text.strip())

            topics.append({
                "topic": title,
                "traffic": traffic,
                "news_context": news_titles[:3],
            })

        return topics

    except Exception as e:
        print(f"    Google Trends RSS failed: {e}")
        return []


def score_google_trends(all_niche_keywords):
    """
    Check which niche keywords appear in Google Trends daily trending.
    Returns dict of {keyword: growth_signal} where growth_signal is 0-200.
    """
    print("  [Source A] Google Trends RSS...")
    trending = fetch_google_trends_rss("US")
    time.sleep(RATE_LIMIT_SECONDS)
    trending_global = fetch_google_trends_rss("")
    trending.extend(trending_global)

    # Build a searchable text blob from all trending topics
    trending_text = " ".join(
        t["topic"].lower() + " " + " ".join(t["news_context"]).lower()
        for t in trending
    ).lower()

    scores = {}
    for kw in all_niche_keywords:
        kw_lower = kw.lower()
        # Direct match in trending topics
        direct_matches = sum(
            1 for t in trending if kw_lower in t["topic"].lower()
        )
        # Contextual match in related news
        context_matches = sum(
            1 for t in trending
            if any(kw_lower in n.lower() for n in t["news_context"])
        )
        # Traffic-weighted score for direct matches
        traffic_score = sum(
            t["traffic"] for t in trending if kw_lower in t["topic"].lower()
        )

        if direct_matches > 0:
            # Normalize traffic to a 0-200 scale (500K+ traffic = 200)
            normalized = min(200, (traffic_score / 500000) * 200)
            scores[kw] = max(normalized, 50 * direct_matches)
        elif context_matches > 0:
            scores[kw] = 25 * context_matches
        else:
            scores[kw] = 0

    matched = sum(1 for v in scores.values() if v > 0)
    print(f"    {len(trending)} trending topics, {matched}/{len(all_niche_keywords)} keywords matched")
    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE B: Reddit mention volume
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_reddit_search(query, subreddit=None, time_filter="week", limit=100):
    """
    Search Reddit for posts mentioning a query.
    Returns count of posts found.
    """
    if subreddit:
        url = f"https://www.reddit.com/r/{subreddit}/search.json"
    else:
        url = "https://www.reddit.com/search.json"

    params = {
        "q": query,
        "sort": "relevance",
        "t": time_filter,  # hour, day, week, month, year, all
        "limit": limit,
        "restrict_sr": "true" if subreddit else "false",
        "raw_json": 1,
    }
    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        children = data.get("data", {}).get("children", [])

        posts = []
        for child in children:
            post = child.get("data", {})
            if post.get("stickied"):
                continue
            posts.append({
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created_utc": post.get("created_utc", 0),
                "title": post.get("title", ""),
                "subreddit": post.get("subreddit", ""),
            })

        return posts

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429):
            # Rate limited or forbidden — return empty
            pass
        return []
    except Exception:
        return []


def score_reddit_mentions(all_niche_keywords, subreddits):
    """
    For each keyword, compare Reddit mention volume in the last 7 days
    vs the prior month. Returns growth percentages.

    Note: Reddit's search API caps at 100 results per query. When both
    week and month return ~100, volume comparison is unreliable. In that
    case, we fall back to engagement-based scoring (upvotes + comments)
    which is not capped by the API.
    """
    print("  [Source B] Reddit mention volume...")
    scores = {}

    for kw in all_niche_keywords:
        # Search across all relevant subreddits (global search)
        recent_posts = fetch_reddit_search(kw, time_filter="week", limit=100)
        time.sleep(RATE_LIMIT_SECONDS)

        month_posts = fetch_reddit_search(kw, time_filter="month", limit=100)
        time.sleep(RATE_LIMIT_SECONDS)

        recent_count = len(recent_posts)
        month_count = len(month_posts)

        # Detect API cap: if both queries return near max (>90), the counts
        # are unreliable for growth comparison. Use engagement-only scoring.
        api_capped = recent_count >= 90 and month_count >= 90

        if api_capped:
            # When capped, we can't measure volume growth — there are MORE
            # than 100 posts in both periods. This means the topic is very
            # active. We use a time-partitioned approach: check how many of
            # the recent posts are from the LAST 3 DAYS vs OLDER in the week.
            now_ts = datetime.now(timezone.utc).timestamp()
            three_days_ago = now_ts - (3 * 86400)

            very_recent = sum(1 for p in recent_posts if p["created_utc"] > three_days_ago)
            older_in_week = recent_count - very_recent

            # If more posts in last 3 days than the prior 4 days, accelerating
            if older_in_week > 0:
                # Normalize: 3 days vs 4 days
                daily_rate_recent = very_recent / 3.0
                daily_rate_older = older_in_week / 4.0
                if daily_rate_older > 0:
                    growth_pct = ((daily_rate_recent - daily_rate_older) / daily_rate_older) * 100
                else:
                    growth_pct = 100
            else:
                # All posts are very recent — strong signal
                growth_pct = 100

            # Being capped means high activity — apply a minimum floor
            growth_pct = max(20, min(200, growth_pct))
        else:
            # Normal case: API didn't cap, counts are real
            prior_count = max(month_count - recent_count, 0)
            # Normalize prior to weekly average (prior covers ~3 weeks)
            prior_weekly = prior_count / 3.0 if prior_count > 0 else 0

            if prior_weekly > 0:
                growth_pct = ((recent_count - prior_weekly) / prior_weekly) * 100
            elif recent_count > 0:
                growth_pct = 200  # New topic appearing = high growth signal
            else:
                growth_pct = 0

            # Clamp to prevent extreme outliers
            growth_pct = max(-100, min(300, growth_pct))

        # Engagement quality score
        total_engagement = sum(
            p["score"] + p["num_comments"] * 2 for p in recent_posts
        )
        engagement_bonus = min(50, total_engagement / 100)

        scores[kw] = {
            "growth_pct": round(growth_pct, 1),
            "recent_count": recent_count,
            "month_count": month_count,
            "api_capped": api_capped,
            "total_engagement": total_engagement,
            "composite": round(growth_pct + engagement_bonus, 1),
        }

        cap_note = " [capped, using engagement]" if api_capped else ""
        print(f"    '{kw}': {recent_count} recent / {month_count} monthly, growth={growth_pct:+.0f}%{cap_note}")

    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE C: Exa API semantic search
# ═══════════════════════════════════════════════════════════════════════════════

def exa_search(query, start_date, end_date, num_results=30):
    """
    Search Exa for articles about a topic within a date range.
    Returns count of results and list of article metadata.
    """
    headers = {
        "x-api-key": EXA_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {
        "query": query,
        "numResults": num_results,
        "type": "auto",
        "startPublishedDate": start_date,
        "endPublishedDate": end_date,
        "contents": {
            "highlights": {
                "numSentences": 2,
                "highlightsPerUrl": 1,
            }
        },
    }

    try:
        resp = requests.post(EXA_API_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])

        articles = []
        for r in results:
            highlight = ""
            if r.get("highlights"):
                highlight = r["highlights"][0] if isinstance(r["highlights"], list) else str(r["highlights"])
            articles.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "published": r.get("publishedDate", ""),
                "author": r.get("author", ""),
                "highlight": highlight,
            })

        return {
            "count": len(results),
            "articles": articles,
        }

    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else "?"
        print(f"    Exa search failed ({status}): {e}")
        return {"count": 0, "articles": []}
    except Exception as e:
        print(f"    Exa search error: {e}")
        return {"count": 0, "articles": []}


def score_exa_articles(all_niche_keywords):
    """
    For each keyword, compare article volume in last 7 days vs prior 7 days.
    Returns growth metrics + top articles for content angles.

    Note: We request numResults=30. When both periods return 30, the count
    comparison is capped and shows 0% growth. In that case we assign a
    baseline "active topic" score rather than 0, since both periods being
    saturated means the topic is well-covered (stable or growing).
    """
    print("  [Source C] Exa API article volume...")
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    EXA_RESULT_CAP = 30
    scores = {}

    for kw in all_niche_keywords:
        # Recent period (last 7 days)
        recent = exa_search(
            kw,
            start_date=week_ago.strftime("%Y-%m-%dT00:00:00.000Z"),
            end_date=now.strftime("%Y-%m-%dT23:59:59.000Z"),
            num_results=EXA_RESULT_CAP,
        )
        time.sleep(RATE_LIMIT_SECONDS)

        # Prior period (7-14 days ago)
        prior = exa_search(
            kw,
            start_date=two_weeks_ago.strftime("%Y-%m-%dT00:00:00.000Z"),
            end_date=week_ago.strftime("%Y-%m-%dT00:00:00.000Z"),
            num_results=EXA_RESULT_CAP,
        )
        time.sleep(RATE_LIMIT_SECONDS)

        recent_count = recent["count"]
        prior_count = prior["count"]

        # Detect API cap
        api_capped = recent_count >= EXA_RESULT_CAP and prior_count >= EXA_RESULT_CAP

        if api_capped:
            # Both periods saturated — topic is heavily covered.
            # Assign a moderate positive score (well-established topic).
            growth_pct = 30  # "GROWING" baseline for saturated topics
        elif prior_count > 0:
            growth_pct = ((recent_count - prior_count) / prior_count) * 100
        elif recent_count > 0:
            growth_pct = 200  # New topic emerging
        else:
            growth_pct = 0

        cap_note = " [capped]" if api_capped else ""

        scores[kw] = {
            "growth_pct": round(growth_pct, 1),
            "recent_count": recent_count,
            "prior_count": prior_count,
            "api_capped": api_capped,
            "top_articles": recent["articles"][:5],
        }

        print(f"    '{kw}': {recent_count} recent / {prior_count} prior articles, growth={growth_pct:+.0f}%{cap_note}")

    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# SOURCE D: Hacker News story frequency
# ═══════════════════════════════════════════════════════════════════════════════

def hn_search(query, created_after_i=None, created_before_i=None):
    """
    Search HN stories via Algolia API.
    created_after_i / created_before_i are Unix timestamps.
    Returns count and list of stories.
    """
    url = "https://hn.algolia.com/api/v1/search"
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": 100,
    }
    if created_after_i:
        params["numericFilters"] = f"created_at_i>{created_after_i}"
        if created_before_i:
            params["numericFilters"] += f",created_at_i<{created_before_i}"
    elif created_before_i:
        params["numericFilters"] = f"created_at_i<{created_before_i}"

    headers = {"User-Agent": USER_AGENT}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", [])
        total = data.get("nbHits", len(hits))

        stories = []
        for hit in hits:
            stories.append({
                "title": hit.get("title", ""),
                "points": hit.get("points", 0) or 0,
                "num_comments": hit.get("num_comments", 0) or 0,
                "url": hit.get("url", ""),
                "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
                "created_at": hit.get("created_at", ""),
            })

        return {"count": total, "stories": stories}

    except Exception as e:
        print(f"    HN search error for '{query}': {e}")
        return {"count": 0, "stories": []}


def score_hn_stories(all_niche_keywords):
    """
    For each keyword, compare HN story frequency in last 7 days vs prior 7 days.
    """
    print("  [Source D] Hacker News story frequency...")
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    now_ts = int(now.timestamp())
    week_ts = int(week_ago.timestamp())
    two_week_ts = int(two_weeks_ago.timestamp())

    scores = {}

    for kw in all_niche_keywords:
        # Recent period
        recent = hn_search(kw, created_after_i=week_ts, created_before_i=now_ts)
        time.sleep(RATE_LIMIT_SECONDS)

        # Prior period
        prior = hn_search(kw, created_after_i=two_week_ts, created_before_i=week_ts)
        time.sleep(RATE_LIMIT_SECONDS)

        recent_count = recent["count"]
        prior_count = prior["count"]

        if prior_count > 0:
            growth_pct = ((recent_count - prior_count) / prior_count) * 100
        elif recent_count > 0:
            growth_pct = 150  # New topic signal
        else:
            growth_pct = 0

        # Points-weighted signal (high-engagement stories matter more)
        recent_points = sum(s["points"] for s in recent["stories"])
        prior_points = sum(s["points"] for s in prior["stories"])

        if prior_points > 0:
            points_growth = ((recent_points - prior_points) / prior_points) * 100
        elif recent_points > 0:
            points_growth = 150
        else:
            points_growth = 0

        # Blend count growth and points growth
        blended = (growth_pct * 0.5) + (points_growth * 0.5)

        scores[kw] = {
            "growth_pct": round(blended, 1),
            "recent_count": recent_count,
            "prior_count": prior_count,
            "recent_points": recent_points,
            "prior_points": prior_points,
            "top_stories": recent["stories"][:3],
        }

        print(f"    '{kw}': {recent_count} recent / {prior_count} prior stories, growth={blended:+.0f}%")

    return scores


# ═══════════════════════════════════════════════════════════════════════════════
# Composite scoring + classification
# ═══════════════════════════════════════════════════════════════════════════════

def calculate_relevance(topic, niche_config):
    """
    Calculate relevance score (0-1) for a topic against the niche.
    Higher score = more relevant to the user's niche.
    """
    topic_lower = topic.lower()

    # Check excludes first
    for exclude in niche_config["exclude_topics"]:
        if exclude.lower() in topic_lower:
            return 0.0

    score = 0.0

    # Primary keyword match (highest weight)
    for kw in niche_config["primary_keywords"]:
        if kw.lower() in topic_lower or topic_lower in kw.lower():
            score = max(score, 1.0)
        elif any(word in topic_lower for word in kw.lower().split()):
            score = max(score, 0.8)

    # Secondary keyword match
    for kw in niche_config["secondary_keywords"]:
        if kw.lower() in topic_lower or topic_lower in kw.lower():
            score = max(score, 0.7)
        elif any(word in topic_lower for word in kw.lower().split()):
            score = max(score, 0.5)

    # Broader keyword match
    for kw in niche_config["broader_keywords"]:
        if kw.lower() in topic_lower or topic_lower in kw.lower():
            score = max(score, 0.5)
        elif any(word in topic_lower for word in kw.lower().split()):
            score = max(score, 0.35)

    return round(score, 2)


def classify_topic(growth_score):
    """
    Classify a topic based on its composite growth score.
    Mimics Exploding Topics' Exploding/Regular/Peaked system.
    """
    if growth_score > 100:
        return "EXPLODING"
    elif growth_score > 30:
        return "GROWING"
    elif growth_score > -10:
        return "STABLE"
    else:
        return "DECLINING"


def generate_sparkline(google_score, reddit_score, exa_score, hn_score):
    """
    Generate a simple 7-point sparkline representing growth trajectory.
    We synthesize this from the multi-source signals.
    """
    # Build a synthetic trajectory based on growth signals
    # Start from a baseline and show acceleration
    baseline = 20
    scores = [google_score, reddit_score, exa_score, hn_score]
    avg_growth = sum(s for s in scores if s) / max(len([s for s in scores if s]), 1)

    # Generate 7 data points simulating recent trajectory
    sparkline = []
    for i in range(7):
        # Earlier points are lower, recent points show growth
        factor = 1.0 + (avg_growth / 100.0) * (i / 6.0)
        point = max(1, round(baseline * factor))
        sparkline.append(point)

    return sparkline


def generate_content_angle(topic, exa_data, reddit_data, hn_data):
    """
    Suggest a content angle based on what's being discussed.
    """
    # Pull top article titles and HN story titles for context
    context_titles = []

    if exa_data and exa_data.get("top_articles"):
        context_titles.extend(
            a["title"] for a in exa_data["top_articles"][:3] if a.get("title")
        )

    if hn_data and hn_data.get("top_stories"):
        context_titles.extend(
            s["title"] for s in hn_data["top_stories"][:2] if s.get("title")
        )

    if not context_titles:
        return f"Create content about {topic} — trending topic with growing interest"

    # Simple content angle: reference what people are discussing
    return f"Trending discussions: {'; '.join(context_titles[:3])}"


def generate_summary(topic, google_pct, reddit_data, exa_data, hn_data):
    """
    Generate a human-readable summary of why this topic is trending.
    """
    signals = []

    if google_pct and google_pct > 0:
        signals.append(f"appearing in Google Trends")

    if reddit_data:
        rc = reddit_data.get("recent_count", 0)
        eng = reddit_data.get("total_engagement", 0)
        if rc > 0:
            signals.append(f"{rc} Reddit posts this week ({eng} total engagement)")

    if exa_data:
        rc = exa_data.get("recent_count", 0)
        if rc > 0:
            signals.append(f"{rc} articles published in last 7 days")

    if hn_data:
        rc = hn_data.get("recent_count", 0)
        pts = hn_data.get("recent_points", 0)
        if rc > 0:
            signals.append(f"{rc} HN stories ({pts} points)")

    if signals:
        return f"{topic}: {', '.join(signals)}"
    return f"{topic}: low signal across sources"


# ═══════════════════════════════════════════════════════════════════════════════
# Main collector
# ═══════════════════════════════════════════════════════════════════════════════

def collect():
    """
    Main collection function. Queries all sources, scores, classifies,
    and outputs niche-filtered exploding topics.
    """
    print("[Exploding Topics] Starting niche-aware trend detection...")
    print(f"  Niche: {NICHE_CONFIG['name']}")

    # Build combined keyword list
    all_keywords = (
        NICHE_CONFIG["primary_keywords"]
        + NICHE_CONFIG["secondary_keywords"]
        + NICHE_CONFIG["broader_keywords"]
    )
    print(f"  Tracking {len(all_keywords)} keywords across 4 sources\n")

    # ── Collect from all sources ──────────────────────────────────────────
    google_scores = score_google_trends(all_keywords)
    time.sleep(RATE_LIMIT_SECONDS)

    reddit_scores = score_reddit_mentions(all_keywords, NICHE_CONFIG["subreddits"])
    time.sleep(RATE_LIMIT_SECONDS)

    exa_scores = score_exa_articles(all_keywords)
    time.sleep(RATE_LIMIT_SECONDS)

    hn_scores = score_hn_stories(all_keywords)

    # ── Calculate composite scores ────────────────────────────────────────
    print("\n  Calculating composite growth scores...")
    items = []

    for kw in all_keywords:
        # Extract individual source growth signals
        google_growth = google_scores.get(kw, 0)
        if isinstance(google_growth, dict):
            google_growth = google_growth.get("growth_pct", 0)

        reddit_data = reddit_scores.get(kw, {})
        reddit_growth = reddit_data.get("composite", 0) if isinstance(reddit_data, dict) else 0

        exa_data = exa_scores.get(kw, {})
        exa_growth = exa_data.get("growth_pct", 0) if isinstance(exa_data, dict) else 0

        hn_data = hn_scores.get(kw, {})
        hn_growth = hn_data.get("growth_pct", 0) if isinstance(hn_data, dict) else 0

        # Weighted composite growth score
        # Google Trends: 0.3 (search interest)
        # Reddit: 0.3 (community discussion)
        # Exa/Web: 0.25 (article/content production)
        # HN: 0.15 (tech community signal)
        growth_score = (
            google_growth * 0.30
            + reddit_growth * 0.30
            + exa_growth * 0.25
            + hn_growth * 0.15
        )
        growth_score = round(growth_score, 1)

        # Classification
        status = classify_topic(growth_score)

        # Relevance to niche
        relevance = calculate_relevance(kw, NICHE_CONFIG)

        # Sparkline
        sparkline = generate_sparkline(google_growth, reddit_growth, exa_growth, hn_growth)

        # Summary and content angle
        summary = generate_summary(kw, google_growth, reddit_data, exa_data, hn_data)
        content_angle = generate_content_angle(kw, exa_data, reddit_data, hn_data)

        # Source breakdown
        sources = {
            "google": f"{google_growth:+.0f}%" if google_growth else "0%",
            "reddit": f"{reddit_growth:+.0f}%" if reddit_growth else "0%",
            "web": f"{exa_growth:+.0f}%" if exa_growth else "0%",
            "hn": f"{hn_growth:+.0f}%" if hn_growth else "0%",
        }

        items.append({
            "topic": kw,
            "growth_score": growth_score,
            "status": status,
            "relevance": relevance,
            "sparkline": sparkline,
            "sources": sources,
            "summary": summary,
            "content_angle": content_angle,
            "source_data": {
                "google_raw": google_growth,
                "reddit_raw": reddit_data if isinstance(reddit_data, dict) else {},
                "exa_raw": {
                    "recent_count": exa_data.get("recent_count", 0) if isinstance(exa_data, dict) else 0,
                    "prior_count": exa_data.get("prior_count", 0) if isinstance(exa_data, dict) else 0,
                    "top_articles": (exa_data.get("top_articles", [])[:3] if isinstance(exa_data, dict) else []),
                },
                "hn_raw": {
                    "recent_count": hn_data.get("recent_count", 0) if isinstance(hn_data, dict) else 0,
                    "prior_count": hn_data.get("prior_count", 0) if isinstance(hn_data, dict) else 0,
                    "top_stories": (hn_data.get("top_stories", [])[:3] if isinstance(hn_data, dict) else []),
                },
            },
        })

    # ── Filter and sort ───────────────────────────────────────────────────
    # Filter: only topics with relevance > 0.3
    relevant_items = [i for i in items if i["relevance"] > 0.3]

    # Sort by growth_score descending
    relevant_items.sort(key=lambda x: x["growth_score"], reverse=True)

    # Also keep ALL items (pre-filter) for debugging
    items.sort(key=lambda x: x["growth_score"], reverse=True)

    # ── Build output ──────────────────────────────────────────────────────
    output = {
        "source": "exploding_topics",
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "niche": NICHE_CONFIG["name"],
        "niche_config": NICHE_CONFIG,
        "methodology": {
            "description": "Multi-source growth detection modeled on ExplodingTopics.com",
            "weights": {
                "google_trends": 0.30,
                "reddit_mentions": 0.30,
                "web_articles_exa": 0.25,
                "hackernews_stories": 0.15,
            },
            "classification": {
                "EXPLODING": "growth_score > 100",
                "GROWING": "growth_score 30-100",
                "STABLE": "growth_score -10 to 30",
                "DECLINING": "growth_score < -10",
            },
            "relevance_threshold": 0.3,
        },
        "summary": {
            "total_keywords_tracked": len(all_keywords),
            "relevant_topics": len(relevant_items),
            "exploding": len([i for i in relevant_items if i["status"] == "EXPLODING"]),
            "growing": len([i for i in relevant_items if i["status"] == "GROWING"]),
            "stable": len([i for i in relevant_items if i["status"] == "STABLE"]),
            "declining": len([i for i in relevant_items if i["status"] == "DECLINING"]),
        },
        "items": relevant_items,
        "_all_items_unfiltered": items,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2, default=str))

    # ── Report ────────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print(f"EXPLODING TOPICS — Results for: {NICHE_CONFIG['name']}")
    print(f"{'=' * 60}")
    print(f"  Total keywords tracked: {len(all_keywords)}")
    print(f"  Niche-relevant topics: {len(relevant_items)}")
    print(f"  EXPLODING: {output['summary']['exploding']}")
    print(f"  GROWING:   {output['summary']['growing']}")
    print(f"  STABLE:    {output['summary']['stable']}")
    print(f"  DECLINING: {output['summary']['declining']}")
    print()

    # Show top 10
    for i, item in enumerate(relevant_items[:10], 1):
        status_icon = {
            "EXPLODING": ">>>",
            "GROWING": ">>",
            "STABLE": "--",
            "DECLINING": "<<",
        }.get(item["status"], "??")
        print(
            f"  {i:2d}. {status_icon} [{item['status']:10s}] "
            f"score={item['growth_score']:+7.1f}  "
            f"rel={item['relevance']:.2f}  "
            f"{item['topic']}"
        )

    print(f"\n  Saved to {OUTPUT_FILE}")
    print(f"{'=' * 60}")
    return output


if __name__ == "__main__":
    collect()
