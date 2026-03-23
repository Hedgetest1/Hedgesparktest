"""
nudge_script.py — Serves tracker JavaScript files to Shopify storefronts.

GET /nudge.js
    Returns spark-nudge.js — the storefront nudge renderer.
    Merchants install this with a single <script> tag on product pages.
    No authentication required — the script is public.
    Cache-Control: 300s (5 minutes).

GET /tracker.js
    Returns spark-tracker.js — the behavioral event tracker.
    This is the URL used as the Shopify Script Tag src during auto-install.
    Also serves as the manual install URL for merchants who prefer to
    add the script tag themselves.

    The URL registered in Shopify Script Tags during OAuth install is:
        {APP_URL}/tracker.js

    Cache-Control: 300s (5 minutes) — short enough to pick up tracker
    updates quickly, long enough to reduce origin load.
    Shopify's CDN caches script tag sources separately with its own TTL.
"""
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/nudge.js")
def nudge_script():
    """
    Serve spark-nudge.js — the storefront nudge renderer.

    The script polls /nudges/active on product pages and renders an
    unobtrusive nudge element when a live nudge is configured.
    """
    return FileResponse(
        path="/opt/wishspark/tracker/spark-nudge.js",
        media_type="application/javascript",
        filename="nudge.js",
        headers={"Cache-Control": "public, max-age=300"},
    )


@router.get("/tracker.js")
def tracker_script():
    """
    Serve spark-tracker.js — the behavioral event tracker.

    This is the canonical URL for the tracker script.  It is registered
    as a Shopify Script Tag during OAuth install so merchants do not need
    to manually add it to their theme.

    The script:
      - Fires page_view, product_view, dwell_time, and scroll events
      - Detects product_id via 5-source fallback chain
      - Buffers events offline (sessionStorage) when network is unavailable
      - Has a top-level error boundary — never crashes the storefront
    """
    return FileResponse(
        path="/opt/wishspark/tracker/spark-tracker.js",
        media_type="application/javascript",
        filename="tracker.js",
        headers={"Cache-Control": "public, max-age=300"},
    )
