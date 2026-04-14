"""
store_context.py — Real-time store intelligence for chatbot responses.

Gathers all available merchant data into a lightweight context object
that response generators use to produce store-aware, contextualized answers.

Every chatbot response should feel tailored to THIS merchant's store,
not like a generic product explanation.

Public interface:
    get_store_context(db, shop_domain) -> StoreContext
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text, func
from sqlalchemy.orm import Session

log = logging.getLogger("store_context")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class StoreContext:
    """Everything Spark knows about a merchant's store right now."""

    shop_domain: str = ""
    shop_name: str = ""  # human-friendly store name (derived from domain)

    # Plan & setup
    plan: str = "starter"
    billing_active: bool = False
    setup_status: str = "unknown"
    onboarding_status: str = "unknown"

    # Traffic (7-day)
    visitors_7d: int = 0
    new_visitors_7d: int = 0
    returning_visitors_7d: int = 0

    # Conversion
    cart_rate: float | None = None  # avg of new + returning
    orders_7d: int = 0
    revenue_7d: float = 0.0

    # Signals (from product_metrics / opportunity_signals)
    active_signals_count: int = 0
    top_signal_summary: str | None = None  # human-readable top signal

    # Recent issues
    open_incidents: int = 0
    last_resolved_incident: str | None = None  # summary of last resolved

    # Flags
    has_data: bool = False  # whether the store has any meaningful data yet
    has_revenue: bool = False  # whether purchase tracking is working


def _friendly_name(shop_domain: str) -> str:
    """Derive a human-friendly store name from domain."""
    # "cool-store.myshopify.com" → "Cool Store"
    name = shop_domain.replace(".myshopify.com", "").replace("-", " ").replace("_", " ")
    return name.title() if name else shop_domain


def get_store_context(db: Session, shop_domain: str) -> StoreContext:
    """
    Gather real-time store intelligence. Fast — DB queries only, no API calls.
    Returns a StoreContext with everything available, gracefully defaulting
    where data is missing.
    """
    ctx = StoreContext(shop_domain=shop_domain, shop_name=_friendly_name(shop_domain))

    try:
        # --- Merchant record ---
        from app.models.merchant import Merchant
        merchant = db.query(Merchant).filter(Merchant.shop_domain == shop_domain).first()
        if merchant:
            ctx.plan = merchant.plan or "starter"
            ctx.billing_active = merchant.billing_active or False
            ctx.onboarding_status = merchant.onboarding_status or "unknown"

        # --- Store metrics (pre-aggregated) ---
        from app.models.store_metrics import StoreMetrics
        metrics = db.query(StoreMetrics).filter(StoreMetrics.shop_domain == shop_domain).first()
        if metrics:
            ctx.new_visitors_7d = metrics.new_visitors_7d or 0
            ctx.returning_visitors_7d = metrics.returning_visitors_7d or 0
            ctx.visitors_7d = ctx.new_visitors_7d + ctx.returning_visitors_7d

            cart_values = [v for v in [metrics.new_visitor_cart_rate, metrics.returning_visitor_cart_rate] if v is not None]
            if cart_values:
                ctx.cart_rate = sum(cart_values) / len(cart_values)

            ctx.has_data = ctx.visitors_7d > 0

        # --- Revenue (from shop_orders, last 7 days) ---
        try:
            cutoff_7d = _now() - timedelta(days=7)
            row = db.execute(text("""
                SELECT COUNT(*), COALESCE(SUM(total_price), 0)
                FROM shop_orders
                WHERE shop_domain = :shop AND created_at >= :cutoff
            """), {"shop": shop_domain, "cutoff": cutoff_7d}).fetchone()
            if row:
                ctx.orders_7d = row[0] or 0
                ctx.revenue_7d = float(row[1] or 0)
                ctx.has_revenue = ctx.orders_7d > 0
        except Exception:
            pass

        # --- Active signals (from opportunity_signals or product_metrics) ---
        try:
            from app.models.product_metrics import ProductMetrics
            # Count products with meaningful recent activity as a signal proxy
            active_products = (
                db.query(func.count(ProductMetrics.id))
                .filter(
                    ProductMetrics.shop_domain == shop_domain,
                    ProductMetrics.views_24h > 0,
                )
                .scalar() or 0
            )
            ctx.active_signals_count = active_products

            # Get the top product by views for a concrete signal
            if active_products > 0:
                top = (
                    db.query(ProductMetrics.product_url, ProductMetrics.views_24h,
                             ProductMetrics.cart_conversions_24h)
                    .filter(
                        ProductMetrics.shop_domain == shop_domain,
                        ProductMetrics.views_24h > 0,
                    )
                    .order_by(ProductMetrics.views_24h.desc())
                    .first()
                )
                if top:
                    product_name = _extract_product_name(top.product_url)
                    views = top.views_24h
                    carts = top.cart_conversions_24h or 0
                    if carts > 0:
                        ctx.top_signal_summary = f"{product_name} ({views} views, {carts} carts in 24h)"
                    else:
                        ctx.top_signal_summary = f"{product_name} ({views} views in 24h, no carts yet)"
        except Exception:
            pass

        # --- Open incidents ---
        try:
            from app.models.support_incident import SupportIncident
            ctx.open_incidents = (
                db.query(func.count(SupportIncident.id))
                .filter(
                    SupportIncident.shop_domain == shop_domain,
                    SupportIncident.status.in_(["open", "triaged", "investigating"]),
                )
                .scalar() or 0
            )

            # Last resolved incident summary
            last_resolved = (
                db.query(SupportIncident.resolution_summary, SupportIncident.affected_area)
                .filter(
                    SupportIncident.shop_domain == shop_domain,
                    SupportIncident.status == "resolved",
                    SupportIncident.resolution_summary.isnot(None),
                )
                .order_by(SupportIncident.resolved_at.desc())
                .first()
            )
            if last_resolved and last_resolved.resolution_summary:
                ctx.last_resolved_incident = last_resolved.resolution_summary[:100]
        except Exception:
            pass

    except Exception as exc:
        log.warning("store_context: failed for %s: %s", shop_domain, exc)

    return ctx


def _extract_product_name(product_url: str | None) -> str:
    """Extract a human-readable product name from a Shopify product URL."""
    if not product_url:
        return "a product"
    # "/products/cool-widget-pro" → "Cool Widget Pro"
    parts = product_url.rstrip("/").split("/")
    slug = parts[-1] if parts else product_url
    name = slug.replace("-", " ").replace("_", " ").title()
    return name if len(name) < 60 else name[:57] + "..."
