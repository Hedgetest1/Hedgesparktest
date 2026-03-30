"""
Serves storefront JavaScript files:
  /tracker.js       — spark-tracker.js (auto-injected via Script Tag)
  /attribution.js   — spark-attribution.js (merchant pastes in checkout settings)

Cache-Control strategy:
  - tracker.js with ?v= param (versioned URL from script tag):
    Cache for 7 days.  Version bump changes URL → instant refresh.
  - tracker.js without ?v=: Cache 5 minutes (backward compat).
  - attribution.js: Cache 1 hour. Not versioned — changes are rare.
"""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

router = APIRouter()

_TRACKER_PATH = "/opt/wishspark/tracker/spark-tracker.js"
_ATTRIBUTION_PATH = "/opt/wishspark/tracker/spark-attribution.js"

_VERSIONED_HEADERS = {
    "Cache-Control": "public, max-age=604800, immutable",
}

_UNVERSIONED_HEADERS = {
    "Cache-Control": "public, max-age=300, stale-while-revalidate=60",
}

_ATTRIBUTION_HEADERS = {
    "Cache-Control": "public, max-age=3600, stale-while-revalidate=300",
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


@router.get("/attribution.js")
def attribution(shop: str | None = Query(default=None)):
    """
    Serve spark-attribution.js for the checkout thank-you page.

    The ?shop= param is read CLIENT-SIDE by the script itself (not by this
    endpoint) to scope the attribution event to the correct merchant.

    Install instructions for merchants:
      Settings → Checkout → Order status page → Additional scripts
      Add: <script src="https://api.hedgesparkhq.com/attribution.js?shop={{ shop.permanent_domain }}"></script>
    """
    return FileResponse(
        _ATTRIBUTION_PATH,
        media_type="application/javascript",
        filename="attribution.js",
        headers=_ATTRIBUTION_HEADERS,
    )
