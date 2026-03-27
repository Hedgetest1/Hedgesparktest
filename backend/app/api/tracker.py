"""
Serves the canonical tracker script: /opt/wishspark/tracker/spark-tracker.js

This is the ONLY tracker delivery path.  All Shopify Script Tag registrations
point to {APP_URL}/tracker.js which is handled by this endpoint.

Cache-Control: 5 minutes max-age (short enough to pick up tracker updates,
long enough to avoid redundant downloads from the same visitor session).
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_TRACKER_PATH = "/opt/wishspark/tracker/spark-tracker.js"

_CACHE_HEADERS = {
    "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
}


@router.get("/tracker.js")
def tracker():
    return FileResponse(
        _TRACKER_PATH,
        media_type="application/javascript",
        filename="tracker.js",
        headers=_CACHE_HEADERS,
    )
