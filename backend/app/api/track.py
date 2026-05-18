"""
POST /track — storefront event ingestion endpoint for HedgeSpark.

Receives events from spark-tracker.js, upserts a Visitor row, then persists
an Event row with all fields stored in their dedicated columns.

Column mapping (events table schema):
  payload.page_url    → Event.url          (raw page URL, always present)
  payload.product_url → Event.product_url  (canonical product path, NULL on non-product pages)
  payload.timestamp   → Event.timestamp    (epoch ms, bigint)
  payload.dwell_seconds   → Event.dwell_seconds
  payload.scroll_depth    → Event.max_scroll_depth
  payload.shop_domain     → Event.shop_domain
  payload.visitor_id      → Event.visitor_id
  payload.event_type      → Event.event_type
  payload.source_type     → Event.source_type  (direct | google | facebook | …)
  payload.referrer        → Event.referrer     (raw document.referrer)

Design note — url vs product_url
---------------------------------
url       = raw page URL for every event (what page the visitor was on).
product_url = the canonical product path when the event fired on a product page;
              NULL for non-product pages (home, collection, checkout, etc.).
              Canonical format: /products/{handle}

Server-side normalization (defensive layer)
-------------------------------------------
Even though spark-tracker.js now sends path-only product_url values, we
normalize server-side as a safety net for:
  - old tracker versions still in browser caches
  - third-party integrations that send full URLs
  - manual API calls during development

normalize_product_url() extracts /products/{handle} from any input and
returns None for non-product values, so garbage never reaches the DB.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_lazy_db
from app.core.url_utils import normalize_product_url
from app.models.event import Event
from app.models.merchant import Merchant
from app.models.shop_order import ShopOrder
from app.models.visitor import Visitor
from app.models.visitor_purchase_session import VisitorPurchaseSession
from app.services.shopify_auth import is_valid_shop_domain

import logging

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# CORS for storefront & pixel requests
#
# /track and /track/batch are called cross-origin from:
#   - spark-tracker.js on *.myshopify.com storefronts
#   - spark-pixel.js in Shopify's Custom Pixel sandbox (unpredictable origin)
#
# The main CORSMiddleware only allows app.hedgesparkhq.com (dashboard).
# These routes need Access-Control-Allow-Origin: * so cross-origin fetch
# with *: application/json passes the browser preflight check.
#
# Safe because: no cookies/credentials are used (tracker sends credentials: "omit"),
# the payload is validated (known shop, rate-limited, schema-checked), and
# the response contains no sensitive data.
# ---------------------------------------------------------------------------
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Max-Age": "86400",
}


@router.options("/track")
@router.options("/track/batch")
async def track_cors_preflight():
    """Handle CORS preflight for storefront and pixel callers."""
    return Response(status_code=204, headers=_CORS_HEADERS)


# Strict allowlist of event types accepted from the storefront tracker.
# Any value not in this set is rejected with HTTP 400.
# To add a new event type: update this set AND the corresponding tracker script.
_ALLOWED_EVENT_TYPES: frozenset[str] = frozenset({
    "page_view",
    "product_view",
    "dwell_time",
    "scroll",
    "add_to_cart",
    "click",
    "mousemove",
    "page_leave",
    "wishlist_add",
    "purchase",
    "begin_checkout",
    "view_cart",
    # UX frustration signals (tracker-emitted). `rage_click` = 3+ rapid
    # clicks on same element; `pogo_stick` = back-navigation within
    # 3s of page load. Both are volume-sampled — the tracker self-limits
    # to at most 1 per type per page so we never drown in events.
    "rage_click",
    "pogo_stick",
})




class TrackPayload(BaseModel):
    # Tier 2.3 — upper bounds on every untrusted string. The public
    # tracker endpoint is the widest unauthenticated surface in the
    # product; a single compromised storefront sending multi-MB strings
    # would hit the DB, the logs, the audit trail, and the LLM prompt.
    # Bounds are generous enough to never reject real traffic.
    shop_domain: str = Field(..., max_length=255)
    visitor_id: str = Field(..., max_length=128)
    event_type: str = Field(..., max_length=64)
    page_url: Optional[str] = Field(None, max_length=2048)
    product_url: Optional[str] = Field(None, max_length=2048)
    timestamp: Optional[int] = Field(None, ge=0, le=9999999999999)  # epoch ms, max ~2286 CE
    dwell_seconds: Optional[int] = Field(None, ge=0, le=86400)    # max 24h
    scroll_depth: Optional[int] = Field(None, ge=0, le=100)       # percentage

    # Spatial heatmap coordinates as % of viewport. Sent on click +
    # mousemove events from spark-tracker.js v16+. Stored ONLY in
    # Redis buckets (no schema migration) — see _bump_heatmap_bucket
    # below.
    x_pct: Optional[float] = Field(None, ge=0, le=100)
    y_pct: Optional[float] = Field(None, ge=0, le=100)

    # Source attribution — sent by spark-tracker.js since migration j7e0a4b8c3d6.
    source_type: Optional[str] = Field(None, max_length=64)
    referrer: Optional[str] = Field(None, max_length=2048)
    utm_medium: Optional[str] = Field(None, max_length=128)

    # Full UTM parameters — captured from URL query string by tracker.
    utm_source: Optional[str] = Field(None, max_length=128)
    utm_campaign: Optional[str] = Field(None, max_length=255)
    utm_content: Optional[str] = Field(None, max_length=255)
    utm_term: Optional[str] = Field(None, max_length=255)

    # Click ID — ad platform identifiers. Stored as "type:value".
    # Tracker sends whichever is present: gclid, fbclid, ttclid, msclkid.
    click_id: Optional[str] = Field(None, max_length=256)

    # Landing page — first page URL of the visit (set by tracker on first page_view).
    landing_page: Optional[str] = Field(None, max_length=2048)

    # Device type — "mobile" or "desktop", sent by tracker since v3.
    device_type: Optional[str] = Field(None, max_length=32)

    # Shopify numeric product ID — sent on product pages since migration o1a2b3c4d5e6.
    # Sourced from window.ShopifyAnalytics.meta.product.id by spark-tracker.js.
    # Used to resolve product_url at order ingestion time for real conversion metrics.
    product_id: Optional[str] = Field(None, max_length=64)

    # Purchase fields — sent by spark-tracker.js on the Shopify thank-you page.
    # Replaces Shopify webhooks (orders/*) which require Protected Customer Data approval.
    order_id: Optional[str] = Field(None, max_length=64)
    order_total: Optional[float] = None  # total_price as float
    currency: Optional[str] = Field(None, max_length=16)
    # Class D enrichment (2026-04-26 — populated by spark-pixel.js v14+).
    # All optional — old pixel versions still post without these and the
    # order persists fine (columns NULL, base analytics still work).
    discount_amount: Optional[float] = None
    discount_codes: Optional[list[str]] = None
    tax_amount: Optional[float] = None
    payment_method: Optional[str] = Field(None, max_length=64)
    financial_status: Optional[str] = Field(None, max_length=32)
    fulfillment_status: Optional[str] = Field(None, max_length=32)
    # v15 (2026-04-26): line_items with variant info — closes the
    # "Variants performance" audit gap. Cap at 50 items/order on
    # the pixel side; we re-cap server-side for defense in depth.
    line_items: Optional[list[dict]] = None

    # Shopify _shopify_y cookie value — Shopify's persistent visitor ID.
    # Sent by spark-tracker.js from the storefront. Also available as event.clientId
    # in the Custom Pixel sandbox. Enables identity bridging when the pixel can't
    # read our _hs_vid cookie or localStorage.
    shopify_y: Optional[str] = Field(None, max_length=256)

    # Identity bridge — sent by the pixel when it reads the _hs_vid cookie.
    # This is the storefront tracker's visitor_id, bridging browsing → purchase.
    tracker_visitor_id: Optional[str] = Field(None, max_length=128)

    # Per-merchant pixel secret — validated on purchase events to prevent spoofing.
    pixel_secret: Optional[str] = Field(None, max_length=256)

    # GDPR consent gating (Art. 6 lawful basis, Art. 7 consent).
    # The storefront script SHOULD pass `gdpr_consent_given=True` after the
    # visitor has accepted the shop's consent banner. The field is optional
    # for backwards compatibility with the current tracker build; once the
    # tracker ships with consent support, this default will tighten to
    # "must be explicitly set" for EU storefronts.
    #
    #   True  → event is ingested as usual.
    #   False → event is SILENTLY DROPPED (204 so scanners can't infer
    #           the gate exists, but no data is persisted).
    #   None  → legacy path — currently allowed (see `_CONSENT_STRICT`).
    gdpr_consent_given: Optional[bool] = None
    # Two-letter country hint from the tracker (e.g. "IT", "DE"). Used to
    # scope the strict-consent gate to EU/EEA visitors only — shops outside
    # the EU don't need explicit consent.
    consent_region: Optional[str] = Field(None, max_length=8)


# ---------------------------------------------------------------------------
# GDPR consent gating
# ---------------------------------------------------------------------------

# EU + EEA country codes. When `consent_region` matches any of these AND
# `gdpr_consent_given is False`, the event is dropped. When
# `gdpr_consent_given is None` (legacy tracker), we fall back to the
# global toggle `_CONSENT_STRICT`.
_EU_EEA_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "IS", "LI", "NO",  # EEA
})


def _consent_allows_ingestion(payload: "TrackPayload", request=None) -> bool:
    """Return True when the event may be persisted under the shop's
    lawful basis.

    Decision tree:
      1. Explicit `gdpr_consent_given=True` → allow.
      2. Explicit `gdpr_consent_given=False` → deny.
      3. Browser-level Global Privacy Control signal (`Sec-GPC: 1`)
         OR legacy `DNT: 1` header → deny. Required for CCPA/CPRA
         compliance in California and honored under the same logic
         worldwide.
      4. Otherwise (legacy tracker without the field) → allow for
         backwards compatibility. Once the tracker ships consent
         support, legacy missing-field traffic can be tightened via
         `TRACK_CONSENT_STRICT=1`.
    """
    if payload.gdpr_consent_given is True:
        return True
    if payload.gdpr_consent_given is False:
        return False

    if request is not None:
        try:
            sec_gpc = request.headers.get("sec-gpc", "").strip()
            dnt = request.headers.get("dnt", "").strip()
            if sec_gpc == "1" or dnt == "1":
                return False
        except Exception as exc:
            log.warning("track: _consent_allows_ingestion failed: %s", exc)

    if os.getenv("TRACK_CONSENT_STRICT", "").strip() == "1":
        return False
    return True


def _bump_heatmap_bucket(
    shop_domain: str,
    url: str,
    event_type: str,
    x_pct: float,
    y_pct: float,
) -> None:
    """Increment 10×10 spatial-heatmap bucket counter for click /
    mousemove events. Redis HASH per (shop, url-md5, event_type),
    field = "{x_bucket}:{y_bucket}" (each 0-9). 30-day TTL.

    Why hashed url: avoid 2KB Redis key per page. Lookup uses the
    same md5 truncation in the spatial-heatmap endpoint.

    Why bucket grid 10×10: 100 cells per page is fine resolution for
    canvas heatmap rendering; coarser (5×5) loses headline buttons,
    finer (20×20) blows up Redis size with no perceptible UX gain.
    """
    if x_pct is None or y_pct is None or not url:
        return
    if event_type not in ("click", "mousemove"):
        return
    try:
        import hashlib as _h
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("track.heatmap_bucket")
            return
        url_h = _h.md5(url.encode("utf-8")).hexdigest()[:16]
        x_bucket = max(0, min(9, int(x_pct // 10)))
        y_bucket = max(0, min(9, int(y_pct // 10)))
        key = f"hs:hmap:{shop_domain}:{url_h}:{event_type}"
        field = f"{x_bucket}:{y_bucket}"
        rc.hincrby(key, field, 1)
        rc.expire(key, 30 * 24 * 3600)
    except Exception as exc:
        log.warning("track: _bump_heatmap_bucket failed: %s", exc)


def _bump_consent_metric(accepted: bool) -> None:
    """Track consent-denied vs consent-accepted counts for the
    compliance synthesizer. Redis-only, 30d retention."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("track.consent_metric")
            return
        from datetime import datetime as _dt, timezone as _tz
        day = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        key = f"hs:consent:{day}:{'accepted' if accepted else 'denied'}"
        rc.incr(key)
        rc.expire(key, 30 * 24 * 3600)
    except Exception as exc:
        log.warning("track: _bump_consent_metric failed: %s", exc)


