"""
proactive_chat.py — System-initiated chat messages for merchants.

Deterministic, cooldown-gated proactive messages that make the chatbot
feel alive and attentive. No LLM. No spam. Every trigger has explicit
cooldown rules and dedup.

Triggers:
    1. post_connect    — First visit after store connected. 24h cooldown.
    2. post_fix        — After a verified fix was delivered. 12h cooldown.
    3. low_activity    — Merchant connected but no events flowing. 48h cooldown.
    4. post_feature_ack — After feature request logged. 72h cooldown.

Architecture:
    Uses Redis keys for cooldown tracking.
    Messages are generated on-the-fly during polling (no stored message queue).
    Each message gets a deterministic ID for dedup.
    Frontend acks via POST /chat/support/proactive/{id}/ack → sets Redis key.

Public interface:
    get_pending_proactive_messages(db, shop_domain) -> list[dict]
    ack_proactive_message(db, shop_domain, message_id) -> None
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

log = logging.getLogger("proactive_chat")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _redis():
    """Get Redis client or None."""
    try:
        from app.core.redis_client import _client
        return _client()
    except Exception:
        return None


def _cooldown_key(shop_domain: str, trigger: str) -> str:
    return f"hs:proactive:{shop_domain}:{trigger}"


def _ack_key(shop_domain: str, message_id: str) -> str:
    return f"hs:proactive_ack:{shop_domain}:{message_id}"


def _is_on_cooldown(shop_domain: str, trigger: str, cooldown_hours: int) -> bool:
    """Check if a trigger is on cooldown for this shop."""
    rc = _redis()
    if not rc:
        return False  # no Redis = allow (will generate once, then rely on ack dedup)
    key = _cooldown_key(shop_domain, trigger)
    return rc.exists(key) == 1


def _set_cooldown(shop_domain: str, trigger: str, cooldown_hours: int) -> None:
    """Set cooldown for a trigger."""
    rc = _redis()
    if not rc:
        return
    key = _cooldown_key(shop_domain, trigger)
    rc.setex(key, cooldown_hours * 3600, "1")


def _is_acked(shop_domain: str, message_id: str) -> bool:
    """Check if a message was already acknowledged."""
    rc = _redis()
    if not rc:
        return False
    return rc.exists(_ack_key(shop_domain, message_id)) == 1


def _message_id(shop_domain: str, trigger: str, context: str = "") -> str:
    """Generate a deterministic message ID for dedup."""
    raw = f"{shop_domain}:{trigger}:{context}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Trigger definitions
# ---------------------------------------------------------------------------

_TRIGGERS = [
    {
        "name": "post_connect",
        "cooldown_hours": 24,
        "message": (
            "Your store is connected and data is flowing. "
            "Take a few minutes to explore the dashboard \u2014 and if anything "
            "feels off or unclear, tell me here. That\u2019s exactly what this "
            "test phase is for."
        ),
    },
    {
        "name": "post_fix",
        "cooldown_hours": 12,
        "message": (
            "A fix was recently applied for an issue you reported. "
            "Is everything looking right now? If not, just say so \u2014 "
            "I\u2019ll escalate it."
        ),
    },
    {
        "name": "low_activity",
        "cooldown_hours": 48,
        "message": (
            "Checking in \u2014 I noticed your store hasn\u2019t received much "
            "visitor data recently. If you\u2019re seeing issues with tracking "
            "or something feels stuck, let me know and I\u2019ll take a look."
        ),
    },
    {
        "name": "post_feature_ack",
        "cooldown_hours": 72,
        "message": (
            "Thanks again for your feature suggestion earlier. "
            "The team is reviewing it. If you think of anything else \u2014 "
            "bugs, ideas, friction \u2014 keep sending it here."
        ),
    },
    {
        "name": "first_purchase",
        "cooldown_hours": 168,  # once per week max
        "message": None,  # dynamic — built by trigger check
    },
    {
        "name": "revenue_anomaly",
        "cooldown_hours": 48,
        "message": None,  # dynamic — built by trigger check
    },
    {
        "name": "conversion_opportunity",
        "cooldown_hours": 72,
        "message": None,  # dynamic — built by trigger check
    },
    {
        "name": "weekly_insight",
        "cooldown_hours": 168,  # once per week
        "message": None,  # dynamic — built by insight engine
    },
]


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------

def _check_post_connect(db: Session, shop_domain: str) -> bool:
    """Trigger: merchant recently connected (has merchant record, onboarding done)."""
    try:
        from app.models.merchant import Merchant
        merchant = (
            db.query(Merchant)
            .filter(Merchant.shop_domain == shop_domain)
            .first()
        )
        if not merchant:
            return False
        # Trigger if merchant connected within last 48 hours
        if merchant.created_at and (_now() - merchant.created_at) < timedelta(hours=48):
            return True
        return False
    except Exception:
        return False


def _check_post_fix(db: Session, shop_domain: str) -> bool:
    """Trigger: a fix was recently delivered (resolution_delivered_at within 24h)."""
    try:
        from app.models.support_incident import SupportIncident
        recent_fix = (
            db.query(SupportIncident)
            .filter(
                SupportIncident.shop_domain == shop_domain,
                SupportIncident.status == "resolved",
                SupportIncident.resolution_verified == True,
                SupportIncident.resolution_delivered_at.isnot(None),
                SupportIncident.resolution_delivered_at >= _now() - timedelta(hours=24),
            )
            .first()
        )
        return recent_fix is not None
    except Exception:
        return False


def _check_low_activity(db: Session, shop_domain: str) -> bool:
    """Trigger: merchant connected but very low event volume."""
    try:
        from app.models.store_metrics import StoreMetrics
        metrics = (
            db.query(StoreMetrics)
            .filter(StoreMetrics.shop_domain == shop_domain)
            .first()
        )
        if not metrics:
            return True  # no metrics at all = likely stuck
        # Low activity: fewer than 5 visitors in last 7 days
        total_visitors = (metrics.new_visitors_7d or 0) + (metrics.returning_visitors_7d or 0)
        return total_visitors < 5
    except Exception:
        return False


def _check_post_feature_ack(db: Session, shop_domain: str) -> bool:
    """Trigger: merchant submitted a feature request recently."""
    try:
        from app.models.support_incident import SupportIncident
        recent_feature = (
            db.query(SupportIncident)
            .filter(
                SupportIncident.shop_domain == shop_domain,
                SupportIncident.classification == "feature_request",
                SupportIncident.created_at >= _now() - timedelta(hours=72),
            )
            .first()
        )
        return recent_feature is not None
    except Exception:
        return False


def _check_first_purchase(db: Session, shop_domain: str) -> bool | str:
    """Trigger: first purchase tracked. Returns message or False."""
    try:
        from sqlalchemy import text as sql_text
        row = db.execute(sql_text(
            "SELECT COUNT(*) FROM shop_orders WHERE shop_domain = :shop"
        ), {"shop": shop_domain}).fetchone()
        order_count = row[0] if row else 0

        if 1 <= order_count <= 3:
            return (
                f"Your first {'purchase has' if order_count == 1 else f'{order_count} purchases have'} "
                f"been tracked through HedgeSpark. Revenue attribution is now active \u2014 "
                f"you can see which traffic sources drive real purchases in the Revenue section."
            )
        return False
    except Exception:
        return False


def _check_revenue_anomaly(db: Session, shop_domain: str) -> bool | str:
    """Trigger: revenue dropped >30% week-over-week. Returns message or False."""
    try:
        from sqlalchemy import text as sql_text
        now = _now()
        this_week_start = now - timedelta(days=7)
        last_week_start = now - timedelta(days=14)

        row = db.execute(sql_text("""
            SELECT
                COALESCE(SUM(CASE WHEN created_at >= :this_week THEN total_price ELSE 0 END), 0) AS rev_this,
                COALESCE(SUM(CASE WHEN created_at >= :last_week AND created_at < :this_week THEN total_price ELSE 0 END), 0) AS rev_last
            FROM shop_orders
            WHERE shop_domain = :shop AND created_at >= :last_week
        """), {"shop": shop_domain, "this_week": this_week_start, "last_week": last_week_start}).fetchone()

        if not row:
            return False

        rev_this = float(row[0])
        rev_last = float(row[1])

        if rev_last < 50:
            return False  # too little baseline to detect anomaly

        drop_pct = (rev_last - rev_this) / rev_last * 100
        if drop_pct >= 30:
            return (
                f"I noticed your store\u2019s revenue dropped about {drop_pct:.0f}% this week "
                f"compared to last week. If you want, I can look at which products or "
                f"traffic sources changed. Just say the word."
            )
        return False
    except Exception:
        return False


def _check_conversion_opportunity(db: Session, shop_domain: str) -> bool | str:
    """Trigger: product with high views but zero conversions. Returns message or False."""
    try:
        from app.models.product_metrics import ProductMetrics
        # Find a product with significant views but no carts/purchases
        product = (
            db.query(ProductMetrics.product_url, ProductMetrics.views_7d,
                     ProductMetrics.cart_conversions_7d, ProductMetrics.purchases_7d)
            .filter(
                ProductMetrics.shop_domain == shop_domain,
                ProductMetrics.views_7d >= 10,  # meaningful traffic
                ProductMetrics.cart_conversions_7d == 0,  # zero carts
            )
            .order_by(ProductMetrics.views_7d.desc())
            .first()
        )
        if not product:
            return False

        # Extract product name from URL
        from app.services.store_context import _extract_product_name
        name = _extract_product_name(product.product_url)
        views = product.views_7d

        return (
            f"{name} is getting attention ({views} views this week) but hasn\u2019t "
            f"converted to any carts yet. A nudge or pricing adjustment might help. "
            f"Want me to explain the options?"
        )
    except Exception:
        return False


def _check_weekly_insight(db: Session, shop_domain: str) -> bool | str:
    """Trigger: weekly performance insight from the insight engine."""
    try:
        from app.services.store_insight_engine import generate_store_insight
        insight = generate_store_insight(db, shop_domain)
        if not insight:
            return False

        # Format as a proactive check-in
        return (
            f"Weekly check-in for your store:\n\n"
            f"{insight.headline}\n\n"
            f"{insight.explanation}\n\n"
            f"{insight.action}"
        )
    except Exception:
        return False


_TRIGGER_CHECKS = {
    "post_connect": _check_post_connect,
    "post_fix": _check_post_fix,
    "low_activity": _check_low_activity,
    "post_feature_ack": _check_post_feature_ack,
    "first_purchase": _check_first_purchase,
    "revenue_anomaly": _check_revenue_anomaly,
    "conversion_opportunity": _check_conversion_opportunity,
    "weekly_insight": _check_weekly_insight,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def precompute_proactive_messages(db: Session, shop_domain: str) -> list[dict]:
    """
    Pre-compute proactive messages for a shop. Called by aggregation worker.
    Results stored in Redis for fast retrieval during polling.

    This is the HEAVY path — runs all trigger checks. Called once per
    aggregation cycle (5 min), not per frontend poll (30s).
    """
    results = _evaluate_triggers(db, shop_domain)
    if results:
        rc = _redis()
        if rc:
            import json
            cache_key = f"hs:proactive_cache:{shop_domain}"
            rc.setex(cache_key, 600, json.dumps(results))  # 10-min cache
    return results


def get_pending_proactive_messages(
    db: Session,
    shop_domain: str,
) -> list[dict]:
    """
    Return pending proactive messages for a shop.

    Fast path: reads from Redis cache (pre-computed by worker).
    Slow fallback: evaluates triggers directly (for shops not yet in cache).
    """
    # Try cached results first (fast — no DB queries)
    rc = _redis()
    if rc:
        import json
        cache_key = f"hs:proactive_cache:{shop_domain}"
        cached = rc.get(cache_key)
        if cached:
            try:
                messages = json.loads(cached)
                # Filter out already-acked messages
                return [m for m in messages if not _is_acked(shop_domain, m["id"])]
            except (json.JSONDecodeError, ValueError):
                pass

    # Fallback: evaluate triggers directly (for cache miss)
    return _evaluate_triggers(db, shop_domain)


def _evaluate_triggers(db: Session, shop_domain: str) -> list[dict]:
    """Core trigger evaluation logic — shared by precompute and fallback."""
    results: list[dict] = []

    # Quick check: shop must exist as a merchant
    try:
        from app.models.merchant import Merchant
        merchant_exists = db.query(Merchant.id).filter(Merchant.shop_domain == shop_domain).first() is not None
        if not merchant_exists:
            return []
    except Exception:
        return []

    for trigger in _TRIGGERS:
        name = trigger["name"]
        cooldown = trigger["cooldown_hours"]

        # Skip if on cooldown
        if _is_on_cooldown(shop_domain, name, cooldown):
            continue

        # Evaluate trigger condition
        check_fn = _TRIGGER_CHECKS.get(name)
        if not check_fn:
            continue

        try:
            check_result = check_fn(db, shop_domain)
        except Exception:
            continue

        if not check_result:
            continue

        # Dynamic triggers return a string message; static triggers return True
        if isinstance(check_result, str):
            msg_text = check_result
        else:
            msg_text = trigger["message"]
            if msg_text is None:
                continue  # dynamic trigger returned True but has no static message

        # Generate deterministic message ID
        msg_id = _message_id(shop_domain, name)

        # Skip if already acked
        if _is_acked(shop_domain, msg_id):
            continue

        # Set cooldown immediately (prevents re-firing next poll)
        _set_cooldown(shop_domain, name, cooldown)

        results.append({
            "id": msg_id,
            "message": msg_text,
            "created_at": _now().isoformat() + "Z",
        })

    return results


def ack_proactive_message(
    db: Session,
    shop_domain: str,
    message_id: str,
) -> None:
    """Mark a proactive message as delivered. Prevents re-delivery."""
    rc = _redis()
    if rc:
        # Keep ack for 30 days
        rc.setex(_ack_key(shop_domain, message_id), 30 * 86400, "1")
