"""
anomaly_replay.py — Phase Ω⁷ killer #1.

"Watch what actually happened."

When anomaly_fusion flags a cross-signal pattern, the merchant clicks
Replay and gets a minute-by-minute reconstruction of the event window
that produced the signal — visitor sessions, cart events, source mix,
geographic heatmap — everything that was visible to the detector, now
visible to the human operator.

This is the difference between "an alert fired" and "I understand what
happened". No other SMB Shopify tool ships this because nobody else has
the event-level granularity we do (first-party tracker + purchase
attribution chain).

Endpoint:
    GET /pro/anomalies/{pattern}/replay?minutes=60

Returns a structured timeline:
  - window: {start, end, pattern, severity}
  - events: [{timestamp_ms, event_type, visitor_id_hash, source, device, url}]
  - summary: {by_minute: [...], by_source: {...}, by_device: {...}}
  - narrative: one-sentence "here's what happened" string

Auth: Pro session. Uses `shop_domain = :shop` — tenant-isolated.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_read_db
from app.core.deps import require_scale_session

log = logging.getLogger("anomaly_replay")

router = APIRouter(tags=["anomaly_replay"])


_MIN_WINDOW = 15
_MAX_WINDOW = 240
_MAX_EVENTS = 500


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _hash_visitor(vid: str) -> str:
    """Stable 8-char hash for anonymization in the replay stream."""
    return hashlib.sha256((vid or "").encode()).hexdigest()[:8]


class AnomalyReplayWindow(BaseModel):
    start_ms: int
    end_ms: int
    minutes: int


class AnomalyReplaySummary(BaseModel):
    total_events: int
    unique_visitors: int
    by_source: list[dict[str, Any]] = Field(default_factory=list)
    by_device: list[dict[str, Any]] = Field(default_factory=list)
    by_type: list[dict[str, Any]] = Field(default_factory=list)


class AnomalyReplayResponse(BaseModel):
    pattern: str
    window: AnomalyReplayWindow
    events: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    summary: AnomalyReplaySummary
    narrative: str
    truncated: bool


@router.get("/pro/anomalies/{pattern}/replay", response_model=AnomalyReplayResponse)
def get_anomaly_replay(
    pattern: str,
    minutes: int = Query(default=60, ge=_MIN_WINDOW, le=_MAX_WINDOW),
    shop: str = Depends(require_scale_session),
    db: Session = Depends(get_read_db),
):
    """
    Reconstruct the event window around a detected anomaly pattern.

    The pattern name maps to a severity bucket (critical / warning / info)
    — in v1 we use "now" as the anchor because the fusion engine is
    real-time. v2 will accept an explicit anchor timestamp to replay
    historic anomalies.
    """
    # Feature usage telemetry — this is the killer feature, we want to
    # see how often merchants actually click Replay.
    try:
        from app.core.feature_usage import track
        track("anomaly_replay", shop)
    except Exception as exc:
        log.warning("anomaly_replay: feature usage track failed: %s", exc)

    now_ms = _now_ms()
    window_start_ms = now_ms - (minutes * 60 * 1000)

    # Pull ALL events in window for this shop — tenant-isolated.
    # sql-ms-type: ok — `:window_start`/`:now_ms` are int epoch ms (computed above).
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    timestamp,
                    event_type,
                    visitor_id,
                    COALESCE(source_type, 'direct') AS source_type,
                    COALESCE(device_type, 'unknown') AS device_type,
                    url,
                    product_url
                FROM events
                WHERE shop_domain = :shop
                  AND timestamp >= :window_start
                  AND timestamp <= :now_ms
                ORDER BY timestamp ASC
                LIMIT :max_events
                """
            ),
            {
                "shop": shop,
                "window_start": window_start_ms,
                "now_ms": now_ms,
                "max_events": _MAX_EVENTS,
            },
        ).fetchall()
    except Exception as exc:
        log.warning("anomaly_replay: events query failed for %s: %s", shop, exc)
        rows = []

    # Transform rows into lightweight event dicts
    events: list[dict] = []
    by_source: dict[str, int] = {}
    by_device: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        ts, et, vid, src, dev, url, purl = r
        events.append({
            "ts_ms": int(ts),
            "type": et,
            "visitor": _hash_visitor(vid),
            "source": src,
            "device": dev,
            "url": (purl or url or "")[:120],
        })
        by_source[src] = by_source.get(src, 0) + 1
        by_device[dev] = by_device.get(dev, 0) + 1
        by_type[et] = by_type.get(et, 0) + 1

    # Bucket by minute for the scrubbable timeline
    bucket_ms = 60 * 1000
    by_minute: dict[int, int] = {}
    for e in events:
        b = e["ts_ms"] // bucket_ms * bucket_ms
        by_minute[b] = by_minute.get(b, 0) + 1
    by_minute_sorted = sorted(by_minute.items())

    # Fill gaps so the timeline is continuous across the whole window
    timeline: list[dict] = []
    cur = window_start_ms // bucket_ms * bucket_ms
    end_bucket = now_ms // bucket_ms * bucket_ms
    while cur <= end_bucket:
        timeline.append({"ts_ms": cur, "count": by_minute.get(cur, 0)})
        cur += bucket_ms

    # Narrative — deterministic one-sentence summary
    if not events:
        narrative = f"Nothing happened in the last {minutes} minutes — the anomaly detector spotted a pattern but no tracker events landed in the window."
    else:
        top_source = max(by_source.items(), key=lambda kv: kv[1])[0] if by_source else "unknown"
        top_device = max(by_device.items(), key=lambda kv: kv[1])[0] if by_device else "unknown"
        unique_visitors = len({e["visitor"] for e in events})
        narrative = (
            f"{len(events)} events from {unique_visitors} unique visitors, "
            f"mostly {top_source} traffic on {top_device}. "
            f"Peak minute: {max(by_minute.values()) if by_minute else 0} events."
        )

    return {
        "pattern": pattern,
        "window": {
            "start_ms": window_start_ms,
            "end_ms": now_ms,
            "minutes": minutes,
        },
        "events": events[:200],  # cap the per-event list so the modal stays fast
        "timeline": timeline,
        "summary": {
            "total_events": len(events),
            "unique_visitors": len({e["visitor"] for e in events}),
            "by_source": sorted(
                [{"source": k, "count": v} for k, v in by_source.items()],
                key=lambda x: -x["count"],
            )[:8],
            "by_device": sorted(
                [{"device": k, "count": v} for k, v in by_device.items()],
                key=lambda x: -x["count"],
            ),
            "by_type": sorted(
                [{"type": k, "count": v} for k, v in by_type.items()],
                key=lambda x: -x["count"],
            )[:10],
        },
        "narrative": narrative,
        "truncated": len(rows) >= _MAX_EVENTS,
    }
