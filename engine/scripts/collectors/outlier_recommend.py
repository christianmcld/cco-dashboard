#!/usr/bin/env python3
"""
Outlier Recommender — CCO Outlier Engine (subsystem #1, Half B)

Reads output/outliers-raw.json. For the top outliers per account:
  - transcribes the reel (Whisper, best-effort; falls back to caption)
  - generates a FieldWork-adapted recommendation in Christian's voice:
      why_it_worked, recommended_angle, hook_framework, 5 hooks, script_summary
  - the source reel is the TEMPLATE: mirror its hook + structure, swap in
    Christian's FieldWork offer / tone / verbiage. Not invent-from-scratch.

Writes the dashboard data file: cco-dashboard/src/data/outliers.json

Requires: OPENAI_API_KEY in ~/.ai_secrets.json (Whisper + generation)
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent  # content-engine/
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
RAW_IN = ROOT / "output" / "outliers-raw.json"
VOICE_PROFILE = Path.home() / "the-clone" / "wiki" / "christian-mcleod-voice-profile.md"
DASH_OUT = DASHDIR / "src" / "data" / "outliers.json"
THUMB_DIR = DASHDIR / "public" / "outlier-thumbs"

MAX_TOTAL = 50               # cap on total recommendations generated (all outliers first)
RELEVANCE_THRESHOLD = 70     # 0-100; reels scoring >= this are FieldWork message-matches
GEN_MODEL = "gemini-2.5-pro"
SCORE_MODEL = "gemini-2.5-flash"   # cheap/fast for scoring every reel's copy
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models"

FIELDWORK_MISSION = """
FieldWork's core mission: use MONEY AS A TOOL — build and create money online — in order to spend
MORE TIME OFFLINE doing what you love with the people you love. Build Online. Live Offline.
A reel's copy is RELEVANT if it touches any of: money as a means (not the end), building income/
business online, freedom of time, escaping the grind/9-to-5, working less / from anywhere, buying
back your time, family/adventure/outdoors/nature as the reward, designing life around what matters,
or any bridge between earning online and living offline. Outdoor-adventure or lifestyle copy COUNTS
when it frames freedom, time, or money-as-a-tool, even without business words.
"""
# NOTE: transcription (Whisper) is disabled while the OpenAI account billing is
# inactive. Generation runs on caption + metrics. Re-enable transcribe() for full
# beat-by-beat script modeling once a transcription provider is live.

FIELDWORK_BRIEF = """
FIELDWORK — Christian McLeod's flagship program. Rally cry: "Build Online. Live Offline."
Big idea: use AI and automation to build an online business that buys back your time, so you can
spend it doing what you love with the people you love. Audience: founders, operators, coaches, and
experts who have a skill but want a business that does not chain them to a desk. The offer is the
FieldWork program (build the engines once, then return to the field). Tone: grounded, warm, a little
campfire/outdoors, anti-hustle-porn, honest about the work. Avoid hype. NEVER use em dashes.
"""


def load_saved_reference() -> str:
    """Style guide synthesized from Christian's IG saved folders (what he wants
    to emulate). Injected into generation so packs match his saved aesthetic."""
    ref_path = ROOT / "output" / "saved-reference.json"
    if not ref_path.exists():
        return ""
    try:
        data = json.loads(ref_path.read_text())
    except Exception:
        return ""
    lines = []
    for folder, info in data.get("folders", {}).items():
        guide = info.get("style_guide", [])
        if guide:
            lines.append(f"From Christian's saved '{folder}' folder (emulate this): " + "; ".join(guide))
    return "\n".join(lines)


_SAVED_REF = load_saved_reference()


def load_voice() -> str:
    try:
        return VOICE_PROFILE.read_text()[:6000]
    except Exception:
        return "(voice profile unavailable — write plain, grounded, warm, no hype, no em dashes)"


def transcribe(video_url: str, key: str = None) -> str | None:
    """Download the reel and transcribe it with OpenAI Whisper. Falls back to
    None (caller uses caption) on any failure."""
    okey = get_key("OPENAI_API_KEY")
    if not video_url or not okey:
        return None
    try:
        r = requests.get(video_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        if r.status_code != 200 or not r.content or len(r.content) > 24 * 1024 * 1024:
            return None
        resp = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {okey}"},
            files={"file": ("reel.mp4", r.content, "video/mp4")},
            data={"model": "whisper-1"},
            timeout=180,
        )
        if resp.status_code == 200:
            return (resp.json().get("text") or "").strip() or None
        return None
    except Exception:
        return None


def download_thumb(reel: dict) -> str | None:
    """Download the reel cover image locally (IG CDN URLs expire). Returns the
    public web path, or None to fall back to the (expiring) remote URL."""
    url = reel.get("display_url")
    vid = reel.get("source_video_id")
    if not url or not vid:
        return None
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    dest = THUMB_DIR / f"{vid}.jpg"
    web_path = f"/outlier-thumbs/{vid}.jpg"
    if dest.exists():
        return web_path
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        if r.status_code == 200 and r.content:
            dest.write_bytes(r.content)
            return web_path
    except Exception as e:
        print(f"    thumb download failed: {e}")
    return None


def generate(outlier: dict, transcript: str | None, voice: str, key: str, match_note: str = "") -> dict | None:
    source_text = transcript or outlier.get("source_caption") or ""
    saved_block = (f"STYLE TO EMULATE (from the content Christian saves as reference):\n{_SAVED_REF}\n\n"
                   if _SAVED_REF else "")
    sys_prompt = (
        "You are Christian McLeod's content strategist. A competitor's Instagram reel went viral "
        "(a statistical outlier for that account). Your job: produce Christian's OWN version, using "
        "the competitor reel as the EXACT template. Mirror its hook style and script structure "
        "beat-for-beat, but swap in Christian's FieldWork offer, examples, and voice. Do not invent a "
        "different concept. Match the proven FORM; change the SUBSTANCE to Christian's.\n\n"
        f"{FIELDWORK_BRIEF}\n\nCHRISTIAN'S VOICE PROFILE (write in this voice):\n{voice}\n\n"
        f"{saved_block}"
        "Return STRICT JSON with keys: "
        "hook_framework (short label for the original's hook type), "
        "why_it_worked (1-2 sentences on why the original spiked), "
        "headline (a short punchy title for this content idea, 3-8 words), "
        "recommended_angle (1-2 sentences: Christian's version of this idea), "
        "hooks (array of exactly 5 distinct scroll-stopping opening lines in Christian's voice, modeled "
        "on the original's hook pattern), "
        "talking_points (array of 6-10 bullet points of exactly what Christian should SAY in the video, "
        "in order, each a full spoken sentence or two in his voice. This is a talking-head video, so "
        "give a complete spine he can record from, not vague directions. Cover the hook, the setup, the "
        "meat/value, and the CTA), "
        "body_copy (the same content written as a flowing word-for-word teleprompter script, 120-220 "
        "words, in his voice, no stage directions), "
        "script_summary (a tight beat-by-beat outline, 4-7 beats, each one short line), "
        "production_guidance (array of 3-5 short practical tips: what to film, b-roll, on-screen text, "
        "shot ideas, CTA). No markdown, no em dashes."
    )
    user = (
        f"COMPETITOR: @{outlier['source_account']} | {outlier['source_plays']:,} plays "
        f"({outlier['outlier_score']}x their average)\n"
        f"CAPTION: {outlier.get('source_caption','')}\n"
        f"REEL TRANSCRIPT / TEXT:\n{source_text[:3500]}\n"
        + (f"\nNOTE: {match_note}\n" if match_note else "")
    )
    try:
        resp = requests.post(
            f"{GEMINI}/{GEN_MODEL}:generateContent?key={key}",
            headers={"Content-Type": "application/json"},
            json={
                "systemInstruction": {"parts": [{"text": sys_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "response_mime_type": "application/json",
                    "temperature": 0.8,
                },
            },
            timeout=120,
        )
        if resp.status_code != 200:
            print(f"    gen {resp.status_code}: {resp.text[:150]}")
            return None
        cand = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(cand)
    except Exception as e:
        print(f"    generation failed: {e}")
        return None


def score_relevance(handle: str, reels: list, key: str) -> dict:
    """Batch-score every reel's copy for FieldWork-message relevance (0-100).
    Returns {video_id: {"score": int, "reason": str}}."""
    scores: dict = {}
    chunk = 40
    for start in range(0, len(reels), chunk):
        batch = reels[start:start + chunk]
        items = [{"id": r["source_video_id"], "caption": (r.get("source_caption") or "")[:300]} for r in batch]
        sys_prompt = (
            "You score how well each Instagram reel's COPY aligns with FieldWork's message.\n"
            f"{FIELDWORK_MISSION}\n"
            "For each item return its id, a relevance score 0-100, and a short reason. "
            "Return STRICT JSON: {\"results\":[{\"id\":\"...\",\"score\":0,\"reason\":\"...\"}]}. No em dashes."
        )
        try:
            resp = requests.post(
                f"{GEMINI}/{SCORE_MODEL}:generateContent?key={key}",
                headers={"Content-Type": "application/json"},
                json={
                    "systemInstruction": {"parts": [{"text": sys_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": json.dumps(items)}]}],
                    "generationConfig": {"response_mime_type": "application/json", "temperature": 0.2},
                },
                timeout=120,
            )
            if resp.status_code != 200:
                print(f"    relevance {resp.status_code}: {resp.text[:120]}")
                continue
            parsed = json.loads(resp.json()["candidates"][0]["content"]["parts"][0]["text"])
            for row in parsed.get("results", []):
                scores[row["id"]] = {"score": int(row.get("score", 0)), "reason": row.get("reason", "")}
        except Exception as e:
            print(f"    relevance batch failed: {e}")
    return scores


def main():
    key = get_key("GOOGLE_AI_STUDIO_KEY")
    if not key:
        print("ERROR: GOOGLE_AI_STUDIO_KEY missing")
        sys.exit(1)

    raw = json.loads(RAW_IN.read_text())
    voice = load_voice()
    all_reels = raw.get("reels", [])
    reel_by_id = {r["source_video_id"]: r for r in all_reels}

    # group reels by account
    reels_by_acct: dict[str, list] = {}
    for r in all_reels:
        reels_by_acct.setdefault(r["source_account"], []).append(r)

    # --- relevance pass: score every reel's copy for FieldWork message fit ---
    print(f"Scoring FieldWork-message relevance for {len(all_reels)} reels...")
    relevance: dict = {}
    for acct, lst in reels_by_acct.items():
        relevance.update(score_relevance(acct, lst, key))
    for vid, r in reel_by_id.items():
        rel = relevance.get(vid, {})
        r["relevance_score"] = rel.get("score", 0)
        r["relevance_reason"] = rel.get("reason", "")
        r["is_message_match"] = r["relevance_score"] >= RELEVANCE_THRESHOLD

    # --- selection: ALL outliers, then fill with top message-matches, cap MAX_TOTAL ---
    seen: set = set()
    selected = []
    def add(reel):
        if reel["source_video_id"] not in seen:
            seen.add(reel["source_video_id"])
            selected.append(reel)
    # every outlier across all creators (incl smaller multiples like Taki's 4-5x)
    for r in sorted([r for r in all_reels if r.get("is_outlier")], key=lambda r: r["outlier_score"], reverse=True):
        add(r)
    # then message-matches by relevance until we hit the cap
    for r in sorted([r for r in all_reels if r.get("is_message_match")],
                    key=lambda r: (r["relevance_score"], r.get("timestamp") or ""), reverse=True):
        if len(selected) >= MAX_TOTAL:
            break
        add(r)
    selected = selected[:MAX_TOTAL]
    n_out = sum(1 for r in selected if r.get("is_outlier"))
    n_msg = sum(1 for r in selected if r.get("is_message_match"))
    print(f"Selected {len(selected)} reels: {n_out} outliers, {n_msg} FieldWork message-matches "
          f"(some overlap). Generating recommendations...")

    enriched = []
    for i, o in enumerate(selected, 1):
        tags = []
        if o.get("is_outlier"):
            tags.append(f"{o['outlier_score']}x outlier")
        if o.get("is_message_match"):
            tags.append(f"FieldWork match {o.get('relevance_score')}")
        print(f"[{i}/{len(selected)}] @{o['source_account']} [{', '.join(tags)}]")
        match_note = ""
        if o.get("is_message_match") and not o.get("is_outlier"):
            match_note = ("This reel is NOT a performance outlier but its copy fits the FieldWork "
                          f"message ({o.get('relevance_reason','')}). Lean the angle into the money-as-a-tool / "
                          "build-online-live-offline bridge.")
        transcript = transcribe(o.get("video_url"), key)
        rec = generate(o, transcript, voice, key, match_note)
        if not rec:
            print("    ! skipped (generation failed)")
            continue
        thumb = download_thumb(o)
        enriched.append({
            "source_account": o["source_account"],
            "source_url": o["source_url"],
            "thumbnail": thumb or o.get("display_url"),
            "source_plays": o["source_plays"],
            "source_likes": o.get("source_likes"),
            "source_comments": o.get("source_comments"),
            "account_baseline": o["account_baseline"],
            "outlier_score": o["outlier_score"],
            "is_outlier": bool(o.get("is_outlier")),
            "is_message_match": bool(o.get("is_message_match")),
            "relevance_score": o.get("relevance_score", 0),
            "relevance_reason": o.get("relevance_reason", ""),
            "source_caption": o.get("source_caption", ""),
            "timestamp": o.get("timestamp"),
            "video_duration": o.get("video_duration"),
            "transcript": transcript,
            "hook_framework": rec.get("hook_framework"),
            "why_it_worked": rec.get("why_it_worked"),
            "headline": rec.get("headline") or (rec.get("hooks") or [""])[0],
            "recommended_angle": rec.get("recommended_angle"),
            "my_hooks": rec.get("hooks", []),
            "my_talking_points": rec.get("talking_points", []),
            "my_body_copy": rec.get("body_copy", ""),
            "my_script_summary": rec.get("script_summary"),
            "production_guidance": rec.get("production_guidance", []),
        })

    # rank so strong message-matches surface alongside outliers (relevance/20 ~ outlier x)
    enriched.sort(key=lambda o: max(o["outlier_score"], o.get("relevance_score", 0) / 20), reverse=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "accounts": raw.get("accounts", []),
        "outlier_total_detected": len(raw["outliers"]),
        "message_match_total": sum(1 for r in all_reels if r.get("is_message_match")),
        "outliers": enriched,
    }
    DASH_OUT.parent.mkdir(parents=True, exist_ok=True)
    DASH_OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(enriched)} recommendations -> {DASH_OUT}")


if __name__ == "__main__":
    main()
