import time
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text
from app.services.price_intelligence_engine import update_price_intelligence
from app.models.visitor_product_state import VisitorProductState
from app.models.product_opportunity import ProductOpportunity
from app.models.opportunity_signal import OpportunitySignal
from app.core.database import engine as _db_engine, SessionLocal


def classify_opportunity(avg_intent_score, hot_count, wishlist_count, avg_dwell, avg_scroll):
    opportunity_type = "NO_ACTION"
    recommended_action = "NONE"
    explanation = "No strong product opportunity detected"
    priority_score = 0

    if avg_intent_score >= 80 and wishlist_count >= 1:
        opportunity_type = "PRICE_DROP_OR_LOW_STOCK_NUDGE"
        recommended_action = "PRICE_DROP_ALERT"
        explanation = "High intent product with strong commitment signals"
        priority_score = 90

    elif avg_intent_score >= 60 and wishlist_count == 0:
        opportunity_type = "WISHLIST_PROMPT_TEST"
        recommended_action = "PROMINENT_WISHLIST_CTA"
        explanation = "High interest but low commitment; test stronger wishlist CTA"
        priority_score = 75

    elif avg_dwell >= 20 and avg_scroll >= 70 and wishlist_count == 0:
        opportunity_type = "FRICTION_OR_PRICE_SENSITIVITY"
        recommended_action = "REVIEW_PRICE_TRUST_CTA"
        explanation = "Users explore deeply but do not commit; review offer, price, trust, or CTA"
        priority_score = 70

    elif hot_count >= 2:
        opportunity_type = "HIGH_INTEREST_PRODUCT"
        recommended_action = "MONITOR_AND_PROMOTE"
        explanation = "Multiple HOT visitor-product states detected"
        priority_score = 65

    return opportunity_type, recommended_action, explanation, priority_score


def update_product_opportunity(db: Session, product_url: str):
    if not product_url:
        return

    row = (
        db.query(
            VisitorProductState.product_url,
            func.count(VisitorProductState.id).label("records"),
            func.avg(VisitorProductState.intent_score).label("avg_intent_score"),
            func.sum(
                case((VisitorProductState.intent_level == "HOT", 1), else_=0)
            ).label("hot_count"),
            func.sum(
                case((VisitorProductState.wishlist_added == True, 1), else_=0)
            ).label("wishlist_count"),
            func.avg(VisitorProductState.total_dwell_seconds).label("avg_dwell"),
            func.avg(VisitorProductState.max_scroll_depth).label("avg_scroll")
        )
        .filter(VisitorProductState.product_url == product_url)
        .group_by(VisitorProductState.product_url)
        .first()
    )

    if not row:
        return

    records = int(row.records or 0)
    avg_intent_score = float(row.avg_intent_score or 0)
    hot_count = int(row.hot_count or 0)
    wishlist_count = int(row.wishlist_count or 0)
    avg_dwell = float(row.avg_dwell or 0)
    avg_scroll = float(row.avg_scroll or 0)

    opportunity_type, recommended_action, explanation, priority_score = classify_opportunity(
        avg_intent_score=avg_intent_score,
        hot_count=hot_count,
        wishlist_count=wishlist_count,
        avg_dwell=avg_dwell,
        avg_scroll=avg_scroll
    )

    existing = (
        db.query(ProductOpportunity)
        .filter(ProductOpportunity.product_url == product_url)
        .first()
    )

    if not existing:
        existing = ProductOpportunity(product_url=product_url)
        db.add(existing)
        db.flush()

    existing.records = records
    existing.avg_intent_score = avg_intent_score
    existing.hot_count = hot_count
    existing.wishlist_count = wishlist_count
    existing.avg_dwell_seconds = avg_dwell
    existing.avg_scroll_depth = avg_scroll

    existing.opportunity_type = opportunity_type
    existing.priority_score = priority_score
    existing.recommended_action = recommended_action
    existing.opportunity_explanation = explanation
    existing.plan_required = "pro"
    existing.updated_at = datetime.utcnow()

    db.commit()
    update_price_intelligence(db, product_url)


