#!/usr/bin/env python3
"""
Master Collector — runs ALL data collectors in sequence, merges results,
and rebuilds the dashboard data.

Usage:
  cd ~/content-engine && uv run python3 scripts/collectors/collect-all.py

Workflow:
  1. Run each collector (google-trends, reddit, hackernews, producthunt, socialblade, content-discovery, tiktok-creative, exploding-topics)
  2. Merge all source JSONs into the daily snapshot
  3. Run build-dashboard-data.py to regenerate dashboard data
  4. Copy to dashboard public/ directory
  5. Report results
"""

import json
import importlib.util
import sys
import os
import subprocess
import traceback
from datetime import datetime, timezone, date
from pathlib import Path

BASE_DIR = Path.home() / "content-engine"
COLLECTORS_DIR = BASE_DIR / "scripts" / "collectors"
SOURCES_DIR = BASE_DIR / "trending-data" / "sources"
SNAPSHOTS_DIR = BASE_DIR / "trending-data" / "snapshots"
PUBLIC_DIR = BASE_DIR / "trending-dashboard" / "public"

COLLECTORS = [
    ("Google Trends", "google-trends.py"),
    ("Reddit", "reddit-trending.py"),
    ("Hacker News", "hackernews.py"),
    ("Product Hunt", "producthunt.py"),
    ("SocialBlade", "socialblade.py"),
    ("Content Discovery", "content-discovery.py"),
    ("TikTok Creative Center", "tiktok-creative.py"),
    ("Exploding Topics", "exploding-topics.py"),
]


def load_and_run_collector(name, filename):
    """Import and run a collector module."""
    filepath = COLLECTORS_DIR / filename
    if not filepath.exists():
        print(f"  SKIP: {filepath} not found")
        return None

    # Import the module dynamically
    module_name = filename.replace(".py", "").replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if hasattr(module, "collect"):
        return module.collect()
    else:
        print(f"  SKIP: {filename} has no collect() function")
        return None


def merge_sources():
    """Merge all source JSONs into a single daily snapshot."""
    all_items = []
    sources_found = []

    for source_file in sorted(SOURCES_DIR.glob("*.json")):
        try:
            data = json.loads(source_file.read_text())
            source_name = data.get("source", source_file.stem)
            items = data.get("items", [])
            all_items.extend(items)
            sources_found.append({
                "name": source_name,
                "count": len(items),
                "collected_at": data.get("collected_at", ""),
            })
        except Exception as e:
            print(f"  Warning: Could not read {source_file}: {e}")

    return all_items, sources_found


def build_daily_snapshot(all_items, sources_found):
    """Build and save the daily snapshot."""
    today = date.today().isoformat()

    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "date": today,
        "sources": sources_found,
        "total_items": len(all_items),
        "items": all_items,
    }

    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_file = SNAPSHOTS_DIR / f"{today}.json"
    snapshot_file.write_text(json.dumps(snapshot, indent=2, default=str))
    print(f"\nDaily snapshot saved: {snapshot_file}")
    return snapshot_file


def rebuild_dashboard():
    """Run build-dashboard-data.py to regenerate dashboard data."""
    build_script = BASE_DIR / "scripts" / "build-dashboard-data.py"
    if not build_script.exists():
        print("  Warning: build-dashboard-data.py not found, skipping dashboard rebuild")
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(build_script)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print("  Dashboard data rebuilt successfully")
            if result.stdout.strip():
                for line in result.stdout.strip().split("\n"):
                    print(f"    {line}")
            return True
        else:
            print(f"  Dashboard rebuild failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"  Dashboard rebuild error: {e}")
        return False


def copy_sources_to_public():
    """Copy source files to dashboard public directory for frontend access."""
    public_sources = PUBLIC_DIR / "sources"
    public_sources.mkdir(parents=True, exist_ok=True)

    for source_file in SOURCES_DIR.glob("*.json"):
        dest = public_sources / source_file.name
        dest.write_text(source_file.read_text())

    print(f"  Source files copied to {public_sources}")


def main():
    print("=" * 60)
    print("CCO Dashboard — Master Data Collector")
    print(f"Run time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Ensure directories exist
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)

    # Run each collector
    successes = 0
    failures = 0
    total_items = 0

    for name, filename in COLLECTORS:
        print(f"\n{'—' * 40}")
        print(f"Running: {name}")
        print(f"{'—' * 40}")
        try:
            result = load_and_run_collector(name, filename)
            if result and result.get("items"):
                successes += 1
                total_items += len(result.get("items", []))
            else:
                failures += 1
                print(f"  {name}: No items collected")
        except Exception as e:
            failures += 1
            print(f"  {name}: FAILED — {e}")
            traceback.print_exc()

    # Merge all sources
    print(f"\n{'=' * 40}")
    print("Merging sources...")
    all_items, sources_found = merge_sources()
    total_from_files = sum(s["count"] for s in sources_found)

    # Build daily snapshot
    build_daily_snapshot(all_items, sources_found)

    # Rebuild dashboard
    print("\nRebuilding dashboard data...")
    rebuild_dashboard()

    # Copy sources to public
    print("\nCopying to dashboard public/...")
    copy_sources_to_public()

    # Final report
    print(f"\n{'=' * 60}")
    print(f"COLLECTION COMPLETE")
    print(f"{'=' * 60}")
    print(f"Collected from {successes}/{len(COLLECTORS)} sources, {total_from_files} total items")
    print(f"Failures: {failures}")
    print()
    for s in sources_found:
        print(f"  {s['name']:20s} — {s['count']:4d} items")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
