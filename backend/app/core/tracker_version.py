"""
tracker_version.py — Single source of truth for tracker script URL and version.

The TRACKER_VERSION constant is bumped whenever spark-tracker.js changes.
It's appended as ?v= query param to the script tag src, which:
  1. Busts browser/CDN caches (browsers re-fetch when query string changes)
  2. Allows ensure_tracker_script_tag() to detect stale versions and re-register
  3. Provides operator visibility for which version each merchant is running

Update workflow:
  1. Modify /opt/wishspark/tracker/spark-tracker.js
  2. Bump TRACKER_VERSION below
  3. Deploy — existing merchants' script tags will be updated on next
     onboarding/repair cycle (ensure_tracker_script_tag detects URL mismatch)

All code that needs the tracker URL MUST use get_tracker_url() from this module.
Do NOT construct the URL inline elsewhere.
"""
import os

# Bump this when any file in /opt/wishspark/tracker/*.js changes.
# Format: integer, monotonically increasing.
TRACKER_VERSION = 14

# SHA-256 of the concatenated contents of every tracker/*.js file,
# computed by test_elite_hardening_v2::test_tracker_js_hash_matches_version.
# When any tracker script is edited, the test fails with the new hash —
# developer workflow:
#   1. Edit tracker/*.js
#   2. Bump TRACKER_VERSION above
#   3. Run pytest; paste the new hash from the failure message below
# This pairing guarantees TRACKER_VERSION is bumped EVERY time any tracker
# script changes — otherwise merchant browsers keep cached stale JS.
TRACKER_SOURCE_HASH = "465b26d54e58bb039968ba34769a8b914b36b96e5bf39e428a90bcd5deb723f7"


def get_tracker_url() -> str:
    """
    Return the canonical tracker script URL with version cache-bust param.

    Example: https://api.hedgesparkhq.com/tracker.js?v=7

    Uses TRACKER_SCRIPT_URL env override if set (for custom CDN deployments).
    """
    override = os.getenv("TRACKER_SCRIPT_URL", "").strip()
    if override:
        # Respect override but still append version if not already present
        if "?v=" not in override and "&v=" not in override:
            return f"{override}?v={TRACKER_VERSION}"
        return override

    app_url = os.getenv("APP_URL", "")
    if not app_url:
        return ""

    return f"{app_url}/tracker.js?v={TRACKER_VERSION}"
