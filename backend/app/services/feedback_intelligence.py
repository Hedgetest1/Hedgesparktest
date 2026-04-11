"""
feedback_intelligence.py — Aggregate merchant feedback into actionable themes.

Scans inbound emails classified as feature_request or suggestion,
groups them by keyword similarity, and surfaces recurring themes
for operator / product visibility.

No LLM. Deterministic keyword extraction + grouping.

Public interface:
    compute_feedback_themes(db) -> list[dict]
    get_feedback_summary(db) -> dict

Called by: ops API (GET /ops/feedback/themes)
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

log = logging.getLogger("feedback_intelligence")

_FEEDBACK_WINDOW_DAYS = 30

# Domain keywords that map feedback to product areas
_AREA_KEYWORDS: dict[str, list[str]] = {
    "nudges": ["nudge", "popup", "notification", "banner", "message", "overlay"],
    "analytics": ["report", "analytics", "chart", "graph", "metric", "dashboard", "stats"],
    "attribution": ["attribution", "source", "utm", "campaign", "channel", "referrer"],
    "tracking": ["tracker", "pixel", "events", "visitor", "tracking", "script"],
    "segments": ["segment", "cohort", "audience", "group", "filter"],
    "integrations": ["klaviyo", "integration", "connect", "sync", "api", "webhook"],
    "pricing": ["price", "pricing", "cost", "plan", "billing", "subscription", "upgrade"],
    "export": ["export", "csv", "download", "spreadsheet", "data export"],
    "email": ["email", "digest", "newsletter", "notification", "alert"],
    "mobile": ["mobile", "phone", "responsive", "app"],
}


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _extract_area(text_content: str) -> str:
    """Extract product area from feedback text using keyword matching."""
    text_lower = (text_content or "").lower()
    scores: dict[str, int] = {}

    for area, keywords in _AREA_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[area] = score

    if scores:
        return max(scores, key=scores.get)
    return "general"


def compute_feedback_themes(db: Session) -> list[dict]:
    """
    Aggregate feature requests and suggestions into themes.

    Returns list of themes sorted by request count (descending):
        [{"area": str, "count": int, "shops": list[str],
          "sample_subjects": list[str], "first_seen": str, "last_seen": str}]
    """
    cutoff = _now() - timedelta(days=_FEEDBACK_WINDOW_DAYS)

    rows = db.execute(text("""
        SELECT id, shop_domain, subject, body_text, classification, created_at
        FROM inbound_emails
        WHERE classification IN ('feature_request', 'suggestion')
          AND created_at >= :cutoff
        ORDER BY created_at DESC
    """), {"cutoff": cutoff}).fetchall()

    if not rows:
        return []

    # Group by area
    themes: dict[str, dict] = {}

    for row in rows:
        email_id, shop, subject, body, classification, created_at = row
        combined = f"{subject or ''} {body or ''}"
        area = _extract_area(combined)

        if area not in themes:
            themes[area] = {
                "area": area,
                "count": 0,
                "shops": set(),
                "sample_subjects": [],
                "first_seen": created_at,
                "last_seen": created_at,
            }

        t = themes[area]
        t["count"] += 1
        if shop:
            t["shops"].add(shop)
        if subject and len(t["sample_subjects"]) < 5:
            t["sample_subjects"].append(subject[:100])
        if created_at < t["first_seen"]:
            t["first_seen"] = created_at
        if created_at > t["last_seen"]:
            t["last_seen"] = created_at

    # Convert to sorted list
    result = []
    for t in sorted(themes.values(), key=lambda x: x["count"], reverse=True):
        result.append({
            "area": t["area"],
            "count": t["count"],
            "unique_shops": len(t["shops"]),
            "shops": sorted(t["shops"])[:10],  # cap for response size
            "sample_subjects": t["sample_subjects"],
            "first_seen": t["first_seen"].isoformat() + "Z" if t["first_seen"] else None,
            "last_seen": t["last_seen"].isoformat() + "Z" if t["last_seen"] else None,
        })

    return result


def get_feedback_summary(db: Session) -> dict:
    """
    High-level feedback summary for operator dashboard.

    Returns:
        {
            "total_feedback_30d": int,
            "unique_shops": int,
            "top_area": str | None,
            "themes": list[dict],   # from compute_feedback_themes
            "classification_breakdown": dict[str, int],
        }
    """
    cutoff = _now() - timedelta(days=_FEEDBACK_WINDOW_DAYS)

    # Counts
    total = db.execute(text("""
        SELECT COUNT(*) FROM inbound_emails
        WHERE classification IN ('feature_request', 'suggestion')
          AND created_at >= :cutoff
    """), {"cutoff": cutoff}).scalar() or 0

    shops = db.execute(text("""
        SELECT COUNT(DISTINCT shop_domain) FROM inbound_emails
        WHERE classification IN ('feature_request', 'suggestion')
          AND created_at >= :cutoff
          AND shop_domain IS NOT NULL
    """), {"cutoff": cutoff}).scalar() or 0

    # Breakdown by classification
    breakdown_rows = db.execute(text("""
        SELECT classification, COUNT(*) FROM inbound_emails
        WHERE classification IN ('feature_request', 'suggestion', 'praise')
          AND created_at >= :cutoff
        GROUP BY classification
    """), {"cutoff": cutoff}).fetchall()
    breakdown = {row[0]: row[1] for row in breakdown_rows}

    themes = compute_feedback_themes(db)

    return {
        "total_feedback_30d": total,
        "unique_shops": shops,
        "top_area": themes[0]["area"] if themes else None,
        "themes": themes,
        "classification_breakdown": breakdown,
    }
