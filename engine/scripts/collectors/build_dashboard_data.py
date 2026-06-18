#!/usr/bin/env python3
"""
Build the CCO main-dashboard data (src/data/dashboard-data.json) from the
FieldWork competitor reels we scrape (output/outliers-raw.json) plus the
generated recommendations (cco-dashboard/src/data/outliers.json).

Replaces the old CMNDSHFT AI-trends data: Creator Watch, summary, performance
insights (hook frameworks + durations), top outliers, trending, suggested
content, and the pipeline counters now reflect Christian's tracked creators.

Usage: uv run python3 build_dashboard_data.py
"""

import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # content-engine/
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
RAW = ROOT / "output" / "outliers-raw.json"
RECS = DASHDIR / "src" / "data" / "outliers.json"
DASH = DASHDIR / "src" / "data" / "dashboard-data.json"
LIBRARY = DASHDIR / "src" / "data" / "library.json"

# Hook-framework keyword classifier (shared with competitor-research.py)
HOOK_FRAMEWORKS = {
    "Cost Replacement": ["charged", "cost", "paid", "spend", "expensive", "agency", "subscription", "replaced", "$", "price", "dollar", "thousand", "hundred", "grand", "free"],
    "Capability Reveal": ["you can now", "you can actually", "did you know", "just released", "new feature", "just dropped", "now possible"],
    "Pattern Interrupt": ["stop", "don't", "never", "wrong", "mistake", "quit", "warning"],
    "Curiosity Gap": ["this one", "secret", "hidden", "most people", "nobody", "the real", "actually"],
    "Pain Point": ["struggling", "frustrated", "tired of", "sick of", "waste", "broken", "failing"],
    "Before/After": ["used to", "before", "now i", "went from", "transformed", "changed"],
    "Proof First": ["made", "earned", "grew", "built", "closed", "generated", "subscribers", "followers"],
    "Contrarian": ["lie", "myth", "truth is", "unpopular", "controversial", "everyone is wrong"],
    "Question": ["?"],
    "Test/Experiment": ["tried", "tested", "experiment", "challenge", "day one", "day 1", "days of"],
}


def classify(caption: str) -> str:
    c = (caption or "").lower()
    best, score = "Curiosity Gap", 0
    for fw, kws in HOOK_FRAMEWORKS.items():
        s = sum(1 for k in kws if k in c)
        if s > score:
            best, score = fw, s
    return best


def engagement(r: dict) -> int:
    return int((r.get("source_likes") or 0) + (r.get("source_comments") or 0))