def _check_per_shop_rate(request, shop_domain: str) -> bool:
    """
    Per-IP + per-shop rate limit: max 60 events per 60 seconds per (IP, shop).

    This catches the scenario where a single IP floods events for one shop
    while staying under the global /track rate limit (which is per-IP only).

    Uses Redis when available; silently allows when Redis is down.
    """
    try:
        from app.core.redis_client import _client
        client = _client()
        if client is None:
            from app.core.silent_fallback import record_silent_return
            record_silent_return("track.per_shop_rate")
            return True
        from app.core.client_ip import extract_client_ip
        ip = extract_client_ip(request)
        key = f"hs:rl:track:{ip}:{shop_domain}"
        count = client.incr(key)
        if count == 1:
            client.expire(key, 60)
        return count <= 60
    except Exception as exc:
        log.warning("track: _check_per_shop_rate failed: %s", exc)
        return True  # fail open


def _is_known_shop(db: Session, shop_domain: str) -> bool:
    """
    Check if shop_domain belongs to a known installed merchant.

    Uses Redis cache (5-min TTL) to avoid DB hit per event.
    This is the primary tracker abuse protection — prevents forged
    events for shops that never installed HedgeSpark.
    """
    from app.core.redis_client import cache_get, cache_set
    cache_key = f"hs:known_shop:{shop_domain}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    exists = (
        db.query(Merchant)
        .filter(Merchant.shop_domain == shop_domain, Merchant.install_status == "active")
        .first()
    ) is not None
    cache_set(cache_key, exists, 300)  # 5 min TTL
    return exists


