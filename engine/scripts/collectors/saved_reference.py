#!/usr/bin/env python3
"""
Saved-folder reference — scrape Christian's Instagram saved collections
("Christian Story content emulate" + "Live offline") and synthesize a style
guide the content generator emulates, plus BELIEF tags per folder.

Reads output/saved-folders.json (shortcodes captured from the logged-in browser),
scrapes each post's caption/owner via Apify, and writes output/saved-reference.json
(used by outlier_recommend.py to steer generation toward what Christian wants to emulate).

Usage: uv run python3 saved_reference.py
Requires: APIFY_TOKEN, GOOGLE_AI_STUDIO_KEY
"""

import json
import sys
import urllib.request
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
IN = ROOT / "output" / "saved-folders.json"
OUT = ROOT / "output" / "saved-reference.json"
SCRAPER = "apify~instagram-scraper"
BASE = "https://api.apify.com/v2/acts"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"
MODEL = "gemini-2.5-flash"


def scrape_posts(shortcodes, token):
    urls = [f"https://www.instagram.com/p/{sc}/" for sc in shortcodes]
    payload = {"directUrls": urls, "resultsType": "posts", "resultsLimit": len(urls), "addParentData": False}
    req = urllib.request.Request(f"{BASE}/{SCRAPER}/run-sync-get-dataset-items?token={token}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=400) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        print(f"  ! scrape failed: {e}")
        return []
    out = []
    for it in (data if isinstance(data, list) else []):
        cap = it.get("caption")
        if cap:
            out.append({"owner": it.get("ownerUsername"), "caption": cap[:500], "type": it.get("type")})
    return out


def synthesize(folder, posts, key):
    captions = "\n---\n".join(p["caption"][:300] for p in posts[:30])
    prompt = (
        f"These are Instagram posts Christian McLeod saved in a folder named '{folder}' as content he wants "
        "to emulate. Study the patterns and write a tight STYLE GUIDE he can apply to his own content.\n\n"
        f"POSTS:\n{captions}\n\n"
        "Return STRICT JSON: {\"style_guide\": [\"...\", \"...\"] (4-7 short, specific, actionable bullets on "
        "hook style, structure, tone, topics, and formats that recur), \"themes\": [\"...\"] (3-6 recurring "
        "themes), \"belief_tags\": [\"B\",\"E\",...] (which BELIEF pillars this folder mostly builds: B Bold "
        "Authority, E Earned Proof, L Lived Story, I Intimate Humanity, E2 Esteemed Admiration, F Faithful "
        "Service)}. No prose, no em dashes."
    )
    try:
        resp = requests.post(f"{GEMINI}/{MODEL}:generateContent?key={key}",
                             headers={"Content-Type": "application/json"},
                             json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                                   "generationConfig": {"response_mime_type": "application/json", "temperature": 0.3}},
                             timeout=120)
        if resp.status_code != 200:
            print(f"  synth {resp.status_code}: {resp.text[:120]}")
            return {}
        import re
        txt = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception as e:
        print(f"  synth failed: {e}")
        return {}


def main():
    atoken = get_key("APIFY_TOKEN")
    gkey = get_key("GOOGLE_AI_STUDIO_KEY")
    folders = json.loads(IN.read_text()).get("folders", {})

    result = {"folders": {}}
    for folder, shortcodes in folders.items():
        print(f"Scraping {len(shortcodes)} posts from '{folder}'...")
        posts = scrape_posts(shortcodes, atoken)
        print(f"  got {len(posts)} captions")
        synth = synthesize(folder, posts, gkey) if posts else {}
        result["folders"][folder] = {
            "post_count": len(posts),
            "style_guide": synth.get("style_guide", []),
            "themes": synth.get("themes", []),
            "belief_tags": synth.get("belief_tags", []),
            "sample_captions": [p["caption"][:200] for p in posts[:5]],
        }
        for b in result["folders"][folder]["style_guide"]:
            print("   -", b[:80])

    OUT.write_text(json.dumps(result, indent=2))
    print(f"Wrote saved-reference.json")


if __name__ == "__main__":
    main()
