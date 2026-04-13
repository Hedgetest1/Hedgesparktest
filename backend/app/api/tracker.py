"""
Serves storefront JavaScript files:
  /tracker.js       — spark-tracker.js (auto-injected via Script Tag)
  /attribution.js   — spark-attribution.js (merchant pastes in checkout settings)

Cache-Control strategy (ε4):
  - tracker.js with ?v= param (versioned URL from script tag):
    Cache 1 YEAR at browser AND at CDN edge (Cloudflare / Fastly / etc).
    Version bump changes URL → instant refresh for everyone. Safe because
    the URL itself is the cache key, so a bump is atomic.
  - tracker.js without ?v=: Cache 5 minutes (backward compat).
  - attribution.js: Cache 1 hour. Not versioned — changes are rare.

CDN headers
-----------
- `public`            — cacheable by all
- `max-age=X`         — browser cache
- `s-maxage=X`        — CDN cache (can differ from browser)
- `immutable`         — browser never revalidates (only valid with version)
- `stale-while-revalidate` — serve stale while fetching fresh in background

To deploy behind Cloudflare (recommended):
  1. Put `api.hedgesparkhq.com` behind Cloudflare proxy (orange cloud)
  2. Cache Rule: "Cache Everything" for `/tracker.js*` + `/nudge-script.js*`
  3. Edge TTL: Respect origin headers (the ones set below)
  Result: storefront visitors worldwide get ~10ms edge latency on the
  tracker load. Origin backend is only hit on version bumps.
"""
from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

router = APIRouter()

_TRACKER_PATH = "/opt/wishspark/tracker/spark-tracker.js"
_ATTRIBUTION_PATH = "/opt/wishspark/tracker/spark-attribution.js"

# 1 year browser cache, 1 year CDN cache, immutable — valid because
# the URL has ?v={TRACKER_VERSION}. A version bump hits a new URL.
_ONE_YEAR = 31536000
_VERSIONED_HEADERS = {
    "Cache-Control": f"public, max-age={_ONE_YEAR}, s-maxage={_ONE_YEAR}, immutable",
    "Vary": "Accept-Encoding",
    "X-Content-Type-Options": "nosniff",
}

_UNVERSIONED_HEADERS = {
    "Cache-Control": "public, max-age=300, s-maxage=60, stale-while-revalidate=60",
    "Vary": "Accept-Encoding",
    "X-Content-Type-Options": "nosniff",
}

_ATTRIBUTION_HEADERS = {
    "Cache-Control": "public, max-age=3600, s-maxage=3600, stale-while-revalidate=300",
    "Vary": "Accept-Encoding",
    "X-Content-Type-Options": "nosniff",
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
