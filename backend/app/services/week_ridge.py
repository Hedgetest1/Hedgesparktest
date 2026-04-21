"""
week_ridge.py — 7-day Week Ridge chart payload for Lite v5 Zone 4.

Returns two parallel 7-day series in merchant's currency:
  - at_risk_eur: estimated daily high-intent visitor loss
    (count × 30-day AOV × baseline recovery rate)
  - captured_eur: actual daily order revenue from shop_orders

Plus a week-over-week pct on captured revenue so the interpretation
sentence has a "+12% better than last week" hook.

Cold-start: <3 days of shop_order activity → returns an empty `days`
list with `cold_start: true`. The UI renders "Watching your week
build" instead of a flat chart. Never fabricate.

Spec: /docs/LITE_VISUAL_SPEC_v5.md §2 Zone 4 + §9 week-ridge endpoint.
TIER: 0 (read-only queries on existing events + shop_orders tables).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("week_ridge")

# Conservative recovery rate for high-intent abandoners. Matches the
# hardcoded 0.08 in revenue_at_risk._compute_abandoned_high_intent;
# when that file promotes to a module constant, we re-import.
_HIGH_INTENT_RECOVERY_RATE = 0.08

_CACHE_TTL_SECONDS = 5 * 60
_CACHE_KEY_PREFIX = "hs:week_ridge:v1"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cache_get(shop: str) -> dict | None:
    try:
        from app.core.redis_client import _client

        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return

            record_silent_return("week_ridge.cache_read")
            return None
        key = f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
        raw = rc.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        log.warning("week_ridge: cache read failed: %s", exc)
        return None


def _cache_set(shop: str, data: dict) -> None:
    try:
        from app.core.redis_client import _client

        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return

            record_silent_return("week_ridge.cache_write")
            return
        key = f"{_CACHE_KEY_PREFIX}:{hashlib.md5(shop.encode()).hexdigest()[:16]}"
        rc.setex(key, _CACHE_TTL_SECONDS, json.dumps(data, default=str))
    except Exception as exc:
        log.warning("week_ridge: cache write failed: %s", exc)


def _captured_by_day(
    db: Session, shop: str, days: int, currency: str | None
) -> dict[str, float]:
    """Return {YYYY-MM-DD: captured_eur} for the last `days` days.

    Filters by shop's primary currency to avoid mixing amounts from
    merchants who accept multiple currencies (prevents data_truth
    money_aggregation_no_currency violation).
    """
    try:
        rows = db.execute(
            text(
                """
                SELECT DATE(created_at AT TIME ZONE 'UTC')::text AS day,
                       COALESCE(SUM(total_price), 0) AS captured
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - make_interval(days => :days)
                  AND total_price > 0
                  AND (:currency IS NULL OR currency = :currency)
                GROUP BY day
                ORDER BY day ASC
                """
            ),
            {"shop": shop, "days": days, "currency": currency},
        ).fetchall()
    except Exception as exc:
        log.warning("week_ridge: captured query failed: %s", exc)
        return {}
    return {row[0]: float(row[1] or 0) for row in rows}


def _abandoned_high_intent_by_day(db: Session, shop: str, days: int) -> dict[str, int]:
    """Return {YYYY-MM-DD: high_intent_count} for the last `days` days.

    Same predicate as revenue_at_risk._compute_abandoned_high_intent:
    visitors with event_type='add_to_cart' OR (event_type='dwell_time'
    AND max_scroll_depth >= 50), excluded if they later purchased.

    Grouped by day. Uses events.timestamp (millisecond epoch) converted
    to a day bucket via to_timestamp.
    """
    cutoff_ms = int((_now() - timedelta(days=days)).timestamp() * 1000)
    try:
        rows = db.execute(
            text(
                """
                SELECT DATE(to_timestamp(e.timestamp / 1000))::text AS day,
                       COUNT(DISTINCT e.visitor_id) AS high_intent
                FROM events e
                WHERE e.shop_domain = :shop
                  AND e.timestamp >= :cutoff_ms
                  AND (
                       e.event_type = 'add_to_cart'
                    OR (e.event_type = 'dwell_time'
                        AND COALESCE(e.max_scroll_depth, 0) >= 50)
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM visitor_purchase_sessions vps
                      WHERE vps.shop_domain = e.shop_domain
                        AND vps.visitor_id = e.visitor_id
                  )
                GROUP BY day
                ORDER BY day ASC
                """
            ),
            {"shop": shop, "cutoff_ms": cutoff_ms},
        ).fetchall()
    except Exception as exc:
        log.warning("week_ridge: abandoned_high_intent query failed: %s", exc)
        return {}
    return {row[0]: int(row[1] or 0) for row in rows}


def _aov_last_30d(db: Session, shop: str, currency: str | None) -> float:
    """Return shop's 30-day AOV in shop currency."""
    try:
        row = db.execute(
            text(
                """
                SELECT COALESCE(AVG(total_price), 0)
                FROM shop_orders
                WHERE shop_domain = :shop
                  AND created_at >= NOW() - INTERVAL '30 days'
                  AND total_price > 0
                  AND (:currency IS NULL OR currency = :currency)
                """
            ),
            {"shop": shop, "currency": currency},
        ).fetchone()
    except Exception as exc:
        log.warning("week_ridge: aov query failed: %s", exc)
        return 0.0
    return float(row[0] or 0) if row else 0.0


