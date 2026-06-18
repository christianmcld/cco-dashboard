#!/usr/bin/env python3
"""
Market Trends collector — what's trending in the FieldWork market.

Uses Gemini with Google Search grounding to surface ~8 currently-trending
topics / SEO keywords around the FieldWork themes (founders reclaiming time,
working less, getting outside, optimizing and automating their business). Each
topic carries the heat_score schema the dashboard's heat map + emerging-topics
list expect. Writes output/market-trends.json.

Usage: uv run python3 build_market_trends.py
Requires: GOOGLE_AI_STUDIO_KEY
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "output" / "market-trends.json"
MODEL = "gemini-2.5-flash"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"

THEMES = (
    "founders and solopreneurs reclaiming their time, buying back their time, working less while "
    "earning more, automating their business with AI, escaping the 9-to-5 grind, time and location "
    "freedom, anti-hustle / lifestyle business, and spending more time outside and offline with the "
    "people they love. This is the market around the FieldWork program (Build Online. Live Offline.)."
)

PROMPT = (
    "Use Google Search to identify the 8 topics / SEO keyword themes that are MOST trending RIGHT NOW "
    f"in the market around: {THEMES}\n\n"
    "For each, return an object with:\n"
    "- name: the trending topic or keyword phrase (short, headline style, no leading number)\n"
    "- heat_score: an object {total, engagement, velocity}, each 0-100. total = overall heat, "
    "engagement = how much discussion/search volume, velocity = how fast it is rising. Vary them "
    "realistically; not everything is 90+.\n"
    "- details: an array of 2-3 short strings: what is happening in the market, and why it matters for "
    "FieldWork content. Start one with '**What is happening:**' and one with '**Why it matters:**'.\n\n"
    "Return STRICT JSON only: {\"topics\":[...]}. Sort by heat_score.total descending. No em dashes."
)


def main():
    key = get_key("GOOGLE_AI_STUDIO_KEY")
    if not key:
        print("ERROR: GOOGLE_AI_STUDIO_KEY missing")
        sys.exit(1)

    resp = requests.post(
        f"{GEMINI}/{MODEL}:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={
            "tools": [{"google_search": {}}],
            "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
            "generationConfig": {"temperature": 0.4},
        },
        timeout=120,
    )
    if resp.status_code != 200:
        print(f"ERROR {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    # strip markdown fences if present
    m = re.search(r"\{.*\}", text, re.S)
    raw = m.group(0) if m else text
    try:
        parsed = json.loads(raw)
    except Exception as e:
        print(f"ERROR parsing JSON: {e}\n{text[:300]}")
        sys.exit(1)

    topics = parsed.get("topics", [])[:8]
    # normalize heat_score
    for t in topics:
        hs = t.get("heat_score") or {}
        t["heat_score"] = {
            "total": int(hs.get("total", 0)),
            "engagement": int(hs.get("engagement", 0)),
            "velocity": int(hs.get("velocity", 0)),
        }
        t.setdefault("details", [])
    topics.sort(key=lambda x: x["heat_score"]["total"], reverse=True)

    OUT.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "topics": topics,
    }, indent=2))
    print(f"Wrote {len(topics)} market trends -> {OUT}")
    for t in topics:
        print(f"  [{t['heat_score']['total']:>3}] {t['name']}")


if __name__ == "__main__":
    main()
