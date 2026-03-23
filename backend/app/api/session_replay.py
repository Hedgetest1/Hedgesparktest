"""
GET /analytics/sessions — Session Replay Lite.

Returns the last 10 visitor sessions grouped by visitor_id.
Each row includes: visitor_id, ordered page list, total dwell duration,
last page visited, and event count.

No video. No recording. Structured session timeline from the events table.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/sessions")
def session_list(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    query = text("""
        SELECT
            visitor_id,
            ARRAY_AGG(url ORDER BY COALESCE(timestamp, 0) ASC)
                FILTER (WHERE url IS NOT NULL AND url <> '') AS pages_visited,
            SUM(COALESCE(dwell_seconds, 0)) AS total_duration_seconds,
            MAX(COALESCE(timestamp, 0))     AS last_active_ts,
            COUNT(*)                        AS event_count
        FROM events
        WHERE shop_domain = :shop_domain
        GROUP BY visitor_id
        ORDER BY last_active_ts DESC
        LIMIT 10
    """)

    with engine.begin() as conn:
        rows = conn.execute(query, {"shop_domain": shop}).mappings().all()

    result = []
    for r in rows:
        pages = list(r["pages_visited"] or [])
        result.append({
            "visitor_id": r["visitor_id"] or "unknown",
            "pages_visited": pages,
            "total_duration_seconds": int(r["total_duration_seconds"] or 0),
            "last_page": pages[-1] if pages else None,
            "event_count": int(r["event_count"] or 0),
        })

    return {"sessions": result}