# ---------------------------------------------------------------------------
# Rule-based opportunity detection engine
# Queries the events table directly — no AI, no external calls.
# All queries are bounded to a rolling time window and isolated by shop_domain.
# ---------------------------------------------------------------------------

def _conversion_metrics(conn, shop_domain: str, cutoff_ms: int) -> list[dict]:
    """
    Per product URL: total views, unique visitors, and cart-conversion count.

    A cart conversion is a visitor who also reached a cart or checkout URL
    (or triggered an add_to_cart event) within the same time window.
    Only product URLs (containing /products/) are included.
    Minimum 10 views to appear in results.
    """
    result = conn.execute(
        text("""
            WITH product_views AS (
                SELECT url AS product_url, visitor_id
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp >= :cutoff_ms
                  AND url LIKE '%/products/%'
                  AND event_type IN ('page_view', 'product_view')
            ),
            cart_visitors AS (
                SELECT DISTINCT visitor_id
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp >= :cutoff_ms
                  AND (
                      url LIKE '%/cart%'
                   OR url LIKE '%/checkout%'
                   OR event_type = 'add_to_cart'
                  )
            )
            SELECT
                pv.product_url,
                COUNT(*)                                                   AS total_views,
                COUNT(DISTINCT pv.visitor_id)                              AS unique_visitors,
                COUNT(DISTINCT CASE
                    WHEN cv.visitor_id IS NOT NULL THEN pv.visitor_id
                END)                                                       AS cart_conversions
            FROM product_views pv
            LEFT JOIN cart_visitors cv ON cv.visitor_id = pv.visitor_id
            GROUP BY pv.product_url
            HAVING COUNT(*) >= 10
            ORDER BY COUNT(*) DESC
            LIMIT 100
        """),
        {"shop_domain": shop_domain, "cutoff_ms": cutoff_ms},
    )
    return [dict(r._mapping) for r in result.fetchall()]


def _return_visitor_counts(conn, shop_domain: str, cutoff_7d_ms: int) -> dict[str, int]:
    """
    Return a mapping of product_url → count of visitors who viewed it on
    more than one distinct calendar day within the last 7 days.
    """
    result = conn.execute(
        text("""
            WITH product_daily AS (
                SELECT
                    url            AS product_url,
                    visitor_id,
                    COUNT(DISTINCT DATE(TO_TIMESTAMP(timestamp / 1000.0))) AS days_seen
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp >= :cutoff_7d_ms
                  AND url LIKE '%/products/%'
                  AND event_type IN ('page_view', 'product_view')
                GROUP BY url, visitor_id
            )
            SELECT
                product_url,
                COUNT(DISTINCT visitor_id) AS return_visitors
            FROM product_daily
            WHERE days_seen > 1
            GROUP BY product_url
            HAVING COUNT(DISTINCT visitor_id) > 0
            ORDER BY return_visitors DESC
            LIMIT 100
        """),
        {"shop_domain": shop_domain, "cutoff_7d_ms": cutoff_7d_ms},
    )
    return {r._mapping["product_url"]: int(r._mapping["return_visitors"]) for r in result.fetchall()}


def _traffic_spikes(conn, shop_domain: str, cutoff_ms: int) -> list[dict]:
    """
    Products where views in the current hour exceed 2× the hourly average
    over the preceding 24-hour window.
    Requires at least 2 prior data hours to avoid false positives on
    products with no history.
    """
    result = conn.execute(
        text("""
            WITH hourly AS (
                SELECT
                    url AS product_url,
                    DATE_TRUNC('hour', TO_TIMESTAMP(timestamp / 1000.0)) AS hour_bucket,
                    COUNT(*) AS views
                FROM events
                WHERE shop_domain = :shop_domain
                  AND timestamp >= :cutoff_ms
                  AND url LIKE '%/products/%'
                  AND event_type IN ('page_view', 'product_view')
                GROUP BY url, DATE_TRUNC('hour', TO_TIMESTAMP(timestamp / 1000.0))
            ),
            product_hourly AS (
                SELECT
                    product_url,
                    AVG(views)                                                    AS avg_hourly_views,
                    MAX(CASE
                        WHEN hour_bucket >= DATE_TRUNC('hour', NOW())
                        THEN views ELSE 0
                    END)                                                          AS current_hour_views,
                    COUNT(DISTINCT hour_bucket)                                   AS hours_with_data
                FROM hourly
                GROUP BY product_url
            )
            SELECT
                product_url,
                ROUND(avg_hourly_views::numeric, 2)                AS avg_hourly_views,
                current_hour_views,
                ROUND(
                    current_hour_views::numeric / NULLIF(avg_hourly_views, 0),
                    2
                )                                                  AS spike_ratio
            FROM product_hourly
            WHERE current_hour_views > 2 * avg_hourly_views
              AND avg_hourly_views > 0
              AND hours_with_data >= 2
            ORDER BY spike_ratio DESC
            LIMIT 20
        """),
        {"shop_domain": shop_domain, "cutoff_ms": cutoff_ms},
    )
    return [dict(r._mapping) for r in result.fetchall()]


