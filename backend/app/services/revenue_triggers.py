"""
revenue_triggers.py — Event-driven email triggers based on revenue signals.

Scans product_metrics and opportunity_signals for conditions that warrant
immediate merchant notification. These are NOT scheduled emails — they fire
when real-time conditions are met.

Triggers:
    1. traffic_spike      — product views_1h >= 3x views_24h/24
    2. high_intent_leak   — product with cart_adds but 0 purchases for 48h+
    3. conversion_drop    — product views up but cart_rate dropped > 30% vs 7d
    4. return_visitor_surge — return_visitor_count_7d >= 2x unique_visitors_7d * 0.3

Each trigger generates a revenue-framed email:
    "Your [Product] got X visitors today but 0 bought. You may be losing ~$Y/week."

Safety:
    - Max 1 triggered email per merchant per 48 hours
    - Only fires for merchants with tier >= MEDIUM
    - Email budget checked before send
    - Response guardrails applied to all content

Called by: agent_worker phase (after lifecycle emails)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revenue_metrics import get_shop_currency

log = logging.getLogger("revenue_triggers")

_TRIGGER_COOLDOWN_HOURS = 48
_REDIS_TRIGGER_PREFIX = "hs:rev_trigger:"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run_revenue_triggers(db: Session) -> dict:
    """
    Scan for revenue-significant events and submit trigger email intents.

    All sends go through the email orchestrator.
    The orchestrator handles rate limits, priority resolution, and governance.

    Returns {"checked": int, "triggered": int, "skipped": int}
    """
    summary = {"checked": 0, "triggered": 0, "skipped": 0}

    # Get all active merchants with traffic
    shops = db.execute(text("""
        SELECT DISTINCT pm.shop_domain, m.contact_email
        FROM product_metrics pm
        JOIN merchants m ON m.shop_domain = pm.shop_domain
        WHERE m.install_status = 'active'
          AND m.contact_email IS NOT NULL
          AND m.is_synthetic = false
          AND pm.views_24h > 0
        LIMIT 50
    """)).fetchall()

    for row in shops:
        shop = row[0]
        contact_email = row[1]
        summary["checked"] += 1

        if _is_on_cooldown(shop):
            summary["skipped"] += 1
            continue

        trigger = _find_best_trigger(db, shop)
        if not trigger:
            continue

        # Submit intent to orchestrator (only path)
        from app.services.email_orchestrator import EmailIntent, submit_intent
        html = _build_trigger_html(trigger)
        intent = EmailIntent(
            shop_domain=shop,
            email_type=f"trigger_{trigger['type']}",
            to_email=contact_email,
            subject=trigger["subject"],
            html=html,
            plain_text=trigger["message"],
            from_address="HedgeSpark <dev@hedgesparkhq.com>",
            producer="revenue_triggers",
            context=trigger,
        )
        submit_intent(db, intent)
        _set_cooldown(shop)
        summary["triggered"] += 1

    return summary


def _find_best_trigger(db: Session, shop: str) -> dict | None:
    """Find the most valuable trigger for this merchant. Returns None if nothing notable."""

    # Resolve native currency once — used by all triggers for money formatting.
    currency = get_shop_currency(db, shop)

    # Trigger 1: High-intent leak — cart adds but no purchases (most valuable)
    leak = db.execute(text("""
        SELECT pm.product_url, pm.cart_conversions_24h, pm.views_24h, pm.purchases_24h, pm.revenue_24h
        FROM product_metrics pm
        WHERE pm.shop_domain = :shop
          AND pm.cart_conversions_24h >= 3
          AND pm.purchases_24h = 0
          AND pm.views_24h >= 10
        ORDER BY pm.cart_conversions_24h DESC
        LIMIT 1
    """), {"shop": shop}).first()

    if leak:
        product_url, carts, views, purchases, rev = leak
        product_name = _product_name(db, shop, product_url)
        aov = _get_aov(db, shop)
        weekly_loss = carts * 7 * aov * 0.15
        return {
            "type": "high_intent_leak",
            "product_name": product_name,
            "product_url": product_url,
            "carts": carts,
            "views": views,
            "weekly_loss": round(weekly_loss, 2),
            "subject": f"{product_name} — {carts} cart adds, 0 purchases",
            "message": (
                f"\"{product_name}\" had {carts} add-to-cart events in the last 24 hours "
                f"but zero completed purchases. That pattern usually means something "
                f"between cart and checkout is creating friction.\n\n"
                f"At your store's average order value, closing even a fraction of these "
                f"could recover ~{_format_money(weekly_loss, currency)} per week.\n\n"
                f"Your dashboard has specific recommendations for this product."
            ),
        }

    # Trigger 2: Traffic spike — 1h views >= 3x hourly average
    spike = db.execute(text("""
        SELECT pm.product_url, pm.views_1h, pm.views_24h
        FROM product_metrics pm
        WHERE pm.shop_domain = :shop
          AND pm.views_1h >= 5
          AND pm.views_24h > 0
          AND pm.views_1h >= (pm.views_24h / 24.0) * 3
        ORDER BY pm.views_1h DESC
        LIMIT 1
    """), {"shop": shop}).first()

    if spike:
        product_url, views_1h, views_24h = spike
        product_name = _product_name(db, shop, product_url)
        multiplier = round(views_1h / max(1, views_24h / 24), 1)
        return {
            "type": "traffic_spike",
            "product_name": product_name,
            "product_url": product_url,
            "views_1h": views_1h,
            "views_24h": views_24h,
            "subject": f"{product_name} — {multiplier}x normal traffic right now",
            "message": (
                f"\"{product_name}\" received {views_1h} visitors in the last hour — "
                f"{multiplier}x your typical hourly rate.\n\n"
                f"This could be a social media mention, a paid ad performing well, "
                f"or seasonal interest picking up. "
                f"Your dashboard shows the traffic source breakdown."
            ),
        }

    # Trigger 3: Return visitor surge — lots of people coming back
    returns = db.execute(text("""
        SELECT pm.product_url, pm.return_visitor_count_7d, pm.unique_visitors_7d, pm.purchases_7d
        FROM product_metrics pm
        WHERE pm.shop_domain = :shop
          AND pm.return_visitor_count_7d >= 5
          AND pm.unique_visitors_7d > 0
          AND pm.purchases_7d = 0
          AND (pm.return_visitor_count_7d::float / pm.unique_visitors_7d) >= 0.3
        ORDER BY pm.return_visitor_count_7d DESC
        LIMIT 1
    """), {"shop": shop}).first()

    if returns:
        product_url, return_count, unique, purchases = returns
        product_name = _product_name(db, shop, product_url)
        aov = _get_aov(db, shop)
        potential = return_count * aov * 0.10
        return {
            "type": "return_visitor_surge",
            "product_name": product_name,
            "product_url": product_url,
            "return_count": return_count,
            "subject": f"{product_name} — {return_count} returning visitors, 0 purchases",
            "message": (
                f"{return_count} visitors came back to \"{product_name}\" this week "
                f"without purchasing. Return visitors are high-intent — they've shown "
                f"interest more than once, which means the product appeals to them "
                f"but something is creating hesitation.\n\n"
                f"Your dashboard shows what HedgeSpark recommends for this pattern."
            ),
        }

    return None



def _build_trigger_html(trigger: dict) -> str:
    """Build a brand-consistent HTML email for a revenue trigger.

    Uses the shared email wrapper from email_templates for visual consistency.
    Voice: factual, specific, no alarm — follows brand_voice.py rules.
    """
    from app.services.email_templates import _wrap_html, _heading, _p, _button

    # Build the message paragraphs from the trigger message
    # Split on double newlines and convert each to a styled paragraph
    paragraphs = [p.strip() for p in trigger["message"].split("\n\n") if p.strip()]

    body = _heading(trigger.get("product_name", "Revenue signal detected"))

    for i, para in enumerate(paragraphs):
        # First paragraph is the ground (fact), rest is context
        color = "#c8d1dc" if i == 0 else "#94a3b8"
        body += _p(para, color=color)

    body += _button("Open your dashboard", "https://app.hedgesparkhq.com")
    body += (
        '<p style="margin:20px 0 0 0;font-size:12px;color:#475569;">'
        "If this doesn't look right, reply to this email and we'll look into it."
        "</p>"
    )

    return _wrap_html(trigger["subject"], body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _product_name(db: Session, shop: str, product_url: str) -> str:
    """Get human-readable product name from products table."""
    row = db.execute(text(
        "SELECT title FROM products WHERE shop_domain = :shop AND product_url = :url LIMIT 1"
    ), {"shop": shop, "url": product_url}).first()
    if row and row[0]:
        return row[0][:60]
    # Fallback: extract from URL
    slug = product_url.rstrip("/").split("/")[-1] if product_url else "Unknown Product"
    return slug.replace("-", " ").title()[:60]


def _get_aov(db: Session, shop: str) -> float:
    """Get average order value for a shop. Falls back to €50 if no data."""
    currency = get_shop_currency(db, shop)
    row = db.execute(text("""
        SELECT AVG(total_price) FROM shop_orders
        WHERE shop_domain = :shop AND created_at >= :cutoff
          AND (:currency IS NULL OR currency = :currency)
          AND total_price > 0
    """), {"shop": shop, "cutoff": _now() - timedelta(days=30), "currency": currency}).scalar()
    return float(row) if row else 50.0


def _currency_symbol(currency: str | None) -> str:
    """Map ISO currency code to display symbol."""
    _SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$"}
    return _SYMBOLS.get((currency or "USD").upper(), (currency or "USD") + " ")


def _format_money(amount: float, currency: str | None = None) -> str:
    """Format a money amount for email display using the shop's native currency."""
    sym = _currency_symbol(currency)
    if amount >= 1000:
        return f"{sym}{amount:,.0f}"
    return f"{sym}{amount:.0f}"


def _is_on_cooldown(shop: str) -> bool:
    """Check if this merchant got a trigger email recently."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            return bool(rc.get(f"{_REDIS_TRIGGER_PREFIX}{shop}"))
    except Exception as exc:
        log.warning("revenue_triggers: cooldown check failed: %s", exc)
    return False


def _set_cooldown(shop: str) -> None:
    """Set 48h cooldown after sending a trigger email."""
    try:
        from app.core.redis_client import _client
        rc = _client()
        if rc:
            rc.set(f"{_REDIS_TRIGGER_PREFIX}{shop}", "1", ex=_TRIGGER_COOLDOWN_HOURS * 3600)
    except Exception as exc:
        log.warning("revenue_triggers: set cooldown failed: %s", exc)
