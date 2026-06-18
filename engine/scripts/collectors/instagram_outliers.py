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
    records = []
    for r in reels:
        records.append({
            "source_account": handle,
            "source_video_id": r.get("shortCode"),
            "source_url": r.get("url"),
            "source_caption": r.get("caption") or "",
            "source_plays": plays(r),
            "source_likes": r.get("likesCount"),
            "source_comments": r.get("commentsCount"),
            "timestamp": r.get("timestamp"),
            "display_url": r.get("displayUrl"),
            "video_url": r.get("videoUrl"),
            "video_duration": r.get("videoDuration"),
            "music": (r.get("musicInfo") or {}).get("song_name") if isinstance(r.get("musicInfo"), dict) else None,
        })
    return records


def finalize_account(handle: str, records: list, threshold: float) -> tuple:
    """Compute the per-account baseline (median plays) over the retained history
    and flag outliers. Stable because it uses the full window, not just new posts."""
    if len(records) < 3:
        for r in records:
            r["account_baseline"] = None
            r["outlier_score"] = 0
            r["is_outlier"] = False
        return records, None, []
    baseline = int(statistics.median([r["source_plays"] for r in records]))
    for r in records:
        r["account_baseline"] = baseline
        r["outlier_score"] = round(r["source_plays"] / baseline, 2) if baseline > 0 else 0
        r["is_outlier"] = r["outlier_score"] >= threshold
    records.sort(key=lambda o: o["outlier_score"], reverse=True)
    outliers = [r for r in records if r["is_outlier"]]
    print(f"  @{handle}: baseline {baseline:,} | {len(outliers)} outliers / {len(records)} reels")
    return records, baseline, outliers


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
    threshold = float(cfg.get("outlier_threshold", 3.0))
    rolling_days = int(cfg.get("rolling_window_days", 180))
    days = cfg.get("backfill_days", 180) if mode == "backfill" else cfg.get("incremental_days", 3)
    now = datetime.now(timezone.utc)
    newer_than_iso = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    rolling_cutoff = (now - timedelta(days=rolling_days)).strftime("%Y-%m-%d")
    print(f"Mode: {mode} | scrape window: {days}d | rolling history kept: {rolling_days}d")

    # prior history (for incremental merge so baselines stay stable + cheap scrapes)
    prior_by_acct = {}
    if mode == "incremental" and RAW_OUT.exists():
        for r in json.loads(RAW_OUT.read_text()).get("reels", []):
            prior_by_acct.setdefault(r["source_account"], {})[r["source_video_id"]] = r

    results, all_outliers, all_reels = [], [], []
    for acct in cfg.get("accounts", []):
        handle = acct["handle"]
        acct["_newer_than_iso"] = newer_than_iso
        acct["results_limit"] = 300 if mode == "backfill" else 30   # incremental = light + fast
        new_records = collect_account(acct, cfg, token)
        if mode == "incremental":
            merged = dict(prior_by_acct.get(handle, {}))
            for r in new_records:                       # new posts refresh plays/likes + add new
                merged[r["source_video_id"]] = r
            records = [r for r in merged.values() if (r.get("timestamp") or "")[:10] >= rolling_cutoff]
            print(f"  @{handle}: +{len(new_records)} fresh, {len(records)} in {rolling_days}d window")
        else:
            records = new_records
        records, baseline, outliers = finalize_account(handle, records, threshold)
        results.append({"handle": handle, "reels": records, "baseline": baseline, "outliers": outliers})
        all_outliers.extend(outliers)
        all_reels.extend(records)
        time.sleep(1)

    all_outliers.sort(key=lambda o: o["outlier_score"], reverse=True)
    out = {
        "generated_at": now.isoformat(),
        "mode": mode,
        "window_days": days,
        "accounts": [{"handle": r["handle"], "reels": len(r["reels"]), "baseline": r["baseline"], "outlier_count": len(r["outliers"])} for r in results],
        "outliers": all_outliers,
        "reels": all_reels,
    }
    RAW_OUT.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {len(all_outliers)} outliers ({len(all_reels)} reels) across {len(results)} accounts [{mode}] -> {RAW_OUT}")


if __name__ == "__main__":
    main()
