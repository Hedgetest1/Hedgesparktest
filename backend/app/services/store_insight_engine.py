"""
store_insight_engine.py — Structured analytical reasoning for merchants.

Transforms raw store data into actionable, merchant-readable insights.
No LLM. Pure deterministic rules + comparative logic.

The engine:
    1. Compares this week vs last week across key metrics
    2. Identifies the single biggest change and its probable cause
    3. Produces a merchant-readable explanation
    4. Suggests one concrete action

Public interface:
    generate_store_insight(db, shop_domain) -> StoreInsight | None
    answer_performance_question(db, shop_domain, message) -> str | None
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sql_text, func
from sqlalchemy.orm import Session

log = logging.getLogger("store_insight_engine")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class StoreInsight:
    """A single, prioritized merchant insight with reasoning."""
    headline: str           # 1 sentence: what happened
    explanation: str        # 1-2 sentences: why it matters
    action: str             # 1 sentence: what to do
    category: str           # revenue | conversion | traffic | product | health
    severity: str           # positive | neutral | warning | critical
    data: dict = field(default_factory=dict)  # raw numbers for transparency


@dataclass
class WoWMetrics:
    """Week-over-week comparison data."""
    orders_this: int = 0
    orders_last: int = 0
    revenue_this: float = 0.0
    revenue_last: float = 0.0
    visitors_this: int = 0
    visitors_last: int = 0  # estimated from store_metrics
    new_cart_rate: float | None = None
    returning_cart_rate: float | None = None

    @property
    def order_change_pct(self) -> float | None:
        if self.orders_last == 0:
            return None if self.orders_this == 0 else 100.0
        return (self.orders_this - self.orders_last) / self.orders_last * 100

    @property
    def revenue_change_pct(self) -> float | None:
        if self.revenue_last < 1:
            return None if self.revenue_this < 1 else 100.0
        return (self.revenue_this - self.revenue_last) / self.revenue_last * 100


@dataclass
class ProductSignal:
    """A notable product-level observation."""
    product_name: str
    product_url: str
    views_7d: int
    carts_7d: int
    purchases_7d: int
    cart_rate: float  # carts / views
    signal_type: str  # "high_views_no_carts" | "converting_well" | "declining"


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------

def _gather_wow(db: Session, shop_domain: str) -> WoWMetrics:
    """Gather week-over-week order/revenue data."""
    m = WoWMetrics()
    now = _now()
    try:
        row = db.execute(sql_text("""
            SELECT
                COUNT(*) FILTER (WHERE created_at >= :this_week) AS orders_this,
                COUNT(*) FILTER (WHERE created_at < :this_week) AS orders_last,
                COALESCE(SUM(total_price) FILTER (WHERE created_at >= :this_week), 0) AS rev_this,
                COALESCE(SUM(total_price) FILTER (WHERE created_at < :this_week), 0) AS rev_last
            FROM shop_orders
            WHERE shop_domain = :shop
              AND created_at >= :two_weeks_ago
        """), {
            "shop": shop_domain,
            "this_week": now - timedelta(days=7),
            "two_weeks_ago": now - timedelta(days=14),
        }).fetchone()
        if row:
            m.orders_this = row[0] or 0
            m.orders_last = row[1] or 0
            m.revenue_this = float(row[2] or 0)
            m.revenue_last = float(row[3] or 0)
    except Exception as exc:
        log.warning("insight_engine: order WoW query failed: %s", exc)

    try:
        from app.models.store_metrics import StoreMetrics
        metrics = db.query(StoreMetrics).filter(StoreMetrics.shop_domain == shop_domain).first()
        if metrics:
            m.visitors_this = (metrics.new_visitors_7d or 0) + (metrics.returning_visitors_7d or 0)
            m.new_cart_rate = metrics.new_visitor_cart_rate
            m.returning_cart_rate = metrics.returning_visitor_cart_rate
    except Exception:
        pass

    return m


def _gather_product_signals(db: Session, shop_domain: str) -> list[ProductSignal]:
    """Find notable product-level patterns."""
    signals: list[ProductSignal] = []
    try:
        from app.models.product_metrics import ProductMetrics
        products = (
            db.query(ProductMetrics)
            .filter(
                ProductMetrics.shop_domain == shop_domain,
                ProductMetrics.views_7d >= 3,
            )
            .order_by(ProductMetrics.views_7d.desc())
            .limit(10)
            .all()
        )

        for p in products:
            views = p.views_7d or 0
            carts = p.cart_conversions_7d or 0
            purchases = p.purchases_7d or 0
            cart_rate = carts / views if views > 0 else 0

            name = _product_name(p.product_url)

            if views >= 10 and carts == 0:
                signals.append(ProductSignal(
                    name, p.product_url, views, carts, purchases,
                    cart_rate, "high_views_no_carts",
                ))
            elif cart_rate > 0.15:
                signals.append(ProductSignal(
                    name, p.product_url, views, carts, purchases,
                    cart_rate, "converting_well",
                ))

    except Exception as exc:
        log.warning("insight_engine: product signal query failed: %s", exc)

    return signals


def _product_name(url: str | None) -> str:
    if not url:
        return "a product"
    slug = url.rstrip("/").split("/")[-1]
    return slug.replace("-", " ").replace("_", " ").title()


# ---------------------------------------------------------------------------
# Insight generation — the reasoning engine
# ---------------------------------------------------------------------------

def generate_store_insight(db: Session, shop_domain: str) -> StoreInsight | None:
    """
    Analyze the merchant's store and produce the SINGLE most important insight.

    Priority order:
    1. Revenue anomaly (big drop or big growth)
    2. Conversion problem (high traffic, low conversion)
    3. Product-level opportunity (views but no carts)
    4. Traffic health
    5. Positive momentum

    Returns None if there's insufficient data to say anything meaningful.
    """
    wow = _gather_wow(db, shop_domain)
    products = _gather_product_signals(db, shop_domain)

    # Need some baseline data to reason about
    if wow.orders_this == 0 and wow.orders_last == 0 and wow.visitors_this == 0:
        return None

    # --- Priority 1: Revenue anomaly ---
    rev_change = wow.revenue_change_pct
    if rev_change is not None and abs(rev_change) >= 20 and wow.revenue_last >= 20:
        if rev_change <= -30:
            # Big drop — find probable cause
            explanation = _explain_revenue_drop(wow, products)
            return StoreInsight(
                headline=f"Revenue dropped {abs(rev_change):.0f}% this week.",
                explanation=explanation,
                action=_suggest_revenue_action(wow, products),
                category="revenue",
                severity="warning" if rev_change > -50 else "critical",
                data={"revenue_this": wow.revenue_this, "revenue_last": wow.revenue_last,
                      "change_pct": round(rev_change, 1)},
            )
        elif rev_change >= 30:
            # Big growth — celebrate + identify driver
            driver = _identify_growth_driver(wow, products)
            return StoreInsight(
                headline=f"Revenue is up {rev_change:.0f}% this week.",
                explanation=driver,
                action="Keep doing what's working. Check your top traffic sources to see what's driving this.",
                category="revenue",
                severity="positive",
                data={"revenue_this": wow.revenue_this, "revenue_last": wow.revenue_last,
                      "change_pct": round(rev_change, 1)},
            )

    # --- Priority 2: Conversion problem ---
    if wow.visitors_this >= 10 and wow.new_cart_rate is not None:
        avg_cart = wow.new_cart_rate
        if wow.returning_cart_rate is not None:
            avg_cart = (wow.new_cart_rate + wow.returning_cart_rate) / 2

        if avg_cart < 0.02 and wow.visitors_this >= 20:
            return StoreInsight(
                headline=f"You\u2019re getting traffic ({wow.visitors_this} visitors this week) but very few are adding to cart.",
                explanation=_explain_low_conversion(wow, products),
                action="Focus on your top product pages \u2014 check if product images, descriptions, and pricing are compelling.",
                category="conversion",
                severity="warning",
                data={"visitors": wow.visitors_this, "cart_rate": round(avg_cart, 4)},
            )

        # Returning visitors converting less
        if (wow.returning_cart_rate is not None and wow.new_cart_rate is not None
                and wow.returning_cart_rate < wow.new_cart_rate * 0.5
                and wow.returning_cart_rate < 0.1):
            return StoreInsight(
                headline="Returning visitors are converting much less than new visitors.",
                explanation=(
                    f"New visitor cart rate: {wow.new_cart_rate:.1%}. "
                    f"Returning visitor cart rate: {wow.returning_cart_rate:.1%}. "
                    f"This suggests returning visitors aren\u2019t finding new reasons to buy."
                ),
                action="Consider rotating featured products, adding new arrivals, or using a return visitor nudge with a special offer.",
                category="conversion",
                severity="warning",
                data={"new_cart_rate": round(wow.new_cart_rate, 4),
                      "returning_cart_rate": round(wow.returning_cart_rate, 4)},
            )

    # --- Priority 3: Product-level opportunity ---
    high_views_no_carts = [p for p in products if p.signal_type == "high_views_no_carts"]
    if high_views_no_carts:
        top = high_views_no_carts[0]
        return StoreInsight(
            headline=f"{top.product_name} is getting attention ({top.views_7d} views) but zero carts.",
            explanation=(
                f"Visitors are looking at this product but not adding it to cart. "
                f"This usually means the product page isn\u2019t converting \u2014 "
                f"pricing, images, or copy may need work."
            ),
            action=f"Review the product page for {top.product_name}. A social proof nudge or urgency cue could also help.",
            category="product",
            severity="warning",
            data={"product": top.product_name, "views": top.views_7d, "carts": top.carts_7d},
        )

    # --- Priority 4: Positive momentum ---
    if wow.orders_this > 0 and wow.revenue_this > 0:
        converting_well = [p for p in products if p.signal_type == "converting_well"]
        if converting_well:
            top = converting_well[0]
            return StoreInsight(
                headline=f"Your store is generating revenue \u2014 ${wow.revenue_this:,.0f} this week.",
                explanation=f"{top.product_name} is converting well ({top.cart_rate:.0%} cart rate). That\u2019s your strongest performer right now.",
                action="Consider promoting this product more aggressively or using it as a lead magnet in your ads.",
                category="revenue",
                severity="positive",
                data={"revenue": wow.revenue_this, "top_product": top.product_name,
                      "cart_rate": round(top.cart_rate, 3)},
            )

        return StoreInsight(
            headline=f"Your store did ${wow.revenue_this:,.0f} in revenue this week ({wow.orders_this} orders).",
            explanation="Revenue tracking is active and attribution is working.",
            action="Check the Revenue section to see which traffic sources are driving purchases.",
            category="revenue",
            severity="positive",
            data={"revenue": wow.revenue_this, "orders": wow.orders_this},
        )

    # --- Priority 5: Traffic but no purchases ---
    if wow.visitors_this > 0 and wow.orders_this == 0:
        return StoreInsight(
            headline=f"You have {wow.visitors_this} visitors this week but no tracked purchases yet.",
            explanation="Either purchase tracking isn\u2019t set up yet, or visitors aren\u2019t converting. Check the setup checklist for purchase tracking.",
            action="Complete the Purchase Tracking setup step if you haven\u2019t already. If it\u2019s done, focus on your product pages.",
            category="traffic",
            severity="neutral",
            data={"visitors": wow.visitors_this},
        )

    return None


# ---------------------------------------------------------------------------
# Reasoning helpers — explain WHY, not just WHAT
# ---------------------------------------------------------------------------

def _explain_revenue_drop(wow: WoWMetrics, products: list[ProductSignal]) -> str:
    """Identify the probable cause of a revenue drop."""
    parts = []

    # Check if order count dropped (demand issue) or AOV dropped (pricing issue)
    if wow.orders_last > 0 and wow.orders_this > 0:
        aov_this = wow.revenue_this / wow.orders_this
        aov_last = wow.revenue_last / wow.orders_last
        if aov_this < aov_last * 0.7:
            parts.append(f"Average order value dropped from ${aov_last:.0f} to ${aov_this:.0f} \u2014 customers are buying cheaper items.")
        elif wow.orders_this < wow.orders_last * 0.7:
            parts.append(f"Order count dropped from {wow.orders_last} to {wow.orders_this} \u2014 fewer customers are completing purchases.")
    elif wow.orders_this == 0 and wow.orders_last > 0:
        parts.append("No orders this week compared to last week \u2014 either traffic dropped or conversion stalled.")

    # Check cart rate
    if wow.new_cart_rate is not None and wow.new_cart_rate < 0.03:
        parts.append("Cart rate is very low, suggesting product pages aren\u2019t converting visitors.")

    if not parts:
        parts.append(f"Revenue went from ${wow.revenue_last:,.0f} to ${wow.revenue_this:,.0f}.")

    return " ".join(parts)


def _suggest_revenue_action(wow: WoWMetrics, products: list[ProductSignal]) -> str:
    """Suggest a concrete action for revenue recovery."""
    if wow.orders_this == 0:
        return "Check if purchase tracking is still active. If it is, focus on driving traffic to your best-converting products."

    high_views = [p for p in products if p.signal_type == "high_views_no_carts"]
    if high_views:
        return f"Start with {high_views[0].product_name} \u2014 it\u2019s getting views but no carts. The product page may need work."

    return "Review your traffic sources in the Attribution section to see if a specific channel dropped."


def _identify_growth_driver(wow: WoWMetrics, products: list[ProductSignal]) -> str:
    """Identify what's driving revenue growth."""
    converters = [p for p in products if p.signal_type == "converting_well"]
    if converters:
        return f"{converters[0].product_name} is your strongest converter right now ({converters[0].cart_rate:.0%} cart rate)."

    if wow.orders_this > wow.orders_last * 1.5:
        return f"Order volume jumped from {wow.orders_last} to {wow.orders_this} \u2014 more customers are buying."

    return f"Revenue grew from ${wow.revenue_last:,.0f} to ${wow.revenue_this:,.0f}."


def _explain_low_conversion(wow: WoWMetrics, products: list[ProductSignal]) -> str:
    """Explain why conversion is low."""
    high_views = [p for p in products if p.signal_type == "high_views_no_carts"]
    if high_views:
        return (
            f"{high_views[0].product_name} has {high_views[0].views_7d} views "
            f"but zero carts \u2014 that\u2019s likely where the conversion bottleneck is."
        )
    return "Visitors are browsing but not adding to cart. Product pages, pricing, or product-market fit may need attention."


# ---------------------------------------------------------------------------
# Performance question answering
# ---------------------------------------------------------------------------

def answer_performance_question(db: Session, shop_domain: str, message: str) -> str | None:
    """
    Answer merchant questions about store performance with real analysis.

    Handles: "how is my store doing", "why are sales down", "what should I focus on",
    "what's working", etc.

    Returns a structured insight response, or None if the question isn't performance-related.
    """
    text = message.lower()

    # Detect performance questions
    is_performance = any(p in text for p in (
        "how is my store", "how am i doing", "how's my store", "store doing",
        "why are sales", "why is revenue", "sales down", "revenue down",
        "why are my sales", "sales low", "revenue low",
        "what should i focus", "what to focus", "where should i",
        "what's working", "what is working", "what works",
        "any insights", "give me insights", "store health",
        "performance", "overview", "summary", "how's business",
        "store report", "weekly report", "how are things",
    ))

    if not is_performance:
        return None

    insight = generate_store_insight(db, shop_domain)
    if not insight:
        return "I don\u2019t have enough data yet to give you a meaningful performance analysis. Once you have a few days of visitor and order data, I\u2019ll be able to tell you exactly what\u2019s happening."

    # Try full brief for deeper answer, fall back to single insight
    brief = generate_store_brief(db, shop_domain)
    if brief:
        return brief.to_chat_message()

    response = f"{insight.headline}\n\n{insight.explanation}\n\n{insight.action}"
    return response


# ---------------------------------------------------------------------------
# StoreBrief — Multi-signal synthesized intelligence
# ---------------------------------------------------------------------------

@dataclass
class SignalStatus:
    """A single metric trend for the brief."""
    name: str           # "traffic" | "conversion" | "revenue"
    direction: str      # "up" | "down" | "stable" | "unknown"
    detail: str         # "12% up" or "stable at 45 visitors"
    value_this: float = 0
    value_last: float = 0


@dataclass
class StoreBrief:
    """
    Multi-signal synthesized intelligence brief.

    Not a list of insights — a UNIFIED narrative that tells the merchant:
    1. What's happening across ALL signals (traffic, conversion, revenue)
    2. What the PRIMARY issue or opportunity is
    3. ONE action to take
    """
    shop_domain: str
    generated_at: str

    # Signal statuses (the "what")
    signals: list[SignalStatus] = field(default_factory=list)

    # Synthesis (the "why")
    diagnosis: str = ""          # "The issue is not traffic. It's conversion."
    primary_signal: str = ""     # which signal matters most right now

    # Priority (the "what to do")
    priority_insight: StoreInsight | None = None

    # Raw data for API/dashboard consumption
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "shop_domain": self.shop_domain,
            "generated_at": self.generated_at,
            "signals": [
                {"name": s.name, "direction": s.direction, "detail": s.detail}
                for s in self.signals
            ],
            "diagnosis": self.diagnosis,
            "primary_signal": self.primary_signal,
            "priority_insight": {
                "headline": self.priority_insight.headline,
                "explanation": self.priority_insight.explanation,
                "action": self.priority_insight.action,
                "category": self.priority_insight.category,
                "severity": self.priority_insight.severity,
            } if self.priority_insight else None,
            "data": self.data,
        }

    def to_chat_message(self) -> str:
        """Format as a conversational chat response."""
        parts = []

        # Signal summary
        if self.signals:
            trend_lines = []
            for s in self.signals:
                trend_lines.append(f"\u2022 {s.name.title()}: {s.detail}")
            parts.append("Here\u2019s what I\u2019m seeing this week:\n" + "\n".join(trend_lines))

        # Diagnosis
        if self.diagnosis:
            parts.append(self.diagnosis)

        # Priority action
        if self.priority_insight:
            parts.append(f"Priority: {self.priority_insight.headline}")
            parts.append(self.priority_insight.action)

        return "\n\n".join(parts)