def _upsert_visitor(db: Session, visitor_id: str, shop_domain: str) -> None:
    """Create a Visitor row if new; otherwise bump last_seen.

    Race-safe: concurrent INSERTs for the same (visitor_id, shop_domain)
    are caught via SAVEPOINT + IntegrityError recovery.  The losing request
    falls through to an UPDATE on the existing row.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    visitor = (
        db.query(Visitor)
        .filter(Visitor.visitor_id == visitor_id, Visitor.shop_domain == shop_domain)
        .first()
    )
    if visitor is not None:
        visitor.last_seen = now
        return

    try:
        nested = db.begin_nested()  # SAVEPOINT
        db.add(Visitor(visitor_id=visitor_id, shop_domain=shop_domain, first_seen=now, last_seen=now))
        db.flush()
    except IntegrityError:
        nested.rollback()
        # Another request won the INSERT race — update the existing row
        visitor = (
            db.query(Visitor)
            .filter(Visitor.visitor_id == visitor_id, Visitor.shop_domain == shop_domain)
            .first()
        )
        if visitor is not None:
            visitor.last_seen = now


def _persist_purchase(db: Session, payload: TrackPayload) -> None:
    """
    Persist a client-side purchase event into shop_orders.

    This replaces Shopify webhooks (orders/*) for MVP — all order topics
    require Protected Customer Data approval which blocks MVP validation.

    Idempotent: duplicate order_id is silently skipped via the existing
    UNIQUE constraint on shopify_order_id.
    """
    if payload.event_type != "purchase":
        return
    if not payload.order_id or not payload.order_total or payload.order_total <= 0:
        return

    # Validate pixel_secret against the merchant's stored secret.
    # Uses Redis cache to avoid redundant DB query (shop was already validated).
    from app.core.redis_client import cache_get, cache_set
    _ps_key = f"hs:pixel_secret:{payload.shop_domain}"
    _cached_ps = cache_get(_ps_key)
    if _cached_ps is None:
        merchant = db.query(Merchant).filter(
            Merchant.shop_domain == payload.shop_domain
        ).first()
        _cached_ps = (merchant.pixel_secret or "") if merchant else ""
        cache_set(_ps_key, _cached_ps, 300)  # 5 min TTL
    if _cached_ps:
        if not payload.pixel_secret or payload.pixel_secret != _cached_ps:
            log.warning(
                "track/purchase: pixel_secret mismatch shop=%s order_id=%s — rejected",
                payload.shop_domain, payload.order_id,
            )
            return

    existing = (
        db.query(ShopOrder)
        .filter(ShopOrder.shopify_order_id == str(payload.order_id))
        .first()
    )
    if existing:
        log.info(
            "track/purchase: duplicate skipped order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
        return

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Class D enrichment fields — only persist when the new pixel sent
    # them. Empty/None stays NULL in DB so base analytics continue to
    # work for orders ingested before pixel v14.
    discount_codes_clean: Optional[list[str]] = None
    if payload.discount_codes:
        discount_codes_clean = [
            str(c)[:64] for c in payload.discount_codes if c
        ][:10]  # cap at 10 codes per order (Shopify max is rare > 5)

    # Sanitize incoming line_items: cap quantity, length, fields.
    # Drop unknown keys to keep the JSONB stable + small.
    sanitized_line_items: list[dict] = []
    if payload.line_items:
        for raw in payload.line_items[:50]:
            if not isinstance(raw, dict):
                continue
            sanitized_line_items.append({
                "variant_id":    str(raw.get("variant_id"))[:64] if raw.get("variant_id") else None,
                "variant_title": str(raw.get("variant_title"))[:200] if raw.get("variant_title") else None,
                "product_title": str(raw.get("product_title"))[:200] if raw.get("product_title") else None,
                "sku":           str(raw.get("sku"))[:80] if raw.get("sku") else None,
                "quantity":      int(raw["quantity"]) if isinstance(raw.get("quantity"), (int, float)) else None,
                "price":         float(raw["price"]) if isinstance(raw.get("price"), (int, float)) else None,
            })

    # Currency fallback: pixel always sends currency, but legacy/buggy
    # pixel builds can drop it. Look up the shop's currency rather than
    # corrupting the ShopOrder row with EUR for non-EUR merchants.
    fallback_ccy = "USD"
    if not payload.currency:
        try:
            from app.services.revenue_metrics import get_shop_currency
            fallback_ccy = (get_shop_currency(db, payload.shop_domain) or "USD").upper()
        except Exception:
            fallback_ccy = "USD"
    order = ShopOrder(
        shop_domain=payload.shop_domain,
        shopify_order_id=str(payload.order_id),
        total_price=payload.order_total,
        currency=(payload.currency or fallback_ccy).upper(),
        customer_id=None,       # not available client-side
        customer_email=None,    # not available client-side
        line_items=sanitized_line_items,
        created_at=now,
        ingested_at=now,
        source="pixel",
        # Class D — populated when pixel v14+ supplies them
        discount_amount=payload.discount_amount,
        discount_codes=discount_codes_clean,
        tax_amount=payload.tax_amount,
        payment_method=(payload.payment_method or None),
        financial_status=(payload.financial_status or "paid"),
        fulfillment_status=(payload.fulfillment_status or "unfulfilled"),
    )
    try:
        nested = db.begin_nested()  # SAVEPOINT — won't kill the outer transaction
        db.add(order)
        db.flush()
        log.info(
            "track/purchase: stored order_id=%s shop=%s total=%.2f %s",
            payload.order_id, payload.shop_domain,
            payload.order_total, order.currency,
        )
    except IntegrityError:
        nested.rollback()
        log.info(
            "track/purchase: duplicate skipped (race) order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
    except Exception as exc:
        nested.rollback()
        log.error(
            "track/purchase: unexpected error order_id=%s shop=%s: %s",
            payload.order_id, payload.shop_domain, exc,
        )

    # --- Identity bridge: link tracker visitor_id → order ---
    # When the pixel reads the _hs_vid cookie (set by spark-tracker.js),
    # it sends tracker_visitor_id.  This is the storefront browsing identity.
    # Writing a VisitorPurchaseSession row creates the join path:
    #   events (tracker visitor_id) → visitor_purchase_sessions → shop_orders
    _persist_visitor_bridge(db, payload)

    # --- Geo aggregate: count this order in the per-shop country hash ---
    # Reuses the visitor_geo Redis cache that capture_visitor_geo_sync
    # populated during the same session's earlier page_view events.
    # Best-effort + auxiliary: if no geo cached, the order still ships,
    # we just don't enrich the orders-by-country map for this shop+date.
    # See app/core/geo.py:record_order_geo for storage shape.
    try:
        from app.core.geo import record_order_geo
        record_order_geo(
            payload.shop_domain,
            payload.visitor_id,
            float(payload.order_total),
            order.currency,
        )
    except Exception as exc:
        log.warning("track/purchase: geo aggregate failed: %s", exc)


def _persist_visitor_bridge(db: Session, payload: TrackPayload) -> None:
    """
    Create a visitor_purchase_sessions row linking the storefront tracker
    identity to the purchase order.  This is the identity bridge.

    Resolves first-touch and last-touch attribution from the visitor's
    event history (same logic as /track/purchase-confirmed endpoint).

    Only fires when tracker_visitor_id is present (pixel read the _hs_vid cookie).
    Idempotent: UNIQUE constraint on shopify_order_id prevents duplicates.
    """
    if not payload.order_id:
        return

    # Resolve the tracker visitor_id — four strategies:
    # 1. Direct: pixel read _hs_vid cookie → tracker_visitor_id is set
    # 2. Identity match: pixel's visitor_id already exists in our events table
    #    (happens when Shopify's event.clientId resolves to our hedgespark ID)
    # 3. Mapping: pixel sent Shopify clientId → look up via shopify_y Redis mapping
    # 4. None: no resolution possible → bridge cannot be created
    bridge_vid = payload.tracker_visitor_id
    if not bridge_vid:
        # Strategy 2: check if payload.visitor_id is a known hedgespark visitor
        if payload.visitor_id:
            try:
                from sqlalchemy import text as _text
                known = db.execute(
                    _text("SELECT 1 FROM events WHERE shop_domain = :shop AND visitor_id = :vid LIMIT 1"),
                    {"shop": payload.shop_domain, "vid": payload.visitor_id},
                ).fetchone()
                if known:
                    bridge_vid = payload.visitor_id
                    log.info("track/bridge: visitor_id %s is known tracker identity — using directly",
                             payload.visitor_id[:12])
            except Exception as exc:
                log.warning("track: _persist_visitor_bridge failed: %s", exc)
    if not bridge_vid:
        # Strategy 3: shopify_y mapping lookup
        bridge_vid = _resolve_visitor_from_shopify_y(payload.shop_domain, payload.visitor_id)
    if not bridge_vid:
        log.info("track/bridge: no visitor resolution for order=%s shop=%s vid=%s",
                 payload.order_id, payload.shop_domain, (payload.visitor_id or "?")[:12])
        return

    import json as _json
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Resolve attribution from visitor's event history
    attr = _resolve_visitor_attribution(db, payload.shop_domain, bridge_vid)

    try:
        nested = db.begin_nested()
        db.add(VisitorPurchaseSession(
            shop_domain=payload.shop_domain,
            visitor_id=bridge_vid,
            shopify_order_id=str(payload.order_id),
            confirmed_at=now,
            ingested_at=now,
            first_source=attr.get("first_source"),
            first_campaign=attr.get("first_campaign"),
            last_source=attr.get("last_source"),
            last_campaign=attr.get("last_campaign"),
            attribution_evidence=_json.dumps(attr.get("evidence", {}), default=str) if attr.get("evidence") else None,
        ))
        db.flush()
        log.info(
            "track/bridge: linked tracker_vid=%s → order_id=%s shop=%s first=%s last=%s",
            bridge_vid, payload.order_id, payload.shop_domain,
            attr.get("first_source"), attr.get("last_source"),
        )
    except IntegrityError:
        nested.rollback()
        log.info(
            "track/bridge: duplicate skipped order_id=%s shop=%s",
            payload.order_id, payload.shop_domain,
        )
    except Exception as exc:
        nested.rollback()
        log.error(
            "track/bridge: unexpected error order_id=%s shop=%s: %s",
            payload.order_id, payload.shop_domain, exc,
        )


def _resolve_visitor_attribution(db: Session, shop_domain: str, visitor_id: str) -> dict:
    """
    Resolve first-touch and last-touch attribution from visitor's event history.
    Reuses the same query logic as track_purchase._resolve_attribution.
    """
    result = {"first_source": None, "first_campaign": None, "last_source": None, "last_campaign": None, "evidence": {}}
    try:
        from sqlalchemy import text as sql_text

        first = db.execute(sql_text("""
            SELECT source_type, utm_campaign, utm_source, referrer, landing_page, click_id, timestamp
            FROM events
            WHERE shop_domain = :shop AND visitor_id = :vid AND source_type IS NOT NULL
            ORDER BY timestamp ASC LIMIT 1
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        last = db.execute(sql_text("""
            SELECT source_type, utm_campaign, utm_source, referrer, click_id, timestamp
            FROM events
            WHERE shop_domain = :shop AND visitor_id = :vid AND source_type IS NOT NULL
            ORDER BY timestamp DESC LIMIT 1
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        stats = db.execute(sql_text("""
            SELECT COUNT(*), ARRAY_AGG(DISTINCT source_type) FILTER (WHERE source_type IS NOT NULL)
            FROM events WHERE shop_domain = :shop AND visitor_id = :vid
        """), {"shop": shop_domain, "vid": visitor_id}).fetchone()

        if first:
            result["first_source"] = first[0]
            result["first_campaign"] = first[1] or first[2]
            result["evidence"]["first_event_ts"] = first[6]
            result["evidence"]["first_referrer"] = first[3]
            result["evidence"]["first_landing_page"] = first[4]
            result["evidence"]["first_click_id"] = first[5]

        if last:
            result["last_source"] = last[0]
            result["last_campaign"] = last[1] or last[2]
            result["evidence"]["last_event_ts"] = last[5]
            result["evidence"]["last_referrer"] = last[3]
            result["evidence"]["last_click_id"] = last[4]

        if stats:
            result["evidence"]["total_events"] = stats[0] or 0
            result["evidence"]["distinct_sources"] = list(stats[1] or [])

    except Exception as exc:
        log.warning("track/bridge: attribution resolution failed %s:%s: %s", shop_domain, visitor_id, exc)

    return result


# ---------------------------------------------------------------------------
# Shopify _shopify_y → hedgespark visitor_id mapping
#
# The storefront tracker (spark-tracker.js) sends both our visitor_id and
# the Shopify _shopify_y cookie value. The Custom Pixel sends event.clientId
# (which equals _shopify_y) but CANNOT read our visitor_id.
#
# This mapping bridges the identity gap: when a pixel purchase arrives with
# only a Shopify clientId, we look up the matching hedgespark visitor_id.
# ---------------------------------------------------------------------------
_SHOPIFY_Y_PREFIX = "hs:symap:"
_SHOPIFY_Y_TTL = 7776000  # 90 days — matches Shopify's _shopify_y cookie lifetime


def _store_shopify_y_mapping(payload: TrackPayload) -> None:
    """Store shopify_y → visitor_id mapping in Redis."""
    if not payload.shopify_y or not payload.visitor_id:
        return
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            key = f"{_SHOPIFY_Y_PREFIX}{payload.shop_domain}:{payload.shopify_y}"
            rc.set(key, payload.visitor_id, ex=_SHOPIFY_Y_TTL)
    except Exception as exc:
        log.warning("track: _store_shopify_y_mapping failed: %s", exc)


def _resolve_visitor_from_shopify_y(shop_domain: str, shopify_client_id: str) -> str | None:
    """Look up hedgespark visitor_id from a Shopify clientId/shopify_y value."""
    if not shopify_client_id:
        return None
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc is not None:
            key = f"{_SHOPIFY_Y_PREFIX}{shop_domain}:{shopify_client_id}"
            val = rc.get(key)
            if val:
                log.info("track/bridge: resolved shopify_y=%s → vid=%s shop=%s",
                         shopify_client_id[:12], val[:12], shop_domain)
                return val
    except Exception as exc:
        log.warning("track: _resolve_visitor_from_shopify_y failed: %s", exc)
    return None


def _event_fields_from_payload(payload: "TrackPayload") -> dict:
    """Single source of the Event field values — shared by /track and
    /track/batch, on BOTH branches: Event(**fields) for the
    synchronous purchase path and enqueue_event(fields) for the async
    analytics path. One place ⟹ a column drift is ONE edit; the
    returned keys MUST stay == ingest_buffer._EVENT_FIELDS (locked by
    test_event_fields_match_buffer_contract). Pure: no DB, no Redis.

    Before 2026-05-18 /track/batch had its OWN inline Event(...) with
    only 12 of the 19 columns — it silently dropped utm_*/click_id/
    landing_page (an attribution-loss drift). Unifying here kills that
    class permanently."""
    canonical_product_url = normalize_product_url(payload.product_url)
    return {
        "shop_domain":    payload.shop_domain,
        "visitor_id":     payload.visitor_id,
        "event_type":     payload.event_type,
        "url":            payload.page_url,
        "product_url":    canonical_product_url,
        "timestamp":      payload.timestamp,
        "dwell_seconds":  payload.dwell_seconds,
        "max_scroll_depth": payload.scroll_depth,
        "source_type":    payload.source_type or None,
        "referrer":       payload.referrer or None,
        "utm_medium":     payload.utm_medium or None,
        "utm_source":     payload.utm_source[:128] if payload.utm_source else None,
        "utm_campaign":   payload.utm_campaign[:256] if payload.utm_campaign else None,
        "utm_content":    payload.utm_content[:256] if payload.utm_content else None,
        "utm_term":       payload.utm_term[:256] if payload.utm_term else None,
        "click_id":       payload.click_id[:256] if payload.click_id else None,
        "landing_page":   payload.landing_page[:512] if payload.landing_page else None,
        "product_id":     payload.product_id or None,
        "device_type":    payload.device_type if payload.device_type in ("mobile", "desktop") else None,
    }


@router.post("/track")
def track_event(request: Request, payload: TrackPayload, db: Session = Depends(get_lazy_db)):
    """
    Ingest a single storefront event from spark-tracker.js.

    shop_domain must be a valid *.myshopify.com domain.
    url and product_url are stored as separate columns.
    product_url is normalized to /products/{handle} before persistence.
    source_type and referrer are persisted when present.
    """
    if not is_valid_shop_domain(payload.shop_domain):
        raise HTTPException(
            status_code=400,
            detail="Invalid shop_domain. Must be a valid *.myshopify.com domain.",
        )

    if payload.event_type not in _ALLOWED_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid event_type.",
        )

    # GDPR Art. 6/7 + CCPA/CPRA (Sec-GPC + DNT) consent gate
    # (2026-04-11 audit). When the tracker reports explicit denial OR
    # the browser sends Global Privacy Control, we silently drop the
    # event (200 with an ignored marker — never 400, so scanners
    # can't map the gate).
    if not _consent_allows_ingestion(payload, request=request):
        _bump_consent_metric(accepted=False)
        return {"status": "ignored", "reason": "consent_denied"}
    _bump_consent_metric(accepted=True)

    # Anti-abuse: verify the shop is a known installed merchant.
    # This prevents attackers from fabricating events for arbitrary domains.
    # Cached in Redis for 5 minutes to avoid DB lookup per event.
    if not _is_known_shop(db, payload.shop_domain):
        raise HTTPException(
            status_code=400,
            detail="Unknown shop.",
        )

    # Per-IP + per-shop rate limit: 60 events/min per (IP, shop) combination.
    if not _check_per_shop_rate(request, payload.shop_domain):
        raise HTTPException(
            status_code=429,
            detail="Too many events for this shop.",
        )

    # ── J3-part-1: aggregate ingest admission (jewel-structure
    # 2026-05-17) ──────────────────────────────────────────────────
    # The per-(IP,shop) limit above does NOT bound AGGREGATE write QPS
    # (millions of distinct browser IPs at 10k merchants). Without a
    # global cap an ingest storm saturates the 80-conn PgBouncer pool
    # (shared with dashboard reads + 8 workers) and CASCADES into every
    # endpoint. Cap concurrent EXPENSIVE ingests ≪ pool (mirror of the
    # proven dashboard 4th-tier admission). Over-cap → fast 429: the
    # tracker is fire-and-forget (best-effort analytics), so a bounded
    # fast shed under an extreme storm is graceful degradation, NOT
    # data loss — the alternative is the pool-cascade 500ing the
    # merchant's whole dashboard. Gate is placed AFTER the cheap
    # no-DB validations and BEFORE the expensive visitor-upsert +
    # event INSERT + commit so a shed costs ~1ms, not a full ingest.
    from app.core.ingest_admission import ingest_admit, ingest_release
    _ingest_tok = ingest_admit()
    if _ingest_tok is None:
        raise HTTPException(
            status_code=429,
            detail="Ingest temporarily saturated — retry shortly.",
            headers={"Retry-After": "1"},
        )
    try:
        # Normalize defensively — handles old tracker versions, third-party
        # senders, and any full URL that slipped through. None for
        # non-product input. Pure (no DB), needed by the heatmap call.
        canonical_product_url = normalize_product_url(payload.product_url)

        # Single source of the Event field values — shared with
        # /track/batch and the ingest-buffer drain contract (keys ==
        # ingest_buffer._EVENT_FIELDS). See _event_fields_from_payload.
        _fields = _event_fields_from_payload(payload)

        # ── J3-part-2: high-volume NON-purchase analytics → async
        # buffer (ZERO request DB conn; a singleton drain thread
        # bulk-INSERTs). This is the dominant write volume; moving it
        # off the request pool is what makes the 10k pool-cascade
        # STRUCTURALLY impossible (J3-part-1 only bounded it). Purchase
        # events are revenue/attribution-critical + low-volume → they
        # KEEP the full synchronous path below (§0: must never be at
        # buffer-loss risk). Heatmap + shopify_y are Redis-only (no DB)
        # so they stay inline for both. event_id is unused by any
        # client (grep-verified) so a None id on the async path breaks
        # no contract.
        if payload.event_type != "purchase":
            from app.services.ingest_buffer import enqueue_event
            enqueue_event(_fields)
            _bump_heatmap_bucket(
                shop_domain=payload.shop_domain,
                url=canonical_product_url or payload.page_url or "",
                event_type=payload.event_type,
                x_pct=payload.x_pct,
                y_pct=payload.y_pct,
            )
            _store_shopify_y_mapping(payload)
            return JSONResponse(
                content={"status": "ok", "event_id": None},
                headers=_CORS_HEADERS,
            )

        # ── purchase events: full synchronous path (revenue/order
        # attribution — never buffered, never at loss risk) ──
        _upsert_visitor(db, payload.visitor_id, payload.shop_domain)
        event = Event(**_fields)

        db.add(event)

        # Spatial heatmap bucket increment (Lite spatial heatmap — Lucky
        # Orange Build $39 parity). Click + mousemove events with x_pct/
        # y_pct hit a 10×10 Redis grid; rendered by HeatmapCard click +
        # move tabs. Stored ONLY in Redis to avoid schema migration.
        _bump_heatmap_bucket(
            shop_domain=payload.shop_domain,
            url=canonical_product_url or payload.page_url or "",
            event_type=payload.event_type,
            x_pct=payload.x_pct,
            y_pct=payload.y_pct,
        )

        # Store shopify_y → visitor_id mapping for pixel identity bridging.
        # When the Custom Pixel fires checkout_completed, it sends event.clientId
        # (derived from _shopify_y) but can't read our localStorage. This mapping
        # lets the backend resolve our visitor_id from the pixel's identity.
        _store_shopify_y_mapping(payload)

        # Purchase events also persist to shop_orders for revenue analytics
        _persist_purchase(db, payload)

        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return JSONResponse(
                content={"status": "ok", "event_id": None},
                headers=_CORS_HEADERS,
            )

        # Best-effort geo capture for live visitor map (non-blocking)
        try:
            from app.core.geo import capture_visitor_geo_sync
            capture_visitor_geo_sync(request, payload.shop_domain, payload.visitor_id)
        except Exception as exc:
            log.warning("track: track_event failed: %s", exc)
            pass  # geo is never critical

        return JSONResponse(
            content={"status": "ok", "event_id": event.id},
            headers=_CORS_HEADERS,
        )
    except Exception:
        # The outer admission-guard try wraps db.add + db.commit. Any
        # failure in the body (visitor upsert, persist_purchase, a
        # non-IntegrityError commit error) MUST roll the session back
        # before it propagates / before get_lazy_db teardown — never return
        # a half-written txn to the shared PgBouncer pool
        # (write_no_rollback). Defense-in-depth: explicit, early, here.
        db.rollback()
        raise
    finally:
        ingest_release(_ingest_tok)


# ---------------------------------------------------------------------------
# Batch ingestion — POST /track/batch
#
# Accepts { events: [...] } with up to 50 events per request.
# Single transaction, single commit — 10-50x fewer DB round-trips
# compared to individual /track calls.
#
# Each event in the array uses the same TrackPayload schema.
# Invalid events are skipped (logged) without aborting the batch.
# The response reports accepted count and any rejections.
# ---------------------------------------------------------------------------

class BatchTrackPayload(BaseModel):
    events: list[TrackPayload] = Field(..., max_length=50)


@router.post("/track/batch")
def track_event_batch(request: Request, payload: BatchTrackPayload, db: Session = Depends(get_lazy_db)):
    """
    Ingest a batch of storefront events in a single transaction.

    Accepts up to 50 events.  Invalid events are skipped, not rejected.
    Returns count of accepted vs rejected events.
    """
    # J3-part-1 aggregate ingest admission — sibling of the single
    # /track gate (§11: a known sibling left ungated is the failure
    # mode). Same shared primitive; batch is lower pool-pressure
    # (1 commit/≤50 events) but a batch storm still saturates the
    # shared pool, so the same cap + fast-429 shed applies.
    from app.core.ingest_admission import ingest_admit, ingest_release
    _ingest_tok = ingest_admit()
    if _ingest_tok is None:
        raise HTTPException(
            status_code=429,
            detail="Ingest temporarily saturated — retry shortly.",
            headers={"Retry-After": "1"},
        )
    try:
        MAX_BATCH = 50
        events_list = payload.events[:MAX_BATCH]
        # Buffered (non-purchase → Redis, fire-and-forget, independent
        # of the DB txn) vs synced (purchase → DB, committed together)
        # are counted separately: a purchase-commit IntegrityError must
        # NOT zero analytics that were already safely enqueued (the
        # under-report the #7 split would otherwise introduce).
        buffered_ok = 0
        synced = 0
        rejected = 0

        # Deduplicate visitor upserts within the batch (purchase path
        # only — buffered events' visitors are upserted by the drain).
        seen_visitors: set[tuple[str, str]] = set()
        # Deferred import mirrors the single-/track lazy import (keeps
        # whatever circular-import avoidance the author intended);
        # hoisted out of the per-item loop (once, not ≤50×).
        from app.services.ingest_buffer import enqueue_event

        for item in events_list:
            if not is_valid_shop_domain(item.shop_domain):
                rejected += 1
                continue
            if item.event_type not in _ALLOWED_EVENT_TYPES:
                rejected += 1
                continue

            # ── PRECONDITION PARITY with single /track (GDPR Art. 6/7
            # + CCPA/CPRA GPC/DNT, known-shop anti-abuse, per-shop
            # rate-limit). The pre-2026-05-18 batch gated ONLY on
            # shop-domain-format + event-type, so spark-tracker.js's
            # batched click/mousemove/add_to_cart/begin_checkout were
            # buffered + heatmap-captured even when the visitor DENIED
            # consent or sent Global Privacy Control — a real GDPR/CCPA
            # defect the #7/#6/heatmap commits widened (independent
            # adversarial audit, 2026-05-18). "Parity" must mean the
            # PRECONDITIONS too, not just the buffer mechanism. Batch
            # semantics = skip the offending item (rejected++, continue)
            # — never abort the whole batch, never 4xx (mirrors single
            # /track's silent consent drop + the batch's existing
            # skip-invalid contract). _is_known_shop on a cache hit is
            # Redis-only (0-conn property preserved on the steady-state
            # buffered path); a cache miss opens the lazy session,
            # identical to single /track's documented cold path.
            if not _consent_allows_ingestion(item, request=request):
                _bump_consent_metric(accepted=False)
                rejected += 1
                continue
            _bump_consent_metric(accepted=True)
            if not _is_known_shop(db, item.shop_domain):
                rejected += 1
                continue
            if not _check_per_shop_rate(request, item.shop_domain):
                rejected += 1
                continue

            # ── honest-residual #7: mirror the single-/track J3-part-2
            # split. NON-purchase = async buffer (ZERO request DB conn —
            # the dominant batch volume off the shared pool, making the
            # batch path pool-cascade-immune like single /track);
            # purchase = full synchronous path (revenue/attribution,
            # never buffered, §0). Both branches use the SAME
            # _event_fields_from_payload source (no field drift,
            # keys == ingest_buffer._EVENT_FIELDS). A batch with zero
            # purchases never opens the lazy session ⟹ db.commit()
            # below is a guarded no-op ⟹ 0 connections (composes with
            # the #6 lazy WRITE session).
            if item.event_type != "purchase":
                enqueue_event(_event_fields_from_payload(item))
                # Spatial heatmap parity with single /track's
                # non-purchase branch. spark-tracker.js sends click +
                # mousemove (the ONLY events _bump_heatmap_bucket acts
                # on — it self-gates on event_type) via sendEventBatched
                # ⟹ /track/batch. The pre-2026-05-18 batch never bumped
                # the heatmap, so the Lite spatial HeatmapCard was
                # structurally starved of ~all its data in production
                # (single /track only ever gets page_view/product_view,
                # which heatmap-no-op). Redis-only (no DB / no pool
                # cost) — belongs on the buffered branch; a purchase is
                # never click/mousemove so a purchase-branch call would
                # be provably dead code.
                _bump_heatmap_bucket(
                    shop_domain=item.shop_domain,
                    url=normalize_product_url(item.product_url) or item.page_url or "",
                    event_type=item.event_type,
                    x_pct=item.x_pct,
                    y_pct=item.y_pct,
                )
                _store_shopify_y_mapping(item)
                buffered_ok += 1
                continue

            vkey = (item.visitor_id, item.shop_domain)
            if vkey not in seen_visitors:
                _upsert_visitor(db, item.visitor_id, item.shop_domain)
                seen_visitors.add(vkey)

            db.add(Event(**_event_fields_from_payload(item)))

            # Store shopify_y mapping for identity bridging
            _store_shopify_y_mapping(item)

            # Purchase events also persist to shop_orders
            _persist_purchase(db, item)

            synced += 1

        # Commit ONLY the purchase work. All-non-purchase batch ⟹
        # synced==0 ⟹ no commit ⟹ the #6 lazy session never opens ⟹
        # 0 connections (proven live: x-query-count 0). A commit
        # IntegrityError zeros ONLY the synced count — the buffered
        # analytics are already in Redis and stay accepted.
        if synced > 0:
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                synced = 0
        accepted = buffered_ok + synced

        return JSONResponse(
            content={"status": "ok", "accepted": accepted, "rejected": rejected},
            headers=_CORS_HEADERS,
        )
    except Exception:
        # Same write_no_rollback defense as single /track: roll back a
        # partially-written batch before it propagates / pool-returns.
        db.rollback()
        raise
    finally:
        ingest_release(_ingest_tok)


from fastapi import Response

@router.options("/track")
async def options_track():
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

@router.options("/track/batch")
async def options_track_batch():
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })

