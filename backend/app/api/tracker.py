"""
Serves the canonical tracker script: /opt/wishspark/tracker/spark-tracker.js

This is the ONLY tracker delivery path.  All Shopify Script Tag registrations
point to {APP_URL}/tracker.js?v={TRACKER_VERSION}.

Cache-Control strategy:
  - When ?v= param is present (versioned URL from script tag):
    Cache for 7 days.  Browser will re-fetch only when version bumps
    (new script tag URL) — zero stale cache risk.
  - When ?v= param is absent (direct/legacy access):
    Cache for 5 minutes.  Short enough to pick up changes quickly.
"""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

router = APIRouter()

_TRACKER_PATH = "/opt/wishspark/tracker/spark-tracker.js"

# Versioned URL → long cache (7 days). Version bump changes the URL entirely.
_VERSIONED_HEADERS = {
    "Cache-Control": "public, max-age=604800, immutable",
}

# Unversioned URL → short cache (5 minutes). Backward compat for old tags.
_UNVERSIONED_HEADERS = {
    "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
}


@router.get("/tracker.js")
def tracker(v: str | None = Query(default=None)):
    headers = _VERSIONED_HEADERS if v else _UNVERSIONED_HEADERS
    return FileResponse(
        _TRACKER_PATH,
        media_type="application/javascript",
        filename="tracker.js",
        headers=headers,
    )