def compute_week_ridge(db: Session, shop: str) -> dict:
    """
    Compute the 7-day Week Ridge payload for a shop.

    Returns:
        {
            "shop_domain": str,
            "days": [{date: ISO YYYY-MM-DD, at_risk_eur: float, captured_eur: float}],
            "currency": str,
            "week_over_week_captured_pct": float | None,
            "cold_start": bool,
            "generated_at": ISO datetime,
        }

    Cold-start: <3 days with shop_order activity in the last 14 days.
    """
    cache_hit = _cache_get(shop)
    if cache_hit is not None:
        return cache_hit

    currency = get_shop_currency(db, shop)
    now = _now()

    # Captured revenue per day, last 14 days (need 14 for week-over-week)
    captured = _captured_by_day(db, shop, days=14, currency=currency)

    # Cold-start: fewer than 3 days of activity in the last 14 days
    if len(captured) < 3:
        result: dict = {
            "shop_domain": shop,
            "days": [],
            "currency": currency or "USD",
            "week_over_week_captured_pct": None,
            "cold_start": True,
            "generated_at": now.isoformat() + "Z",
        }
        _cache_set(shop, result)
        return result

    # At-risk per day, last 7 days
    high_intent = _abandoned_high_intent_by_day(db, shop, days=7)
    aov = _aov_last_30d(db, shop, currency)
    # If AOV is zero (no orders in 30d) we still render days — but at_risk
    # is 0 and the front emerald layer carries the week. Honest.

    # Build the 7-day series ending today (shop tz approximated as UTC)
    days_series: list[dict] = []
    today_utc = now.date()
    last_7_days_captured = 0.0
    for i in range(6, -1, -1):  # 6..0 for oldest..newest
        day = today_utc - timedelta(days=i)
        day_str = day.isoformat()
        cap = round(captured.get(day_str, 0.0), 2)
        hi_count = high_intent.get(day_str, 0)
        at_risk = round(hi_count * _HIGH_INTENT_RECOVERY_RATE * aov, 2)
        days_series.append(
            {
                "date": day_str,
                "at_risk_eur": at_risk,
                "captured_eur": cap,
            }
        )
        last_7_days_captured += cap

    # Week-over-week on captured revenue
    prior_7_captured = 0.0
    for i in range(13, 6, -1):  # 13..7 for oldest..newest of prior week
        day = today_utc - timedelta(days=i)
        prior_7_captured += captured.get(day.isoformat(), 0.0)

    if prior_7_captured > 0:
        wow_pct = round(
            ((last_7_days_captured - prior_7_captured) / prior_7_captured) * 100,
            1,
        )
    else:
        wow_pct = None

    result = {
        "shop_domain": shop,
        "days": days_series,
        "currency": currency or "USD",
        "week_over_week_captured_pct": wow_pct,
        "cold_start": False,
        "generated_at": now.isoformat() + "Z",
    }
    _cache_set(shop, result)
    return result