def generate_store_brief(db: Session, shop_domain: str) -> StoreBrief | None:
    """
    Generate a multi-signal synthesized intelligence brief.

    Combines traffic + conversion + revenue + product signals into
    a unified diagnosis with one clear priority.

    Returns None if insufficient data.
    """
    wow = _gather_wow(db, shop_domain)
    products = _gather_product_signals(db, shop_domain)
    now = _now()

    # Need meaningful data
    if wow.visitors_this == 0 and wow.orders_this == 0 and wow.orders_last == 0:
        return None

    brief = StoreBrief(
        shop_domain=shop_domain,
        generated_at=now.isoformat() + "Z",
    )

    # --- Build signal statuses ---

    # Traffic signal
    traffic_detail = f"{wow.visitors_this} visitors this week"
    if wow.visitors_this == 0:
        brief.signals.append(SignalStatus("traffic", "unknown", "no data yet"))
    else:
        brief.signals.append(SignalStatus("traffic", "stable", traffic_detail,
                                          wow.visitors_this, 0))

    # Conversion signal
    if wow.new_cart_rate is not None:
        avg_cart = wow.new_cart_rate
        if wow.returning_cart_rate is not None:
            avg_cart = (wow.new_cart_rate + wow.returning_cart_rate) / 2
        if avg_cart < 0.02:
            brief.signals.append(SignalStatus("conversion", "down",
                                              f"cart rate at {avg_cart:.1%} \u2014 very low",
                                              avg_cart, 0))
        elif avg_cart < 0.05:
            brief.signals.append(SignalStatus("conversion", "stable",
                                              f"cart rate at {avg_cart:.1%}",
                                              avg_cart, 0))
        else:
            brief.signals.append(SignalStatus("conversion", "up",
                                              f"cart rate at {avg_cart:.1%} \u2014 healthy",
                                              avg_cart, 0))

    # Revenue signal
    rev_change = wow.revenue_change_pct
    if wow.revenue_this > 0 or wow.revenue_last > 0:
        if rev_change is not None and wow.revenue_last >= 10:
            if rev_change >= 20:
                brief.signals.append(SignalStatus("revenue", "up",
                    f"${wow.revenue_this:,.0f} (+{rev_change:.0f}% vs last week)",
                    wow.revenue_this, wow.revenue_last))
            elif rev_change <= -20:
                brief.signals.append(SignalStatus("revenue", "down",
                    f"${wow.revenue_this:,.0f} ({rev_change:.0f}% vs last week)",
                    wow.revenue_this, wow.revenue_last))
            else:
                brief.signals.append(SignalStatus("revenue", "stable",
                    f"${wow.revenue_this:,.0f} ({wow.orders_this} orders)",
                    wow.revenue_this, wow.revenue_last))
        elif wow.revenue_this > 0:
            brief.signals.append(SignalStatus("revenue", "up",
                f"${wow.revenue_this:,.0f} ({wow.orders_this} orders)",
                wow.revenue_this, 0))

    # --- Multi-signal diagnosis ---
    down_signals = [s for s in brief.signals if s.direction == "down"]
    up_signals = [s for s in brief.signals if s.direction == "up"]

    if not down_signals and up_signals:
        brief.diagnosis = "Your store is on a positive trajectory."
        brief.primary_signal = up_signals[0].name
    elif len(down_signals) == 1:
        ds = down_signals[0]
        others_ok = all(s.direction != "down" for s in brief.signals if s.name != ds.name)
        if others_ok:
            brief.diagnosis = (
                f"The issue is specifically {ds.name}. "
                f"Other signals look healthy."
            )
        else:
            brief.diagnosis = f"{ds.name.title()} needs attention."
        brief.primary_signal = ds.name
    elif len(down_signals) >= 2:
        names = " and ".join(s.name for s in down_signals)
        # Determine root cause
        has_traffic_down = any(s.name == "traffic" for s in down_signals)
        has_conversion_down = any(s.name == "conversion" for s in down_signals)
        if has_traffic_down and has_conversion_down:
            brief.diagnosis = "Both traffic and conversion are struggling. Start with traffic \u2014 you need visitors before you can optimize conversion."
            brief.primary_signal = "traffic"
        elif has_conversion_down:
            brief.diagnosis = "The issue is not traffic. The issue is conversion."
            brief.primary_signal = "conversion"
        else:
            brief.diagnosis = f"{names.title()} are both declining."
            brief.primary_signal = down_signals[0].name
    elif brief.signals:
        brief.diagnosis = "No major issues detected. Your store is stable."
        brief.primary_signal = "health"

    # --- Priority insight (reuse existing engine) ---
    brief.priority_insight = generate_store_insight(db, shop_domain)

    # --- Raw data ---
    brief.data = {
        "visitors_7d": wow.visitors_this,
        "orders_this_week": wow.orders_this,
        "orders_last_week": wow.orders_last,
        "revenue_this_week": wow.revenue_this,
        "revenue_last_week": wow.revenue_last,
        "revenue_change_pct": round(rev_change, 1) if rev_change is not None else None,
        "cart_rate": round(avg_cart, 4) if wow.new_cart_rate is not None else None,
        "products_tracked": len(products),
        # Bottlenecks now carry the real numbers the hero renders, so the
        # dashboard never has to invent placeholder views/carts values.
        "conversion_bottlenecks": [
            {
                "product_name": p.product_name,
                "views_7d": p.views_7d,
                "carts_7d": p.carts_7d,
                "cart_rate": round(p.cart_rate, 4),
            }
            for p in products
            if p.signal_type == "high_views_no_carts"
        ],
        "top_converters": [p.product_name for p in products if p.signal_type == "converting_well"],
    }

    return brief
