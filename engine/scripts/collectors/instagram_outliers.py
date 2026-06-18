#!/usr/bin/env python3
"""
Instagram Outlier Scraper — CCO Outlier Engine (subsystem #1, Half A)

For each competitor IG account in config/competitor-accounts.json:
  - scrape reels via Apify apify/instagram-scraper
  - compute a per-account baseline (median plays)
  - flag reels whose plays >= threshold x baseline as OUTLIERS

First run backfills `backfill_days` (default 90); later runs scrape
`incremental_days` (default 3). Writes raw outliers to output/outliers-raw.json.

Usage:
    uv run python3 instagram_outliers.py            # auto: backfill if no prior run
    uv run python3 instagram_outliers.py --mode backfill
    uv run python3 instagram_outliers.py --mode incremental

Requires: APIFY_TOKEN in ~/.ai_secrets.json
"""

import json
import sys
import time
import statistics
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent  # content-engine/
CONFIG_PATH = ROOT / "config" / "competitor-accounts.json"
OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
RAW_OUT = OUTPUT_DIR / "outliers-raw.json"

# Dedicated reel scraper: paginates the reels tab properly (the general
# instagram-scraper caps at the small grid count for some accounts, e.g. Steve).
APIFY_ACTOR = "apify~instagram-reel-scraper"
APIFY_BASE = "https://api.apify.com/v2"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def scrape_reels(handle: str, limit: int, newer_than_iso: str, token: str) -> list:
    """Run apify/instagram-reel-scraper synchronously and return reel items.
    Paginates the reels tab (date is filtered client-side in collect_account)."""
    url = (
        f"{APIFY_BASE}/acts/{APIFY_ACTOR}/run-sync-get-dataset-items?token={token}"
    )
    payload = {
        "username": [handle],
        "resultsLimit": limit,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  ! HTTP {e.code} scraping @{handle}: {e.read().decode()[:200]}")
        return []
    except Exception as e:
        print(f"  ! error scraping @{handle}: {e}")
        return []
    if not isinstance(data, list):
        print(f"  ! unexpected response for @{handle}: {str(data)[:200]}")
        return []
    return data


def is_reel(item: dict) -> bool:
    return item.get("type") == "Video" and item.get("videoPlayCount") is not None


def plays(item: dict) -> int:
    return int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)


def collect_account(acct: dict, cfg: dict, token: str) -> dict:
    handle = acct["handle"]
    limit = acct.get("results_limit", 200)
    newer_than = acct.get("_newer_than_iso")
    print(f"\n=== @{handle} (since {newer_than}) ===")
    items = scrape_reels(handle, limit, newer_than, token)
    reels = [it for it in items if is_reel(it)]
    if newer_than:  # client-side date window (reel scraper has no date param)
        reels = [r for r in reels if (r.get("timestamp") or "")[:10] >= newer_than]
    print(f"  scraped {len(items)} items, {len(reels)} reels in window")
    if len(reels) < 3:
        print("  ! too few reels for a reliable baseline; skipping outlier calc")
        return {"handle": handle, "reels": [], "baseline": None, "outliers": []}

    play_counts = [plays(r) for r in reels]
    baseline = int(statistics.median(play_counts))
    threshold = float(cfg.get("outlier_threshold", 3.0))
    print(f"  baseline (median plays): {baseline:,} | threshold: {threshold}x")

    records = []
    for r in reels:
        p = plays(r)
        score = round(p / baseline, 2) if baseline > 0 else 0
        records.append({
            "source_account": handle,
            "source_video_id": r.get("shortCode"),
            "source_url": r.get("url"),
            "source_caption": r.get("caption") or "",
            "source_plays": p,
            "source_likes": r.get("likesCount"),
            "source_comments": r.get("commentsCount"),
            "account_baseline": baseline,
            "outlier_score": score,
            "is_outlier": score >= threshold,
            "timestamp": r.get("timestamp"),
            "display_url": r.get("displayUrl"),
            "video_url": r.get("videoUrl"),
            "video_duration": r.get("videoDuration"),
            "music": (r.get("musicInfo") or {}).get("song_name") if isinstance(r.get("musicInfo"), dict) else None,
        })
    records.sort(key=lambda o: o["outlier_score"], reverse=True)
    outliers = [r for r in records if r["is_outlier"]]
    print(f"  >>> {len(outliers)} OUTLIERS (>= {threshold}x), {len(records)} total reels kept")
    for o in outliers[:5]:
        print(f"      {o['outlier_score']}x  {o['source_plays']:,} plays  {o['source_caption'][:50]!r}")
    return {"handle": handle, "reels": records, "baseline": baseline, "outliers": outliers}


def main():
    mode = "backfill"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    elif RAW_OUT.exists():
        mode = "incremental"

    token = get_key("APIFY_TOKEN")
    if not token:
        print("ERROR: APIFY_TOKEN missing in ~/.ai_secrets.json")
        sys.exit(1)

    cfg = load_config()
    days = cfg.get("backfill_days", 90) if mode == "backfill" else cfg.get("incremental_days", 3)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    newer_than_iso = cutoff.strftime("%Y-%m-%d")
    print(f"Mode: {mode} | window: last {days} days (since {newer_than_iso})")

    results = []
    all_outliers = []
    all_reels = []
    for acct in cfg.get("accounts", []):
        acct["_newer_than_iso"] = newer_than_iso
        acct["results_limit"] = 300 if mode == "backfill" else 80
        res = collect_account(acct, cfg, token)
        results.append(res)
        all_outliers.extend(res["outliers"])
        all_reels.extend(res["reels"])
        time.sleep(1)

    all_outliers.sort(key=lambda o: o["outlier_score"], reverse=True)
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "window_days": days,
        "accounts": [{"handle": r["handle"], "reels": len(r["reels"]), "baseline": r["baseline"], "outlier_count": len(r["outliers"])} for r in results],
        "outliers": all_outliers,
        "reels": all_reels,
    }
    RAW_OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(all_outliers)} outliers ({len(all_reels)} total reels) across {len(results)} accounts -> {RAW_OUT}")


if __name__ == "__main__":
    main()
