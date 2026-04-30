import hashlib
import json
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_merchant_session

_log = logging.getLogger(__name__)

# Redis caches on the two analytics endpoints below. Both are
# per-shop pure aggregates that recompute from `events` on every
# query — cheap at 2 merchants, quadratic at 10k. 60s TTL is tight
# enough that the dashboard feels live and loose enough to absorb
# N concurrent tab-refreshes per shop.
_VI_CACHE_PREFIX = "hs:vint:v1"
_VI_CACHE_TTL_S = 60


def _cache_get(prefix: str, shop: str) -> dict | None:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            # Redis unavailable — fall through to SQL recompute.
            # Observed so ops sees if Redis degrades.
            record_silent_return("visitor_scores.redis_client_none")
            return None
        key = f"{prefix}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
        raw = rc.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        # Silent fallback: Redis unavailable → recompute from SQL. Log
        # the incident so ops catches if Redis degrades without the
        # endpoint user-facing-breaking. Observability required by
        # audit_silent_returns.py preflight.
        _log.warning("visitor_scores cache read failed: %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("visitor_scores.cache_read")
        return None


def _cache_set(prefix: str, shop: str, data: dict, ttl: int) -> None:
    try:
        from app.core.redis_client import _client
        from app.core.silent_fallback import record_silent_return
        rc = _client()
        if rc is None:
            # Redis unavailable — skip cache write, response still
            # OK. Observed so ops sees Redis degradation patterns.
            record_silent_return("visitor_scores.redis_client_none")
            return
        key = f"{prefix}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
        rc.setex(key, ttl, json.dumps(data, default=str))
    except Exception as exc:
        # Silent fallback: Redis write failures don't degrade the
        # response (caller already has the computed result), but we
        # surface the failure for ops observability per preflight rule.
        _log.warning("visitor_scores cache write failed: %s", exc)
        from app.core.silent_fallback import record_silent_return
        record_silent_return("visitor_scores.cache_write")

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ---------------------------------------------------------------------------
# Intent classification thresholds. These are the single source of truth
# for how a visitor's conversion_score translates to "hot / warm / cold".
#
# Score formula (in SQL below):
#   LEAST(dwell_seconds, 60) * 0.6 + max_scroll_depth * 0.3 + clicks * 10
#
# Realistic ceiling is ~116 (60s dwell * 0.6 + 100% scroll * 0.3 + 5 clicks).
# Thresholds picked so that:
#   - HOT means the visitor scrolled deep + dwelled + clicked at least once
#     (a visitor showing real buying behavior)
#   - WARM means some engagement (dwell + scroll, but no clicks)
#   - COLD means a pass-through / bounce visitor
#
# If thresholds change, both the SQL classifier and any frontend copy
# that references "hot/warm/cold" criteria must be reviewed together.
# ---------------------------------------------------------------------------
HOT_THRESHOLD = 50.0
WARM_THRESHOLD = 20.0


class VisitorIntentCounts(BaseModel):
    total_visitors: int = 0
    hot_visitors: int = 0
    warm_visitors: int = 0
    cold_visitors: int = 0
    hot_threshold: float = HOT_THRESHOLD
    warm_threshold: float = WARM_THRESHOLD


@router.get("/visitor-scores")
def visitor_scores(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    query = text("""
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
            clicks,
            (
                LEAST(dwell,60)*0.6 +
                scroll*0.3 +
                clicks*10
            ) AS conversion_score
        FROM visitor_stats
        ORDER BY conversion_score DESC
        LIMIT 20
    """)
    rows = db.execute(query, {"shop_domain": shop}).mappings().all()
    return {"visitors": [dict(r) for r in rows]}


@router.get("/visitor-intent-classification", response_model=VisitorIntentCounts)
def visitor_intent_classification(
    shop: str = Depends(require_merchant_session),
    db: Session = Depends(get_db),
):
    cached = _cache_get(_VI_CACHE_PREFIX, shop)
    if cached is not None:
        return VisitorIntentCounts(**cached)
    """Aggregate every visitor in the shop into hot/warm/cold tiers.

    This is the data source for the Lite-tier Visitor Intent card:
    three counts a merchant can read at a glance — "23 hot, 145 warm,
    412 cold right now". The per-visitor drill-down lives in
    /visitor-scores (top 20 by score) and is a Pro upsell surface for
    the full ranked list.

    Accessible to all merchant sessions (no plan gate); the Lite UI
    simply doesn't render the drill-down.

    Thresholds match the HOT_THRESHOLD / WARM_THRESHOLD constants
    above; they are echoed in the response so the UI can show them
    as tooltip / methodology copy without hardcoding.
    """
    query = text("""
        WITH visitor_stats AS (
            SELECT
                visitor_id,
                MAX(COALESCE(dwell_seconds,0)) AS dwell,
                MAX(COALESCE(max_scroll_depth,0)) AS scroll,
                COUNT(*) FILTER (WHERE event_type='click') AS clicks
            FROM events
            WHERE shop_domain = :shop_domain
            GROUP BY visitor_id
        ),
        scored AS (
            SELECT
                visitor_id,
                (
                    LEAST(dwell,60)*0.6 +
                    scroll*0.3 +
                    clicks*10
                ) AS conversion_score
            FROM visitor_stats
        )
        SELECT
            COUNT(*) AS total_visitors,
            COUNT(*) FILTER (WHERE conversion_score > :hot_threshold) AS hot_visitors,
            COUNT(*) FILTER (
                WHERE conversion_score > :warm_threshold
                  AND conversion_score <= :hot_threshold
            ) AS warm_visitors,
            COUNT(*) FILTER (WHERE conversion_score <= :warm_threshold) AS cold_visitors
        FROM scored
    """)
    row = db.execute(
        query,
        {
            "shop_domain": shop,
            "hot_threshold": HOT_THRESHOLD,
            "warm_threshold": WARM_THRESHOLD,
        },
    ).mappings().one_or_none()

    if row is None:
        result = VisitorIntentCounts()
    else:
        result = VisitorIntentCounts(
            total_visitors=int(row["total_visitors"] or 0),
            hot_visitors=int(row["hot_visitors"] or 0),
            warm_visitors=int(row["warm_visitors"] or 0),
            cold_visitors=int(row["cold_visitors"] or 0),
        )
    _cache_set(_VI_CACHE_PREFIX, shop, result.model_dump(), _VI_CACHE_TTL_S)
    return result
