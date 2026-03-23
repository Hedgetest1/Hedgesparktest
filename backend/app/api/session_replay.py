"""
GET /analytics/sessions — Behavioral Session Timeline.

Returns the last 10 visitor sessions grouped by visitor_id.
Each row includes: visitor_id, ordered page list, total dwell duration,
last page visited, and event count.

NOT a video session replay — no screen recording, no DOM snapshots.
This is a structured behavioral timeline built from the events table.
It shows WHAT visitors did (pages visited, time spent) but not HOW.

The "session_replay" module name and /sessions path are intentional —
this surface replaces the category of insight that session replay tools
provide (path analysis, drop-off points) using only behavioural events,
without any privacy-invasive recording infrastructure.

Dashboard label: "Session Timeline" (not "Session Replay") to accurately
communicate what this surface shows to merchants.

Data source: events table (event_type IN page_view, dwell_time, scroll).
Capture: spark-tracker.js — fires on every page load and page leave.
Latency: events appear within ~1 second of the visitor action.
"""
from datetime import datetime, timezone

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
    """
    Behavioral session timeline — last 10 visitor sessions for the shop.

    Each session shows:
      visitor_id            — pseudonymous localStorage UUID
      pages_visited         — ordered list of URLs visited (by event timestamp)
      total_duration_seconds — sum of dwell_seconds across the session
      last_page             — most recently visited URL
      event_count           — number of events in the session
      last_active_ts        — timestamp of the last event (ms epoch)

    Behavior notes
    --------------
    - One "session" = all events for a visitor_id grouped together.
      This is a session-less grouping, not a time-windowed session.
      Long-term return visitors appear as a single row with all their pages.
    - dwell_seconds is populated only for dwell_time events.  page_view
      events contribute to page list and event count but not duration.
    - Ordered by most recent last_active_ts so the freshest sessions appear first.

    This is NOT a video replay surface.  No screen recording, no DOM
    snapshots, no mouse tracking.  The data is a structured event log.
    """
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
            "visitor_id":              r["visitor_id"] or "unknown",
            "pages_visited":           pages,
            "total_duration_seconds":  int(r["total_duration_seconds"] or 0),
            "last_page":               pages[-1] if pages else None,
            "event_count":             int(r["event_count"] or 0),
            "last_active_ts":          int(r["last_active_ts"] or 0),
        })

    return {
        "sessions":          result,
        "surface_type":      "behavioral_session_timeline",
        "surface_note":      (
            "Structured behavioral timeline — not video session replay. "
            "Shows pages visited and time spent per visitor."
        ),
        "generated_at":      datetime.now(timezone.utc).isoformat() + "Z",
    }