def detect_opportunities(shop_domain: str) -> list[dict]:
    """
    Run all 4 rule-based opportunity detectors against behavioral event data.

    Rules applied:
      1. HIGH_TRAFFIC_NO_CART      — many views, zero cart/checkout events
      2. LOW_CONVERSION_ATTENTION  — many views, conversion rate < 2 %
      3. RETURN_VISITOR_INTEREST   — > 5 visitors viewed same product on 2+ days
      4. TRAFFIC_SPIKE             — current-hour views > 2× rolling hourly average

    Returns a list of signal dicts ready for the /opportunities endpoint.
    """
    now = datetime.utcnow()
    cutoff_24h_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
    cutoff_7d_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    detected_at = now.isoformat()

    signals: list[dict] = []

    with _db_engine.begin() as conn:
        metrics = _conversion_metrics(conn, shop_domain, cutoff_24h_ms)
        return_map = _return_visitor_counts(conn, shop_domain, cutoff_7d_ms)
        spikes = _traffic_spikes(conn, shop_domain, cutoff_24h_ms)

    # ------------------------------------------------------------------ #
    # Rules 1 & 2 — conversion gap signals                               #
    # ------------------------------------------------------------------ #
    for row in metrics:
        product_url = row["product_url"]
        views = int(row["total_views"])
        cart = int(row["cart_conversions"])
        unique = int(row["unique_visitors"])

        conversion_rate = cart / views if views > 0 else 0.0

        if views >= 30 and cart == 0:
            # Rule 1: traffic with no cart signal at all
            strength = round(min(1.0, (views - 30) / 70 + 0.5), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "HIGH_TRAFFIC_NO_CART",
                "signal_strength": strength,
                "explanation": (
                    f"{views} views from {unique} visitors in 24 h "
                    "but no cart or checkout activity detected."
                ),
                "detected_at": detected_at,
            })

        elif views >= 40 and 0 < conversion_rate < 0.02:
            # Rule 2: non-zero but critically low conversion rate
            strength = round(max(0.3, 1.0 - (conversion_rate / 0.02)), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "LOW_CONVERSION_ATTENTION",
                "signal_strength": strength,
                "explanation": (
                    f"{views} views but only {cart} cart event(s) — "
                    f"conversion rate {conversion_rate:.1%} is below 2 %."
                ),
                "detected_at": detected_at,
            })

    # ------------------------------------------------------------------ #
    # Rule 3 — return visitor interest                                    #
    # ------------------------------------------------------------------ #
    for product_url, return_count in return_map.items():
        if return_count > 5:
            strength = round(min(1.0, return_count / 20), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "RETURN_VISITOR_INTEREST",
                "signal_strength": strength,
                "explanation": (
                    f"{return_count} visitors returned to this product "
                    "on multiple days — strong sustained interest."
                ),
                "detected_at": detected_at,
            })

    # ------------------------------------------------------------------ #
    # Rule 4 — traffic spike                                              #
    # ------------------------------------------------------------------ #
    for row in spikes:
        spike_ratio = float(row["spike_ratio"])
        current = int(row["current_hour_views"])
        avg = float(row["avg_hourly_views"])
        strength = round(min(1.0, spike_ratio / 5.0), 2)
        signals.append({
            "product_url": row["product_url"],
            "signal_type": "TRAFFIC_SPIKE",
            "signal_strength": strength,
            "explanation": (
                f"{current} views this hour vs {avg:.1f} hourly average "
                f"({spike_ratio:.1f}× spike detected)."
            ),
            "detected_at": detected_at,
        })

    # Sort by signal_strength descending so strongest signals appear first
    signals.sort(key=lambda s: s["signal_strength"], reverse=True)
    return signals


