#!/usr/bin/env python3
"""
Belief Score — score Christian's OWN posted content against the 6 FieldWork
BELIEF trust pillars, so he can see which pillars his content is building and
which are weak.

Scrapes @christianmcld Instagram reels (and YouTube if a working key is present),
tags each piece against the pillars with Gemini, and aggregates into a per-pillar
score + an overall BELIEF score. Writes cco-dashboard/src/data/belief-score.json.

Usage: uv run python3 belief_score.py
Requires: APIFY_TOKEN, GOOGLE_AI_STUDIO_KEY
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
OUT = DASHDIR / "src" / "data" / "belief-score.json"
IG_HANDLE = "christianmcld"
MODEL = "gemini-2.5-flash"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
APIFY = "https://api.apify.com/v2/acts/apify~instagram-reel-scraper/run-sync-get-dataset-items"

PILLARS = [
    ("B", "Bold Authority"),
    ("E", "Earned Proof"),
    ("L", "Lived Story"),
    ("I", "Intimate Humanity"),
    ("E2", "Esteemed Admiration"),
    ("F", "Faithful Service"),
]
PILLAR_DESC = (
    "B = Bold Authority (go-to voice, named frameworks, one avatar, expertise). "
    "E = Earned Proof (results, numbers, testimonials, third-party credibility, demos, before/after). "
    "L = Lived Story (origin story, personal narrative tied to the mission). "
    "I = Intimate Humanity (one-to-one tone, behind-the-scenes, vulnerability). "
    "E2 = Esteemed Admiration (wins without arrogance, lifestyle, values). "
    "F = Faithful Service (unconditional value, teaching, consistency, long-term)."
)
VALID = {p[0] for p in PILLARS}


def scrape_ig(token, limit=60):
    payload = {"username": [IG_HANDLE], "resultsLimit": limit}
    req = urllib.request.Request(f"{APIFY}?token={token}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"  ! IG scrape failed: {e}")
        return []
    out = []
    for it in (data if isinstance(data, list) else []):
        cap = it.get("caption")
        if cap:
            out.append({"source": "instagram", "text": cap[:400], "url": it.get("url"),
                        "plays": it.get("videoPlayCount"), "timestamp": it.get("timestamp")})
    return out


def tag_pieces(pieces, key):
    """Return list of tag-lists aligned to pieces."""
    results = [None] * len(pieces)
    chunk = 25
    for start in range(0, len(pieces), chunk):
        batch = pieces[start:start + chunk]
        payload = [{"i": start + j, "text": p["text"]} for j, p in enumerate(batch)]
        sys_prompt = (
            "Tag each piece of Christian McLeod's own content with the BELIEF trust pillars it builds.\n"
            f"PILLARS: {PILLAR_DESC}\n"
            "Pick the 1-2 pillars each piece most demonstrates (codes B,E,L,I,E2,F). "
            "Return STRICT JSON {\"results\":[{\"i\":0,\"tags\":[\"L\"]}]}."
        )
        try:
            resp = requests.post(
                f"{GEMINI}/{MODEL}:generateContent?key={key}",
                headers={"Content-Type": "application/json"},
                json={"systemInstruction": {"parts": [{"text": sys_prompt}]},
                      "contents": [{"role": "user", "parts": [{"text": json.dumps(payload)}]}],
                      "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}},
                timeout=120)
            if resp.status_code != 200:
                print(f"  tag {resp.status_code}: {resp.text[:120]}")
                continue
            for row in json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"]).get("results", []):
                idx = row.get("i")
                if isinstance(idx, int) and 0 <= idx < len(pieces):
                    results[idx] = [t for t in row.get("tags", []) if t in VALID][:2]
        except Exception as e:
            print(f"  tag batch failed: {e}")
    return [r or [] for r in results]


def main():
    atoken = get_key("APIFY_TOKEN")
    gkey = get_key("GOOGLE_AI_STUDIO_KEY")
    print(f"Scraping @{IG_HANDLE} reels...")
    pieces = scrape_ig(atoken)
    print(f"  {len(pieces)} IG pieces")
    # (YouTube intentionally omitted until a working YOUTUBE_API_KEY is available)

    if not pieces:
        print("No content to score.")
        return

    tags = tag_pieces(pieces, gkey)
    for p, t in zip(pieces, tags):
        p["belief_tags"] = t

    # ── Recency + frequency: belief decays when you stop posting ──
    now = datetime.now(timezone.utc)
    def days_ago(ts):
        try:
            return (now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).days
        except Exception:
            return 9999
    dated = [(p, days_ago(p.get("timestamp", ""))) for p in pieces]
    days_since_last = min((d for _, d in dated), default=9999)
    posts_30 = sum(1 for _, d in dated if d <= 30)
    posts_90 = sum(1 for _, d in dated if d <= 90)
    posts_365 = sum(1 for _, d in dated if d <= 365)
    cadence_per_week = round(posts_90 / (90 / 7), 1)
    # target cadence ~ 1 post/week over 90 days = ~13; activity 0 when dormant
    activity = min(1.0, posts_90 / 13.0)
    cadence_score = min(100, round(posts_90 / 13.0 * 100))
    dormant = posts_90 == 0

    total = len(pieces)
    counts = Counter()
    for t in tags:
        for code in t:
            counts[code] += 1

    pillars_out = []
    for code, name in PILLARS:
        c = counts.get(code, 0)
        # potential = what the content demonstrates when posted; live = decayed by inactivity
        potential = min(100, round(100 * (c / total) / 0.40)) if total else 0
        live = cadence_score if code == "F" else round(potential * activity)
        sample = next((p["text"][:120] for p, t in zip(pieces, tags) if code in t), "")
        pillars_out.append({"code": code, "letter": "E" if code == "E2" else code,
                            "name": name, "count": c, "score": live, "potential": potential, "sample": sample})
    overall = round(sum(p["score"] for p in pillars_out) / len(pillars_out)) if pillars_out else 0
    weakest = sorted([p for p in pillars_out], key=lambda p: p["potential"])[:2]

    OUT.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "handle": IG_HANDLE,
        "total_pieces": total,
        "sources": {"instagram": total, "youtube": 0},
        "overall_score": overall,
        "dormant": dormant,
        "days_since_last_post": days_since_last,
        "posts_last_30": posts_30, "posts_last_90": posts_90, "posts_last_365": posts_365,
        "cadence_per_week": cadence_per_week,
        "pillars": pillars_out,
        "weakest": [{"code": w["code"], "name": w["name"], "potential": w["potential"]} for w in weakest],
    }, indent=2))
    print(f"Wrote belief-score.json: LIVE overall {overall}/100 (dormant={dormant}, "
          f"last post {days_since_last}d ago, {posts_90} in 90d). "
          + ", ".join(f"{p['letter']}={p['score']}(pot {p['potential']})" for p in pillars_out))


if __name__ == "__main__":
    main()