def main():
    raw = json.loads(RAW.read_text())
    reels = raw.get("reels", [])
    recs = json.loads(RECS.read_text()).get("outliers", []) if RECS.exists() else []
    now = datetime.now(timezone.utc)
    seven_ago = now - timedelta(days=7)

    by_acct = defaultdict(list)
    for r in reels:
        by_acct[r["source_account"]].append(r)

    # ── Creator Watch ───────────────────────────────────────────────
    creators = []
    for handle, rs in by_acct.items():
        rs_sorted = sorted(rs, key=lambda r: r.get("timestamp") or "")
        engs = [engagement(r) for r in rs]
        top = max(rs, key=lambda r: r.get("outlier_score", 0))
        last_dt = max((r.get("timestamp") or "" for r in rs), default="")
        creators.append({
            "username": handle,
            "posts": len(rs),
            "avg_engagement": int(statistics.mean(engs)) if engs else 0,
            "top_outlier": round(max((r.get("outlier_score", 0) for r in rs), default=0), 2),
            "total_engagement": sum(engs),
            "top_hook": (top.get("source_caption") or "").split("\n")[0][:80],
            "engagement_history": [
                {"date": r.get("timestamp"), "engagement": engagement(r)}
                for r in rs_sorted if r.get("timestamp")
            ][-12:],
            "last_post_date": last_dt,
            "posted_recently": bool(last_dt and last_dt >= seven_ago.isoformat()),
        })
    creators.sort(key=lambda c: c["total_engagement"], reverse=True)

    # ── Performance Insights: only the business coaches (exclude Wade's
    #     viral lifestyle content, which would skew the patterns) ──
    PERF_CREATORS = {"tomnoske", "takimoore", "steveofallstreets"}
    perf_reels = [r for r in reels if r["source_account"] in PERF_CREATORS]

    # ── Performance: hook frameworks ────────────────────────────────
    fw_bucket = defaultdict(lambda: {"count": 0, "eng": 0})
    for r in perf_reels:
        fw = classify(r.get("source_caption", ""))
        fw_bucket[fw]["count"] += 1
        fw_bucket[fw]["eng"] += engagement(r)
    frameworks = sorted([
        {"name": k, "count": v["count"], "avg_engagement": int(v["eng"] / v["count"]) if v["count"] else 0}
        for k, v in fw_bucket.items()
    ], key=lambda x: x["avg_engagement"], reverse=True)

    # ── Performance: duration buckets ───────────────────────────────
    dur_defs = [("<30s", 0, 30), ("30-45s", 30, 45), ("45-60s", 45, 60), ("60-90s", 60, 90), (">90s", 90, 1e9)]
    duration = []
    for label, lo, hi in dur_defs:
        d = [r for r in perf_reels if r.get("video_duration") and lo <= r["video_duration"] < hi]
        engs = [engagement(r) for r in d]
        duration.append({"range": label, "count": len(d), "avg_engagement": int(statistics.mean(engs)) if engs else 0})

    # ── Performance: speaking pace (WPM) from transcribed recs (perf creators) ──
    pace_defs = [("<150", 0, 150), ("150-180", 150, 180), ("180-210", 180, 210), ("210-240", 210, 240), (">240", 240, 1e9)]
    paced = []  # (wpm, engagement)
    for rec in recs:
        if rec.get("source_account") not in PERF_CREATORS:
            continue
        t = rec.get("transcript")
        dur = rec.get("video_duration")
        if t and dur and dur > 0:
            wpm = len(t.split()) / (dur / 60.0)
            paced.append((wpm, (rec.get("source_likes") or 0) + (rec.get("source_comments") or 0)))
    pace = []
    for label, lo, hi in pace_defs:
        bucket = [e for w, e in paced if lo <= w < hi]
        pace.append({"range": label, "count": len(bucket), "avg_engagement": int(statistics.mean(bucket)) if bucket else 0})

    # ── Top outliers (balanced: top 3 per creator so every creator shows) ──
    outliers_by_creator = defaultdict(list)
    for r in reels:
        if r.get("is_outlier"):
            outliers_by_creator[r["source_account"]].append(r)
    selected_outliers = []
    for acct, rs in outliers_by_creator.items():
        rs.sort(key=lambda r: r.get("outlier_score", 0), reverse=True)
        selected_outliers.extend(rs[:3])
    selected_outliers.sort(key=lambda r: r.get("outlier_score", 0), reverse=True)
    rec_by_url = {rr.get("source_url"): rr for rr in recs}

    def _post(r):
        rec = rec_by_url.get(r.get("source_url")) or {}
        scr = rec.get("my_script_summary")
        scr = scr if isinstance(scr, list) else [x for x in (scr or "").split("\n") if x.strip()]
        return {
            "hook": (r.get("source_caption") or "").split("\n")[0][:80] or "(no caption)",
            "creator": r["source_account"],
            "engagement": engagement(r),
            "outlier_ratio": round(r.get("outlier_score", 0), 2),
            "priority_score": round(min(99, r.get("outlier_score", 0) * 10), 1),
            "framework": rec.get("hook_framework") or classify(r.get("source_caption", "")),
            "wpm": None,
            "duration": r.get("video_duration"),
            "url": r.get("source_url"),
            "date": r.get("timestamp"),
            "thumbnail": "",
            "media_type": "reel",
            # generated content pack (for Emulate -> Library)
            "headline": rec.get("headline") or (rec.get("my_hooks") or [""])[0],
            "angle": rec.get("recommended_angle", ""),
            "hooks": rec.get("my_hooks", []),
            "talking_points": rec.get("my_talking_points", []),
            "body_copy": rec.get("my_body_copy", ""),
            "script": scr,
            "guidance": rec.get("production_guidance", []),
            "belief_tags": rec.get("belief_tags", []),
        }
    posts = [_post(r) for r in selected_outliers]

    recs_by_creator = defaultdict(list)
    for rec in recs:
        recs_by_creator[rec["source_account"]].append(rec)
    def balanced_recs(per_creator):
        picked = []
        for acct, rs in recs_by_creator.items():
            picked.extend(rs[:per_creator])  # recs already ranked within outliers.json
        return picked

    # ── Trending Now / Heat Map = real FieldWork-market trends (Gemini grounded) ──
    market_path = ROOT / "output" / "market-trends.json"
    if market_path.exists():
        topics = json.loads(market_path.read_text()).get("topics", [])
    else:
        topics = []

    # ── Suggested content (ideas) from recommendations (balanced) ───
    ideas = []
    for rec in balanced_recs(3):
        hooks = rec.get("my_hooks") or []
        title = (hooks[0] if hooks else rec.get("recommended_angle") or "Untitled idea")[:120]
        script = rec.get("my_script_summary")
        script_list = script if isinstance(script, list) else ([s for s in (script or "").split("\n") if s.strip()])
        ideas.append({
            "title": title,
            "headline": rec.get("headline") or title,
            "angle": rec.get("recommended_angle", ""),
            "hooks": hooks,
            "talking_points": rec.get("my_talking_points", []),
            "body_copy": rec.get("my_body_copy", ""),
            "script": script_list,
            "guidance": rec.get("production_guidance", []),
            "source": f"@{rec['source_account']}",
            "source_url": rec.get("source_url", ""),
            "framework": rec.get("hook_framework", ""),
            "belief_tags": rec.get("belief_tags", []),
            "details": {
                "Angle": rec.get("recommended_angle", ""),
                "Source": f"@{rec['source_account']} ({rec.get('outlier_score')}x, {rec.get('source_plays'):,} plays)",
                "Hooks": " | ".join(hooks[:3]),
                "Framework": rec.get("hook_framework", ""),
            },
        })

    # ── Summary + pipeline ──────────────────────────────────────────
    total_eng = sum(c["total_engagement"] for c in creators)
    all_outliers = [r for r in reels if r.get("is_outlier")]
    n_outliers = len(all_outliers)
    strong = len([r for r in all_outliers if r.get("outlier_score", 0) >= 5])
    summary = {
        "total_engagement": total_eng,
        "outlier_count": n_outliers,
        "strong_outliers": strong,
        "topic_count": len(topics),
        "idea_count": len(ideas),
        "creator_count": len(creators),
        "period": f"last {raw.get('window_days', 90)} days",
        "outlier_rate": round(100 * n_outliers / len(reels), 1) if reels else 0,
    }
    pipeline = {
        "reels_analyzed": len(reels),
        "outliers_found": n_outliers,
        "creators_monitored": len(creators),
        "recommendations": len(recs),
    }

    # ── Assemble (preserve unused keys from prior file) ─────────────
    out = json.loads(DASH.read_text()) if DASH.exists() else {}
    out["generated_at"] = now.isoformat()
    out["period"] = summary["period"]
    out["creator_watch"] = {"creators": creators}
    out["performance"] = {"frameworks": frameworks, "pace": pace, "duration": duration}
    out["outliers"] = {"posts": posts, "total": n_outliers}
    out["trending"] = {"topics": topics, "urgent": "", "date": now.strftime("%Y-%m-%d"), "file": ""}
    out["suggested_content"] = {"ideas": ideas}
    out["summary"] = summary
    out["pipeline"] = pipeline
    # library section: drop stale script/skill counts to current reality
    out["library"] = {"content_packs": [], "total_packs": 0, "total_scripts": len(ideas), "total_skills": 0}

    DASH.write_text(json.dumps(out, indent=2))

    # ── Library seed: every recommendation as a ready-to-shoot content pack ──
    def script_beats(s):
        if isinstance(s, list):
            return s
        return [x for x in (s or "").split("\n") if x.strip()]
    library = []
    for rec in recs:
        library.append({
            "id": rec.get("source_url") or rec.get("source_video_id"),
            "headline": rec.get("headline") or (rec.get("my_hooks") or ["Idea"])[0],
            "angle": rec.get("recommended_angle", ""),
            "hooks": rec.get("my_hooks", []),
            "talking_points": rec.get("my_talking_points", []),
            "body_copy": rec.get("my_body_copy", ""),
            "script": script_beats(rec.get("my_script_summary")),
            "guidance": rec.get("production_guidance", []),
            "source": "@" + rec.get("source_account", ""),
            "source_url": rec.get("source_url", ""),
            "framework": rec.get("hook_framework", ""),
            "belief_tags": rec.get("belief_tags", []),
            "outlier_score": rec.get("outlier_score", 0),
            "is_outlier": bool(rec.get("is_outlier")),
            "is_message_match": bool(rec.get("is_message_match")),
            "plays": rec.get("source_plays", 0),
        })
    LIBRARY.write_text(json.dumps({"generated_at": now.isoformat(), "items": library}, indent=2))
    print(f"Wrote dashboard-data.json + library.json ({len(library)} packs): {len(creators)} creators, "
          f"{len(reels)} reels, {n_outliers} outliers, {len(topics)} topics, {len(ideas)} ideas.")


if __name__ == "__main__":
    main()
