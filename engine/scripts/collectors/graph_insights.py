#!/usr/bin/env python3
"""
Instagram Graph API insights — pull REAL native insights (reach, views, profile
views, follower count, audience demographics) into the Analytics tab.

Requires an Instagram BUSINESS/CREATOR account connected to a Facebook Page and a
Graph API access token with instagram_basic + instagram_manage_insights +
pages_read_engagement. Add to ~/.ai_secrets.json:
  api_keys.META_GRAPH_TOKEN          (long-lived user/page access token)
  api_keys.IG_BUSINESS_ACCOUNT_ID    (the IG business account id, e.g. 17841400000000000)

If either is missing this exits gracefully (Analytics stays content-based).
Merges results into cco-dashboard/src/data/analytics.json under "native_insights".

Usage: uv run python3 graph_insights.py
"""

import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.secrets import get_key

ROOT = Path(__file__).resolve().parent.parent.parent
DASHDIR = Path(os.environ.get("CCO_DASH_DIR", str(ROOT.parent / "cco-dashboard")))
ANALYTICS = DASHDIR / "src" / "data" / "analytics.json"
GRAPH = "https://graph.facebook.com/v21.0"


def g(path, token, **params):
    params["access_token"] = token
    url = f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read().decode()).get("error", {})}
    except Exception as e:
        return {"error": {"message": str(e)}}


def main():
    token = get_key("META_GRAPH_TOKEN")
    ig_id = get_key("IG_BUSINESS_ACCOUNT_ID")
    if not token or not ig_id:
        print("Graph token / IG business id not set -- skipping native insights. "
              "Add META_GRAPH_TOKEN + IG_BUSINESS_ACCOUNT_ID to ~/.ai_secrets.json.")
        return

    profile = g(ig_id, token, fields="followers_count,media_count,username")
    if "error" in profile:
        print("Graph error:", profile["error"].get("message", "")[:160])
        return

    # account-level metrics, last 30 days
    acct = g(f"{ig_id}/insights", token,
             metric="reach,impressions,profile_views,website_clicks", period="day",
             since=int((datetime.now(timezone.utc).timestamp()) - 30 * 86400))
    metrics = {}
    for m in acct.get("data", []):
        vals = m.get("values", [])
        metrics[m["name"]] = sum(v.get("value", 0) for v in vals)

    # audience demographics
    demo = g(f"{ig_id}/insights", token, metric="audience_city,audience_gender_age", period="lifetime")
    demographics = {}
    for m in demo.get("data", []):
        v = m.get("values", [{}])
        demographics[m["name"]] = (v[0] or {}).get("value", {}) if v else {}

    insights = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "followers_count": profile.get("followers_count"),
        "media_count": profile.get("media_count"),
        "reach_30d": metrics.get("reach"),
        "impressions_30d": metrics.get("impressions"),
        "profile_views_30d": metrics.get("profile_views"),
        "website_clicks_30d": metrics.get("website_clicks"),
        "demographics": demographics,
    }

    data = json.loads(ANALYTICS.read_text()) if ANALYTICS.exists() else {}
    data["native_insights"] = insights
    ANALYTICS.write_text(json.dumps(data, indent=2))
    print(f"Wrote native insights: reach30={insights['reach_30d']}, "
          f"impressions30={insights['impressions_30d']}, profile_views30={insights['profile_views_30d']}")


if __name__ == "__main__":
    main()
