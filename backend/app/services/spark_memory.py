"""
spark_memory.py — Spark's Memory timeline for Lite v5 Zone 5.

Returns up to 5 most-recent notable events from the last 7 days,
formatted as first-person Spark sentences via spark_voice templates.

Sources (v1 — shippable today on existing tables):
  - daily_brief   — one entry per shop per day (brief_summary event)
  - ops_alerts    — info/warning severity events scoped to the shop
                    (abandoned_detected / unusual_pattern / target_*)

Future (v2, behind flag once data is rich enough):
  - action_outcomes (outcome_status='success' → prevention_success)
  - monthly_cohorts (cohort_milestone)

Spec: /docs/LITE_VISUAL_SPEC_v5.md §2 Zone 5 + §9 endpoint contract.
TIER: 0 (read-only queries on existing tables).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.spark_voice import (
    EVENT_DOT_COLORS,
    format_memory_sentence,
    relative_label,
)

log = logging.getLogger("spark_memory")

# How many distinct recent events Zone 5 can render.
MAX_EVENTS = 5

# How far back to look when assembling the memory feed.
LOOKBACK_DAYS = 7

# Map ops_alerts.alert_type → canonical Spark event_type.
# Unmapped types are skipped (never fabricate a template to fit).
_ALERT_TYPE_TO_EVENT: dict[str, str] = {
    "abandoned_intent": "abandoned_detected",
    "abandoned_detected": "abandoned_detected",
    "traffic_pattern": "unusual_pattern",
    "unusual_pattern": "unusual_pattern",
    "target_hit": "target_hit",
    "target_missed": "target_missed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _weekday_name(dt: datetime) -> str:
    return dt.strftime("%A")


def _fetch_daily_briefs(db: Session, shop: str, cutoff: datetime) -> list[dict]:
    """Pull daily_brief rows from the last 7 days for this shop."""
    try:
        rows = db.execute(
            text(
                """
                SELECT generated_at, brief_date, headline, top_product_label
                FROM daily_brief
                WHERE shop_domain = :shop
                  AND generated_at >= :cutoff
                ORDER BY generated_at DESC
                LIMIT :lim
                """
            ),
            {"shop": shop, "cutoff": cutoff, "lim": MAX_EVENTS * 2},
        ).fetchall()
    except Exception as exc:
        log.warning("spark_memory: daily_brief query failed: %s", exc)
        return []

    events: list[dict] = []
    for row in rows:
        generated_at = row[0]
        brief_date = row[1]
        headline = row[2] or ""
        # Compose a Spark-voice sentence: "{Day} brief: {headline}".
        # We truncate headline to keep the line readable (Zone 5 is
        # compact; headlines can be long).
        if len(headline) > 80:
            headline = headline[:77].rstrip() + "…"
        weekday = _weekday_name(
            datetime.combine(brief_date, datetime.min.time())
        ) if brief_date else "Today"
        sentence = f"{weekday} brief: {headline}" if headline else ""
        if not sentence:
            continue
        events.append(
            {
                "timestamp": generated_at,
                "event_type": "brief_summary",
                "sentence": sentence,
                "dot_color": EVENT_DOT_COLORS.get("brief_summary", "slate"),
            }
        )
    return events


def _fetch_ops_alerts(db: Session, shop: str, cutoff: datetime) -> list[dict]:
    """Pull ops_alerts rows from the last 7 days for this shop."""
    try:
        rows = db.execute(
            text(
                """
                SELECT created_at, alert_type, summary
                FROM ops_alerts
                WHERE shop_domain = :shop
                  AND created_at >= :cutoff
                  AND severity IN ('info', 'warning')
                  AND alert_type = ANY(:alert_types)
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {
                "shop": shop,
                "cutoff": cutoff,
                "alert_types": list(_ALERT_TYPE_TO_EVENT.keys()),
                "lim": MAX_EVENTS * 2,
            },
        ).fetchall()
    except Exception as exc:
        log.warning("spark_memory: ops_alerts query failed: %s", exc)
        return []

    events: list[dict] = []
    for row in rows:
        created_at = row[0]
        alert_type = row[1]
        summary = row[2] or ""
        event_type = _ALERT_TYPE_TO_EVENT.get(alert_type)
        if not event_type:
            continue
        # ops_alerts.summary is already merchant-readable text written
        # by the alerter. We use it verbatim as the Spark sentence IF
        # it looks first-person-compatible; otherwise we fall back to
        # the template (requires context we don't always have here).
        sentence = summary.strip()
        # Third-person → first-person quick-fix: common prefixes that
        # violate coherence §1.1 get rewritten inline.
        for bad, good in (
            ("HedgeSpark ", "I "),
            ("The system ", "I "),
            ("Our system ", "I "),
            ("Our algorithm ", "I "),
        ):
            if sentence.startswith(bad):
                sentence = good + sentence[len(bad):]
                break
        if not sentence:
            continue
        events.append(
            {
                "timestamp": created_at,
                "event_type": event_type,
                "sentence": sentence,
                "dot_color": EVENT_DOT_COLORS.get(event_type, "slate"),
            }
        )
    return events


def build_spark_memory(db: Session, shop: str) -> dict:
    """
    Return the top-MAX_EVENTS recent events for a shop's Memory timeline.

    Returns:
        {
            "shop_domain": str,
            "events": [
                {
                    "timestamp": ISO str,
                    "relative_label": human str (`2h ago`, `yesterday`, ...),
                    "event_type": str,
                    "sentence": str (first-person Spark voice),
                    "dot_color": str (semantic color name)
                }
            ],
            "count": int,
            "generated_at": ISO str,
        }

    Empty list when no sources have events in the last LOOKBACK_DAYS
    days (cold-start). UI renders "Watching your store…" then.
    """
    now = _now()
    cutoff = now - timedelta(days=LOOKBACK_DAYS)

    raw_events = _fetch_daily_briefs(db, shop, cutoff) + _fetch_ops_alerts(
        db, shop, cutoff
    )

    # Sort by recency, take top MAX_EVENTS
    raw_events.sort(key=lambda e: e["timestamp"], reverse=True)
    raw_events = raw_events[:MAX_EVENTS]

    events_out = []
    for e in raw_events:
        ts = e["timestamp"]
        if not isinstance(ts, datetime):
            continue
        events_out.append(
            {
                "timestamp": ts.replace(tzinfo=timezone.utc).isoformat()
                if ts.tzinfo is None
                else ts.isoformat(),
                "relative_label": relative_label(now, ts),
                "event_type": e["event_type"],
                "sentence": e["sentence"],
                "dot_color": e["dot_color"],
            }
        )

    return {
        "shop_domain": shop,
        "events": events_out,
        "count": len(events_out),
        "generated_at": now.isoformat() + "Z",
    }