# ---------------------------------------------------------------------------
# Persistence layer
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = 300    # 5 minutes — in-process cache lifetime
_STALE_HOURS = 1            # DB rows not refreshed within 1 h are deleted

# In-process signal cache: shop_domain → (expires_monotonic, [signal_dict])
_signal_cache: dict[str, tuple[float, list[dict]]] = {}


def _persist_signals(signals: list[dict], shop_domain: str) -> None:
    """
    Upsert every signal in the current detection batch, then delete
    any rows for this shop that were not refreshed (i.e. their condition
    no longer holds and they have aged past _STALE_HOURS).

    Errors are swallowed so a DB hiccup never breaks the API response.
    """
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        for signal in signals:
            existing = (
                db.query(OpportunitySignal)
                .filter(
                    OpportunitySignal.shop_domain == shop_domain,
                    OpportunitySignal.product_url == signal["product_url"],
                    OpportunitySignal.signal_type == signal["signal_type"],
                )
                .first()
            )
            if existing is None:
                db.add(
                    OpportunitySignal(
                        shop_domain=shop_domain,
                        product_url=signal["product_url"],
                        signal_type=signal["signal_type"],
                        signal_strength=signal["signal_strength"],
                        explanation=signal["explanation"],
                        detected_at=now,
                        refreshed_at=now,
                    )
                )
            else:
                existing.signal_strength = signal["signal_strength"]
                existing.explanation = signal["explanation"]
                existing.detected_at = now
                existing.refreshed_at = now

        # Clean up signals whose conditions no longer hold
        stale_cutoff = now - timedelta(hours=_STALE_HOURS)
        db.query(OpportunitySignal).filter(
            OpportunitySignal.shop_domain == shop_domain,
            OpportunitySignal.refreshed_at < stale_cutoff,
        ).delete(synchronize_session=False)

        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _read_fresh_signals_from_db(shop_domain: str) -> list[dict]:
    """
    Read signals refreshed within the last _CACHE_TTL_SECONDS seconds.
    Returns an empty list if no fresh rows exist.
    """
    fresh_cutoff = datetime.utcnow() - timedelta(seconds=_CACHE_TTL_SECONDS)
    db = SessionLocal()
    try:
        rows = (
            db.query(OpportunitySignal)
            .filter(
                OpportunitySignal.shop_domain == shop_domain,
                OpportunitySignal.refreshed_at >= fresh_cutoff,
            )
            .order_by(OpportunitySignal.signal_strength.desc())
            .all()
        )
        return [
            {
                "product_url": r.product_url,
                "signal_type": r.signal_type,
                "signal_strength": r.signal_strength,
                "explanation": r.explanation,
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public entry point used by the API
# ---------------------------------------------------------------------------

def get_or_refresh_signals(shop_domain: str) -> list[dict]:
    """
    Serve opportunity signals with a two-level cache:

      1. In-process cache (fastest) — valid for _CACHE_TTL_SECONDS.
      2. DB read                    — catches the first request after a
                                     process restart if signals are still
                                     fresh in the database.
      3. Full detection + persist   — runs when both caches are cold.

    All paths are scoped to shop_domain.
    """
    now = time.monotonic()

    # Level 1 — in-process cache
    entry = _signal_cache.get(shop_domain)
    if entry is not None:
        expires_at, cached = entry
        if now < expires_at:
            return cached

    # Level 2 — DB (survives restarts; avoids recompute if recently run)
    db_signals = _read_fresh_signals_from_db(shop_domain)
    if db_signals:
        _signal_cache[shop_domain] = (now + _CACHE_TTL_SECONDS, db_signals)
        return db_signals

    # Level 3 — full detection run
    signals = detect_opportunities(shop_domain)
    _persist_signals(signals, shop_domain)
    _signal_cache[shop_domain] = (now + _CACHE_TTL_SECONDS, signals)
    return signals
