#!/usr/bin/env python3
"""
BELIEF tagging — tag every content pack with the FieldWork BELIEF pillar(s) it
serves. Reads cco-dashboard/src/data/outliers.json, batch-classifies each pack
against the 6 BELIEF pillars with Gemini, writes a `belief_tags` array back.

BELIEF (mirrors FieldWork TRUST), from fieldwork-tool-template/lib/belief.ts:
  B Bold Authority · E Earned Proof · L Lived Story
  I Intimate Humanity · E2 Esteemed Admiration · F Faithful Service

Usage: uv run python3 tag_belief.py
Requires: GOOGLE_AI_STUDIO_KEY
"""

import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
RECS = DASHDIR / "src" / "data" / "outliers.json"
MODEL = "gemini-2.5-flash"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"

PILLARS = (
    "B = Bold Authority (go-to voice, named frameworks, one clear avatar, expertise). "
    "E = Earned Proof (results, numbers, testimonials, third-party credibility, demonstrations, before/after). "
    "L = Lived Story (origin story, personal narrative tied to the mission, retellable). "
    "I = Intimate Humanity (one-to-one tone, behind-the-scenes, vulnerability, the real you). "
    "E2 = Esteemed Admiration (wins without arrogance, lifestyle, values, status with humility). "
    "F = Faithful Service (unconditional value, teaching, consistency, long-term over extraction)."
)
VALID = {"B", "E", "L", "I", "E2", "F"}


def tag_batch(items, key):
    sys_prompt = (
        "You tag short-form content ideas with the FieldWork BELIEF trust pillars they primarily serve.\n"
        f"PILLARS: {PILLARS}\n"
        "For each item, choose the 1-2 pillars it MOST serves (use the codes B, E, L, I, E2, F). "
        "Return STRICT JSON: {\"results\":[{\"id\":\"...\",\"tags\":[\"B\"]}]}. No prose."
    )
    payload = [{"id": it["id"], "text": (it["headline"] + ". " + it["angle"])[:400]} for it in items]
    try:
        resp = requests.post(
            f"{GEMINI}/{MODEL}:generateContent?key={key}",
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": sys_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": json.dumps(payload)}]}],
                "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1},
            },
            timeout=120,
        )
        if resp.status_code != 200:
            print(f"  tag batch {resp.status_code}: {resp.text[:120]}")
            return {}
        parsed = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
        out = {}
        for row in parsed.get("results", []):
            tags = [t for t in row.get("tags", []) if t in VALID][:2]
            out[row["id"]] = tags or ["B"]
        return out
    except Exception as e:
        print(f"  tag batch failed: {e}")
        return {}


def main():
    key = get_key("GOOGLE_AI_STUDIO_KEY")
    data = json.loads(RECS.read_text())
    recs = data.get("outliers", [])
    items = [{"id": r.get("source_url") or r.get("source_video_id"),
              "headline": r.get("headline") or (r.get("my_hooks") or [""])[0],
              "angle": r.get("recommended_angle", "")} for r in recs]

    tags_by_id = {}
    chunk = 25
    for i in range(0, len(items), chunk):
        tags_by_id.update(tag_batch(items[i:i + chunk], key))

    n = 0
    for r in recs:
        rid = r.get("source_url") or r.get("source_video_id")
        if rid in tags_by_id:
            r["belief_tags"] = tags_by_id[rid]
            n += 1
        else:
            r.setdefault("belief_tags", ["B"])
    RECS.write_text(json.dumps(data, indent=2))
    print(f"Tagged {n}/{len(recs)} packs with BELIEF pillars -> {RECS.name}")


if __name__ == "__main__":
    main()
