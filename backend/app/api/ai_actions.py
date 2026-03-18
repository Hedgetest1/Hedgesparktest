from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.core.database import engine
from app.core.deps import require_api_key, require_shop

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("/actions")
def ai_actions(
    shop: str = Depends(require_shop),
    _: None = Depends(require_api_key),
):
    q = text("""
    WITH visitor_stats AS (
        SELECT
            visitor_id,
            MAX(url) AS url,
            MAX(COALESCE(dwell_seconds,0)) AS dwell,
            MAX(COALESCE(max_scroll_depth,0)) AS scroll,
            COUNT(*) FILTER (WHERE event_type='click') AS clicks
        FROM events
        WHERE shop_domain = :shop_domain
        GROUP BY visitor_id
    )
    SELECT
        visitor_id,
        url,
        dwell,
        scroll,
        clicks
    FROM visitor_stats
    ORDER BY dwell DESC, scroll DESC, clicks DESC
    """)

    actions = []

    with engine.begin() as conn:
        rows = conn.execute(q, {"shop_domain": shop}).mappings().all()

    for r in rows:
        if r["scroll"] >= 70 and r["dwell"] >= 20 and r["clicks"] >= 1:
            actions.append({
                "visitor": r["visitor_id"],
                "page": r["url"],
                "type": "HIGH_INTENT",
                "suggestion": "Show limited-time discount",
                "impact": "HIGH",
            })
        elif r["scroll"] >= 60 and r["clicks"] == 0:
            actions.append({
                "visitor": r["visitor_id"],
                "page": r["url"],
                "type": "HESITATING",
                "suggestion": "Highlight reviews and shipping info",
                "impact": "MEDIUM",
            })
        elif r["dwell"] >= 30 and r["scroll"] < 40:
            actions.append({
                "visitor": r["visitor_id"],
                "page": r["url"],
                "type": "CONFUSED",
                "suggestion": "Add clearer product description",
                "impact": "MEDIUM",
            })

    return {"actions": actions}
