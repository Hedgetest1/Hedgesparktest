"""
nudge_script.py — Serves spark-nudge.js to Shopify storefronts.

GET /nudge.js

    Returns the spark-nudge.js file as JavaScript with strong cache headers.
    Merchants install this with a single <script> tag on product pages:

        <script async src="https://<wishspark-api>/nudge.js?shop={{ shop.permanent_domain }}"></script>

    No authentication required — the script itself is public.
    The ?shop= param is resolved by the script at runtime.

    The script file is served from /opt/wishspark/tracker/spark-nudge.js.
    Cache-Control is set to 300s (5 minutes) — short enough to pick up
    nudge copy changes quickly, long enough to reduce load.
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

    Cache-Control: max-age=300 (5 minutes) keeps the script fresh while
    reducing load on the backend.
    """
    return FileResponse(
        path="/opt/wishspark/tracker/spark-nudge.js",
        media_type="application/javascript",
        filename="nudge.js",
        headers={"Cache-Control": "public, max-age=300"},
    )
