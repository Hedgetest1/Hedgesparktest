"""
opportunity_engine.py — Rule-based opportunity signal detection.

Detection strategy (in order of preference)
--------------------------------------------
1. Read from product_metrics (pre-aggregated by the aggregation worker).
   This is the hot path: no raw event scans on every API request.

2. Bootstrap fallback: if product_metrics has no rows for the shop (worker
   has not completed its first cycle yet), fall back to detect_opportunities()
   which queries the raw events table directly.

Caching strategy (three levels)
---------------------------------
  Level 1  Redis cache_get     — cross-process, survives restarts, 5 min TTL.
  Level 2  OpportunitySignal   — rows with expires_at >= now().
  Level 3  Full detection run  — detect_opportunities_from_metrics() or the
           raw-events fallback.  Result is persisted to DB and to Redis.

Signal taxonomy — 8 signals across 4 groups
--------------------------------------------

  Group A — Traffic quality (mutually exclusive, first match wins per product)
  ────────────────────────────────────────────────────────────────────────────
  DEAD_TRAFFIC               views_24h >= 20  AND  avg_dwell_24h < 5
                             Visitors are bouncing before engaging with the page.
                             Priority bonus: +35

  HIGH_TRAFFIC_NO_CART       views_24h >= 20  AND  cart_conversions_24h == 0
                             Traffic present but no purchase intent signal at all.
                             Priority bonus: +30

  LOW_CONVERSION_ATTENTION   views_24h >= 25  AND  0 < conv_rate < 2 %
                             Some cart events but critically low rate.
                             Priority bonus: +20

  Group B — Engagement quality (mutually exclusive, first match wins)
  ─────────────────────────────────────────────────────────────────────
  HIGH_ENGAGEMENT_NO_ACTION  avg_dwell_24h >= 20  AND  avg_scroll_24h >= 70
                             AND  cart_conversions_24h == 0
                             Deep engagement with zero purchase action.
                             Priority bonus: +28

  SCROLL_HIGH_NO_CLICK       avg_scroll_24h >= 80  AND  avg_dwell_24h >= 10
                             AND  cart_conversions_24h == 0
                             (only fires when HIGH_ENGAGEMENT_NO_ACTION did not)
                             Priority bonus: +15

  Group C — Return-visitor quality (mutually exclusive, first match wins)
  ────────────────────────────────────────────────────────────────────────
  HIGH_RETURN_LOW_CONVERSION return_visitor_count_7d >= 5
                             AND  cart_conversions_24h <= 1
                             Repeat visitors failing to convert.
                             Priority bonus: +18

  RETURN_VISITOR_INTEREST    return_visitor_count_7d > 3
                             (only fires when HIGH_RETURN_LOW_CONVERSION did not)
                             Priority bonus: +10

  Group D — Traffic momentum (independent — can co-fire with A/B/C)
  ─────────────────────────────────────────────────────────────────
  TRAFFIC_SPIKE              views_1h > 1.5 × avg_prior_hourly
                             AND  avg_prior_hourly > 0
                             Priority bonus: +40

Deduplication guarantee
-----------------------
Within each group, elif chains ensure at most one signal fires per product.
Across groups, signals are intentionally independent — a product can trigger
up to four signals (one per group), each telling a different story.

Signal TTL
----------
SIGNAL_TTL_HOURS (24) — set in expires_at on every insert/refresh.
Cleanup is owned by aggregation_worker._cleanup_expired_signals().

Public interface
----------------
  get_or_refresh_signals(shop_domain: str) -> list[dict]
  SIGNAL_TTL_HOURS: int
"""

import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, case, text
from app.services.price_intelligence_engine import update_price_intelligence
from app.services.signal_text import humanize_signal, humanize_action
from app.models.visitor_product_state import VisitorProductState
from app.models.product_opportunity import ProductOpportunity
from app.models.opportunity_signal import OpportunitySignal
from app.models.product_metrics import ProductMetrics
from app.core.database import engine as _db_engine, SessionLocal
from app.core.redis_client import cache_get, cache_set, KEY_SIGNALS, TTL_SIGNALS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SIGNAL_TTL_HOURS: int = 24

# 7 days in milliseconds — widest detection window (return_visitor_count_7d).
_METRICS_FRESHNESS_MS = 7 * 24 * 3_600 * 1_000

# Priority bonuses for rank_score = signal_strength × 100 + priority_bonus.
# Used by brief_engine to pick the top product.
PRIORITY_BONUS: dict[str, int] = {
    # Store-level strategic signals
    "REVENUE_CONCENTRATION":      45,
    "STORE_MOBILE_GAP":           42,
    "STORE_PAID_GAP":             41,
    # Product-level signals
    "TRAFFIC_SPIKE":              40,
    "DEAD_TRAFFIC":               35,
    "HIGH_TRAFFIC_NO_CART":       30,
    "HIGH_ENGAGEMENT_NO_ACTION":  28,
    "DEVICE_PURCHASE_GAP":        27,
    "MOBILE_CONVERSION_GAP":      25,
    "SOURCE_REVENUE_GAP":         24,
    "LANDING_PAGE_FAILURE":       23,
    "CART_RATE_DECLINING":        22,
    "LOW_CONVERSION_ATTENTION":   20,
    "TIME_WINDOW_MISALIGNMENT":   19,
    "HIGH_RETURN_LOW_CONVERSION": 18,
    "PAID_TRAFFIC_NOT_CONVERTING": 16,
    "SCROLL_HIGH_NO_CLICK":       15,
    "RETURN_VISITOR_INTEREST":    10,
    # Early signals (low confidence — ranked below all strong signals)
    "SINGLE_PRODUCT_FOCUS":        5,
    "EARLY_BROWSING_NO_CART":      4,
    "FIRST_VISITOR_ENGAGEMENT":    3,
    "EARLY_DROP_OFF":              2,
}

# Signal types that are low-confidence early signals.
# These must NOT trigger Klaviyo automation.
EARLY_SIGNAL_TYPES = frozenset({
    "EARLY_BROWSING_NO_CART",
    "FIRST_VISITOR_ENGAGEMENT",
    "EARLY_DROP_OFF",
    "SINGLE_PRODUCT_FOCUS",
})


# ---------------------------------------------------------------------------
# Internal helper — product label from URL
# ---------------------------------------------------------------------------

def _label_from_url(url: str) -> str:
    if not url:
        return "this product"
    clean = url.split("?")[0].split("#")[0].rstrip("/")
    parts = [p for p in clean.split("/") if p]
    for i, part in enumerate(parts):
        if part == "products" and i + 1 < len(parts):
            return parts[i + 1].replace("-", " ").replace("_", " ").title()
    if parts:
        return parts[-1].replace("-", " ").replace("_", " ").title()
    return "this product"


# ---------------------------------------------------------------------------
# Signal strength calculators (pure functions, no side effects)
# ---------------------------------------------------------------------------

def _strength_dead_traffic(views_24h: int) -> float:
    """
    Scales with wasted traffic volume.
    0.40 at 20 views → 1.0 at 100+ views.
    """
    return round(min(1.0, (views_24h - 20) / 80 + 0.40), 2)


def _strength_high_traffic_no_cart(views_24h: int) -> float:
    """
    0.40 at 20 views → 1.0 at 90+ views.
    """
    return round(min(1.0, (views_24h - 20) / 70 + 0.40), 2)


def _strength_low_conversion(conv_rate: float) -> float:
    """
    Higher signal when rate is closer to 0 %.
    0.30 floor (evidence of some cart activity) → ~1.0 approaching 0 %.
    """
    return round(max(0.30, 1.0 - (conv_rate / 0.02)), 2)


def _strength_high_engagement_no_action(avg_dwell: float, avg_scroll: float) -> float:
    """
    Weighted combination of dwell (normalised over 60 s) and scroll (0-100).
    0.0 → 1.0.
    """
    dwell_factor = min(1.0, avg_dwell / 60.0)
    scroll_factor = min(1.0, avg_scroll / 100.0)
    return round(dwell_factor * 0.5 + scroll_factor * 0.5, 2)


def _strength_scroll_high_no_click(avg_scroll: float, avg_dwell: float) -> float:
    """
    Primarily driven by scroll depth (80-100 range → 0.50-1.0).
    Dwell factor modulates but cannot drop below 0.30.
    """
    scroll_base = min(1.0, (avg_scroll - 80.0) / 20.0 * 0.5 + 0.50)
    dwell_mod = min(1.0, avg_dwell / 30.0)
    return round(max(0.30, scroll_base * dwell_mod + 0.10), 2)


def _strength_high_return_low_conversion(return_7d: int) -> float:
    """
    0.33 at 5 returns → 1.0 at 15+ returns.
    """
    return round(min(1.0, return_7d / 15.0), 2)


def _strength_return_visitor_interest(return_7d: int) -> float:
    """
    0.20 at 4 returns → 1.0 at 20 returns.
    """
    return round(min(1.0, return_7d / 20.0), 2)


def _strength_traffic_spike(spike_ratio: float) -> float:
    """
    0.30 at 1.5× → 1.0 at 7.5×+.
    """
    return round(min(1.0, spike_ratio / 7.5), 2)


# ---------------------------------------------------------------------------
# Early signal evaluation — fires with 1-5 visitors (low confidence)
# ---------------------------------------------------------------------------

def _evaluate_early_signals(
    product_url: str,
    views_24h: int,
    unique_visitors_24h: int,
    cart_conversions_24h: int,
    avg_dwell_24h: float | None,
    avg_scroll_24h: float | None,
    detected_at: str,
) -> list[dict]:
    """
    Produce low-confidence early signals from minimal data (1-5 visitors).

    These fire ONLY when the standard signal thresholds are not met
    (views_24h < 20). They give merchants immediate time-to-value
    instead of an empty dashboard.

    All early signals carry:
        signal_confidence = "low"
        signal_strength   = 0.10 - 0.25 (deliberately low)

    They are excluded from Klaviyo automation and marked clearly in the UI.
    """
    # Don't fire early signals if we have enough data for real signals
    if views_24h >= 20:
        return []

    signals: list[dict] = []
    label = _label_from_url(product_url)
    dwell = avg_dwell_24h if avg_dwell_24h is not None else None
    scroll = avg_scroll_24h if avg_scroll_24h is not None else None

    m = {
        "views_24h": views_24h,
        "unique_visitors_24h": unique_visitors_24h,
        "avg_dwell_24h": dwell,
        "avg_scroll_24h": scroll,
    }

    # EARLY_BROWSING_NO_CART — any views, zero carts
    if views_24h >= 1 and cart_conversions_24h == 0:
        signals.append({
            "product_url": product_url,
            "signal_type": "EARLY_BROWSING_NO_CART",
            "signal_strength": round(min(0.25, views_24h / 20.0), 2),
            "signal_confidence": "low",
            "explanation": (
                f"Early signal: {views_24h} views, 0 add-to-carts. "
                "Based on limited data — monitor as traffic grows."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("EARLY_BROWSING_NO_CART", label, m),
            "human_action": humanize_action("EARLY_BROWSING_NO_CART"),
        })

    # FIRST_VISITOR_ENGAGEMENT — decent dwell on very first visits
    if views_24h >= 1 and dwell is not None and dwell >= 8:
        signals.append({
            "product_url": product_url,
            "signal_type": "FIRST_VISITOR_ENGAGEMENT",
            "signal_strength": round(min(0.20, dwell / 60.0), 2),
            "signal_confidence": "low",
            "explanation": (
                f"First engagement: visitors averaging {dwell:.0f}s dwell time. "
                "Early positive signal — still building data."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("FIRST_VISITOR_ENGAGEMENT", label, m),
            "human_action": humanize_action("FIRST_VISITOR_ENGAGEMENT"),
        })

    # EARLY_DROP_OFF — visitors leaving very quickly
    if views_24h >= 2 and dwell is not None and dwell < 5:
        signals.append({
            "product_url": product_url,
            "signal_type": "EARLY_DROP_OFF",
            "signal_strength": 0.15,
            "signal_confidence": "low",
            "explanation": (
                f"Early drop-off: visitors leaving in under {dwell:.0f}s. "
                "Limited data, but above-the-fold content may need review."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("EARLY_DROP_OFF", label, m),
            "human_action": humanize_action("EARLY_DROP_OFF"),
        })
    elif views_24h >= 2 and scroll is not None and scroll < 20:
        signals.append({
            "product_url": product_url,
            "signal_type": "EARLY_DROP_OFF",
            "signal_strength": 0.12,
            "signal_confidence": "low",
            "explanation": (
                f"Early drop-off: visitors only scrolling {scroll:.0f}% of the page. "
                "Limited data, but first impressions may not be engaging."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("EARLY_DROP_OFF", label, m),
            "human_action": humanize_action("EARLY_DROP_OFF"),
        })

    return signals


def _evaluate_single_product_focus(
    all_product_signals: list[dict],
    product_views: dict[str, int],
    detected_at: str,
) -> list[dict]:
    """
    SINGLE_PRODUCT_FOCUS — one product receives all or nearly all traffic.

    Fires when total store views are 1-19 and one product has >= 80% share.
    """
    total = sum(product_views.values())
    if total < 1 or total >= 20:
        return []

    top_url = max(product_views, key=product_views.get)
    top_views = product_views[top_url]
    share = top_views / total

    if share < 0.80 or len(product_views) < 1:
        return []

    # Don't duplicate if this product already has early signals
    existing_products = {s["product_url"] for s in all_product_signals if s.get("signal_type") == "SINGLE_PRODUCT_FOCUS"}
    if top_url in existing_products:
        return []

    label = _label_from_url(top_url)
    return [{
        "product_url": top_url,
        "signal_type": "SINGLE_PRODUCT_FOCUS",
        "signal_strength": round(min(0.25, share * 0.25), 2),
        "signal_confidence": "low",
        "explanation": (
            f"All visitor attention ({top_views} of {total} views) is on this product. "
            "Early signal — this is your most interesting product right now."
        ),
        "detected_at": detected_at,
        "human_label": humanize_signal("SINGLE_PRODUCT_FOCUS", label, {"views_24h": top_views}),
        "human_action": humanize_action("SINGLE_PRODUCT_FOCUS"),
    }]


# ---------------------------------------------------------------------------
# Core signal evaluation — applied identically by BOTH detection paths
# ---------------------------------------------------------------------------

def _evaluate_product_signals(
    product_url: str,
    views_24h: int,
    views_1h: int,
    unique_visitors_24h: int,
    cart_conversions_24h: int,
    return_visitor_count_7d: int,
    avg_dwell_24h: float | None,
    avg_scroll_24h: float | None,
    detected_at: str,
    # New segmentation fields (optional for backward compat with bootstrap path)
    views_mobile: int = 0,
    views_desktop: int = 0,
    carts_mobile: int = 0,
    carts_desktop: int = 0,
    cart_conversions_7d: int = 0,
    views_7d: int = 0,
    views_paid: int = 0,
    views_organic: int = 0,
    views_direct: int = 0,
    carts_paid: int = 0,
    carts_organic: int = 0,
    carts_direct: int = 0,
    # Purchase attribution
    purchases_24h: int = 0,
    purchases_mobile: int = 0,
    purchases_desktop: int = 0,
    purchases_paid: int = 0,
    purchases_organic: int = 0,
    purchases_direct: int = 0,
    revenue_24h: float = 0,
    # Time-of-day
    peak_hour_views: int = 0,
    peak_hour_carts: int = 0,
    off_peak_hour_views: int = 0,
    off_peak_hour_carts: int = 0,
    # Session context
    landing_views_24h: int = 0,
    browsing_views_24h: int = 0,
    landing_carts_24h: int = 0,
    browsing_carts_24h: int = 0,
) -> list[dict]:
    """
    Apply all 8 signal rules to a single product's data and return a list
    of signal dicts (zero or more).

    Deduplication is enforced via elif chains within each group:
      Group A (traffic):         DEAD_TRAFFIC > HIGH_TRAFFIC_NO_CART > LOW_CONVERSION_ATTENTION
      Group B (engagement):      HIGH_ENGAGEMENT_NO_ACTION > SCROLL_HIGH_NO_CLICK
      Group C (return visitors): HIGH_RETURN_LOW_CONVERSION > RETURN_VISITOR_INTEREST
      Group D (spike):           independent

    At most 4 signals per product (one per group).
    """
    signals: list[dict] = []
    label = _label_from_url(product_url)

    # Computed conversions
    conv_rate = cart_conversions_24h / views_24h if views_24h > 0 else 0.0

    # Safe dwell/scroll for comparisons (None means no engagement data)
    dwell = avg_dwell_24h if avg_dwell_24h is not None else None
    scroll = avg_scroll_24h if avg_scroll_24h is not None else None

    # ------------------------------------------------------------------ #
    # Group A — Traffic quality (mutually exclusive)                       #
    # ------------------------------------------------------------------ #

    if views_24h >= 20 and dwell is not None and dwell < 5:
        # DEAD_TRAFFIC: people are landing but immediately leaving
        strength = _strength_dead_traffic(views_24h)
        m = {
            "views_24h": views_24h,
            "unique_visitors_24h": unique_visitors_24h,
            "avg_dwell_24h": dwell,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "DEAD_TRAFFIC",
            "signal_strength": strength,
            "explanation": (
                f"{views_24h} views in 24 h but visitors left in under "
                f"{dwell:.1f}s on average — the page is not holding attention."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("DEAD_TRAFFIC", label, m),
            "human_action": humanize_action("DEAD_TRAFFIC"),
        })

    elif views_24h >= 20 and cart_conversions_24h == 0:
        # HIGH_TRAFFIC_NO_CART: traffic present, zero purchase intent
        strength = _strength_high_traffic_no_cart(views_24h)
        m = {
            "views_24h": views_24h,
            "unique_visitors_24h": unique_visitors_24h,
            "cart_conversions_24h": cart_conversions_24h,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "HIGH_TRAFFIC_NO_CART",
            "signal_strength": strength,
            "explanation": (
                f"{views_24h} views from {unique_visitors_24h} visitors in 24 h "
                "but no cart or checkout activity detected."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("HIGH_TRAFFIC_NO_CART", label, m),
            "human_action": humanize_action("HIGH_TRAFFIC_NO_CART"),
        })

    elif views_24h >= 25 and cart_conversions_24h > 0 and conv_rate < 0.02:
        # LOW_CONVERSION_ATTENTION: some intent but critically low rate
        strength = _strength_low_conversion(conv_rate)
        m = {
            "views_24h": views_24h,
            "cart_conversions_24h": cart_conversions_24h,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "LOW_CONVERSION_ATTENTION",
            "signal_strength": strength,
            "explanation": (
                f"{views_24h} views but only {cart_conversions_24h} cart event(s) "
                f"— conversion rate {conv_rate:.1%} is below 2 %."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("LOW_CONVERSION_ATTENTION", label, m),
            "human_action": humanize_action("LOW_CONVERSION_ATTENTION"),
        })

    # ------------------------------------------------------------------ #
    # Group B — Engagement quality (mutually exclusive)                    #
    # ------------------------------------------------------------------ #

    if (
        dwell is not None
        and scroll is not None
        and dwell >= 20
        and scroll >= 70
        and cart_conversions_24h == 0
    ):
        # HIGH_ENGAGEMENT_NO_ACTION: deep engagement, zero conversion
        strength = _strength_high_engagement_no_action(dwell, scroll)
        m = {
            "avg_dwell_24h": dwell,
            "avg_scroll_24h": scroll,
            "cart_conversions_24h": cart_conversions_24h,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "HIGH_ENGAGEMENT_NO_ACTION",
            "signal_strength": strength,
            "explanation": (
                f"Visitors spend {dwell:.0f}s and scroll {scroll:.0f}% of "
                f"{label} on average — but none added it to cart."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("HIGH_ENGAGEMENT_NO_ACTION", label, m),
            "human_action": humanize_action("HIGH_ENGAGEMENT_NO_ACTION"),
        })

    elif (
        scroll is not None
        and dwell is not None
        and scroll >= 85
        and dwell >= 15
        and cart_conversions_24h == 0
    ):
        # SCROLL_HIGH_NO_CLICK: deep readers not converting (tightened thresholds)
        strength = _strength_scroll_high_no_click(scroll, dwell)
        m = {
            "avg_scroll_24h": scroll,
            "avg_dwell_24h": dwell,
            "cart_conversions_24h": cart_conversions_24h,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "SCROLL_HIGH_NO_CLICK",
            "signal_strength": strength,
            "explanation": (
                f"Visitors scroll {scroll:.0f}% through {label} on average "
                "but leave without taking any action."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("SCROLL_HIGH_NO_CLICK", label, m),
            "human_action": humanize_action("SCROLL_HIGH_NO_CLICK"),
        })

    # ------------------------------------------------------------------ #
    # Group C — Return-visitor quality (mutually exclusive)               #
    # ------------------------------------------------------------------ #

    if return_visitor_count_7d >= 5 and cart_conversions_24h <= 1:
        # HIGH_RETURN_LOW_CONVERSION: repeat visitors, almost no conversions
        strength = _strength_high_return_low_conversion(return_visitor_count_7d)
        m = {
            "return_visitor_count_7d": return_visitor_count_7d,
            "cart_conversions_24h": cart_conversions_24h,
        }
        signals.append({
            "product_url": product_url,
            "signal_type": "HIGH_RETURN_LOW_CONVERSION",
            "signal_strength": strength,
            "explanation": (
                f"{return_visitor_count_7d} visitors returned to this product "
                f"on multiple days but only {cart_conversions_24h} cart event(s) "
                "detected — high repeat interest with almost no conversion."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("HIGH_RETURN_LOW_CONVERSION", label, m),
            "human_action": humanize_action("HIGH_RETURN_LOW_CONVERSION"),
        })

    elif return_visitor_count_7d >= 8 and cart_conversions_24h == 0:
        # RETURN_VISITOR_INTEREST: strong repeat engagement with no conversion
        strength = _strength_return_visitor_interest(return_visitor_count_7d)
        m = {"return_visitor_count_7d": return_visitor_count_7d}
        signals.append({
            "product_url": product_url,
            "signal_type": "RETURN_VISITOR_INTEREST",
            "signal_strength": strength,
            "explanation": (
                f"{return_visitor_count_7d} visitors returned to this product "
                "on multiple days — strong sustained interest."
            ),
            "detected_at": detected_at,
            "human_label": humanize_signal("RETURN_VISITOR_INTEREST", label, m),
            "human_action": humanize_action("RETURN_VISITOR_INTEREST"),
        })

    # ------------------------------------------------------------------ #
    # Group D — Traffic spike (independent)                               #
    # ------------------------------------------------------------------ #

    prior_23h = views_24h - views_1h
    if prior_23h > 0:
        avg_prior_hourly = prior_23h / 23.0
        if views_1h >= 10 and views_1h > 3.0 * avg_prior_hourly and avg_prior_hourly > 0:
            spike_ratio = round(views_1h / avg_prior_hourly, 2)
            strength = _strength_traffic_spike(spike_ratio)
            m = {"views_1h": views_1h, "spike_ratio": spike_ratio}
            signals.append({
                "product_url": product_url,
                "signal_type": "TRAFFIC_SPIKE",
                "signal_strength": strength,
                "explanation": (
                    f"{views_1h} views this hour vs {avg_prior_hourly:.1f} hourly average "
                    f"({spike_ratio:.1f}× spike detected)."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("TRAFFIC_SPIKE", label, m),
                "human_action": humanize_action("TRAFFIC_SPIKE"),
            })

    # ------------------------------------------------------------------ #
    # Group E — Device conversion gap (independent)                        #
    # ------------------------------------------------------------------ #

    if views_mobile >= 10 and views_desktop >= 5:
        mobile_rate = carts_mobile / views_mobile if views_mobile > 0 else 0.0
        desktop_rate = carts_desktop / views_desktop if views_desktop > 0 else 0.0

        if desktop_rate > 0 and mobile_rate < desktop_rate * 0.4:
            gap_pct = round((1.0 - mobile_rate / max(desktop_rate, 0.001)) * 100)
            strength = round(min(1.0, views_mobile / 80.0 + 0.30), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "MOBILE_CONVERSION_GAP",
                "signal_strength": strength,
                "explanation": (
                    f"Mobile visitors ({views_mobile} views) convert {gap_pct}% worse "
                    f"than desktop ({views_desktop} views). "
                    f"Mobile cart rate: {mobile_rate:.1%}, desktop: {desktop_rate:.1%}."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal(
                    "MOBILE_CONVERSION_GAP", label,
                    {"views_mobile": views_mobile, "views_desktop": views_desktop},
                ),
                "human_action": humanize_action("MOBILE_CONVERSION_GAP"),
            })
        elif mobile_rate > 0 and desktop_rate < mobile_rate * 0.4 and views_desktop >= 10:
            gap_pct = round((1.0 - desktop_rate / max(mobile_rate, 0.001)) * 100)
            strength = round(min(1.0, views_desktop / 80.0 + 0.30), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "MOBILE_CONVERSION_GAP",
                "signal_strength": strength,
                "explanation": (
                    f"Desktop visitors ({views_desktop} views) convert {gap_pct}% worse "
                    f"than mobile ({views_mobile} views). "
                    f"Desktop cart rate: {desktop_rate:.1%}, mobile: {mobile_rate:.1%}."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal(
                    "MOBILE_CONVERSION_GAP", label,
                    {"views_mobile": views_mobile, "views_desktop": views_desktop},
                ),
                "human_action": humanize_action("MOBILE_CONVERSION_GAP"),
            })

    # ------------------------------------------------------------------ #
    # Group F — Cart rate trend (independent)                              #
    # ------------------------------------------------------------------ #

    if views_24h >= 10 and views_7d >= 30 and cart_conversions_7d > 0:
        rate_24h = cart_conversions_24h / views_24h
        rate_7d = cart_conversions_7d / views_7d
        if rate_7d >= 0.005 and rate_24h < rate_7d * 0.6:
            drop_pct = round((1.0 - rate_24h / rate_7d) * 100)
            strength = round(min(1.0, drop_pct / 70.0 + 0.30), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "CART_RATE_DECLINING",
                "signal_strength": strength,
                "explanation": (
                    f"Cart conversion rate dropped {drop_pct}% — "
                    f"today {rate_24h:.1%} vs 7-day average {rate_7d:.1%}."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal(
                    "CART_RATE_DECLINING", label,
                    {"rate_24h": rate_24h, "rate_7d": rate_7d},
                ),
                "human_action": humanize_action("CART_RATE_DECLINING"),
            })

    # ------------------------------------------------------------------ #
    # Group G — Paid traffic not converting (independent)                  #
    # ------------------------------------------------------------------ #

    if views_paid >= 10 and carts_paid == 0:
        organic_carts = carts_organic + carts_direct
        has_organic_proof = organic_carts > 0
        strength = round(min(1.0, views_paid / 60.0 + 0.30), 2)
        if has_organic_proof:
            explanation = (
                f"{views_paid} paid views with zero carts, but organic/direct traffic "
                f"generated {organic_carts} cart(s). The product page works — "
                f"the paid traffic may be poorly targeted."
            )
        else:
            explanation = (
                f"{views_paid} paid views with zero carts. No traffic source "
                f"is converting — check the product page first, then ad targeting."
            )
        signals.append({
            "product_url": product_url,
            "signal_type": "PAID_TRAFFIC_NOT_CONVERTING",
            "signal_strength": strength,
            "explanation": explanation,
            "detected_at": detected_at,
            "human_label": humanize_signal(
                "PAID_TRAFFIC_NOT_CONVERTING", label,
                {"views_paid": views_paid, "carts_paid": carts_paid},
            ),
            "human_action": humanize_action("PAID_TRAFFIC_NOT_CONVERTING"),
        })

    # ------------------------------------------------------------------ #
    # Group H — Device purchase gap (independent)                          #
    # ------------------------------------------------------------------ #

    if purchases_24h >= 2 and purchases_mobile + purchases_desktop >= 2:
        pm = purchases_mobile
        pd = purchases_desktop
        if pd > 0 and pm == 0 and views_mobile >= 10:
            strength = round(min(1.0, views_mobile / 50.0 + 0.35), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "DEVICE_PURCHASE_GAP",
                "signal_strength": strength,
                "explanation": (
                    f"{views_mobile} mobile views but zero mobile purchases — "
                    f"desktop generated {pd} purchase(s). Mobile checkout may be broken."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("DEVICE_PURCHASE_GAP", label, {"views_mobile": views_mobile, "purchases_desktop": pd}),
                "human_action": humanize_action("DEVICE_PURCHASE_GAP"),
            })
        elif pm > 0 and pd == 0 and views_desktop >= 10:
            strength = round(min(1.0, views_desktop / 50.0 + 0.35), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "DEVICE_PURCHASE_GAP",
                "signal_strength": strength,
                "explanation": (
                    f"{views_desktop} desktop views but zero desktop purchases — "
                    f"mobile generated {pm} purchase(s). Desktop checkout may have issues."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("DEVICE_PURCHASE_GAP", label, {"views_desktop": views_desktop, "purchases_mobile": pm}),
                "human_action": humanize_action("DEVICE_PURCHASE_GAP"),
            })

    # ------------------------------------------------------------------ #
    # Group I — Source revenue gap (independent)                           #
    # ------------------------------------------------------------------ #

    if purchases_24h >= 1 and views_paid >= 10:
        if purchases_paid == 0 and (purchases_organic + purchases_direct) >= 1:
            organic_purchases = purchases_organic + purchases_direct
            strength = round(min(1.0, views_paid / 40.0 + 0.35), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "SOURCE_REVENUE_GAP",
                "signal_strength": strength,
                "explanation": (
                    f"Paid traffic ({views_paid} views) generated zero purchases, "
                    f"while organic/direct traffic generated {organic_purchases}. "
                    f"Ad spend is not converting to revenue."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("SOURCE_REVENUE_GAP", label, {"views_paid": views_paid}),
                "human_action": humanize_action("SOURCE_REVENUE_GAP"),
            })

    # ------------------------------------------------------------------ #
    # Group J — Time window misalignment (independent)                     #
    # ------------------------------------------------------------------ #

    if peak_hour_views >= 8 and off_peak_hour_views >= 8:
        peak_rate = peak_hour_carts / peak_hour_views if peak_hour_views > 0 else 0
        off_peak_rate = off_peak_hour_carts / off_peak_hour_views if off_peak_hour_views > 0 else 0

        if peak_rate > 0 and off_peak_rate > 0:
            if off_peak_rate > peak_rate * 2.0:
                strength = round(min(1.0, 0.35 + (off_peak_rate / peak_rate - 2.0) * 0.15), 2)
                signals.append({
                    "product_url": product_url,
                    "signal_type": "TIME_WINDOW_MISALIGNMENT",
                    "signal_strength": strength,
                    "explanation": (
                        f"Peak traffic hours have {peak_hour_views} views but only "
                        f"{peak_rate:.1%} cart rate. Off-peak converts at {off_peak_rate:.1%} "
                        f"— traffic volume and conversion quality are misaligned."
                    ),
                    "detected_at": detected_at,
                    "human_label": humanize_signal("TIME_WINDOW_MISALIGNMENT", label, {}),
                    "human_action": humanize_action("TIME_WINDOW_MISALIGNMENT"),
                })
            elif peak_rate > off_peak_rate * 2.0:
                strength = round(min(1.0, 0.35 + (peak_rate / off_peak_rate - 2.0) * 0.15), 2)
                signals.append({
                    "product_url": product_url,
                    "signal_type": "TIME_WINDOW_MISALIGNMENT",
                    "signal_strength": strength,
                    "explanation": (
                        f"Off-peak hours have {off_peak_hour_views} views but only "
                        f"{off_peak_rate:.1%} cart rate. Peak hours convert at {peak_rate:.1%} "
                        f"— consider shifting promotional timing."
                    ),
                    "detected_at": detected_at,
                    "human_label": humanize_signal("TIME_WINDOW_MISALIGNMENT", label, {}),
                    "human_action": humanize_action("TIME_WINDOW_MISALIGNMENT"),
                })
        elif peak_rate == 0 and off_peak_rate > 0 and peak_hour_views >= 15:
            strength = round(min(1.0, peak_hour_views / 40.0 + 0.30), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "TIME_WINDOW_MISALIGNMENT",
                "signal_strength": strength,
                "explanation": (
                    f"Peak hours drive {peak_hour_views} views but zero carts. "
                    f"Off-peak hours convert at {off_peak_rate:.1%} — "
                    f"peak traffic quality differs from off-peak."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("TIME_WINDOW_MISALIGNMENT", label, {}),
                "human_action": humanize_action("TIME_WINDOW_MISALIGNMENT"),
            })

    # ------------------------------------------------------------------ #
    # Group K — Landing page failure (independent)                         #
    # ------------------------------------------------------------------ #

    if landing_views_24h >= 10 and browsing_views_24h >= 5:
        landing_rate = landing_carts_24h / landing_views_24h if landing_views_24h > 0 else 0
        browsing_rate = browsing_carts_24h / browsing_views_24h if browsing_views_24h > 0 else 0

        if browsing_rate > 0 and landing_rate < browsing_rate * 0.3:
            strength = round(min(1.0, landing_views_24h / 40.0 + 0.30), 2)
            signals.append({
                "product_url": product_url,
                "signal_type": "LANDING_PAGE_FAILURE",
                "signal_strength": strength,
                "explanation": (
                    f"{landing_views_24h} visitors landed directly on this product page "
                    f"but only {landing_rate:.1%} added to cart. Visitors who browsed to it "
                    f"convert at {browsing_rate:.1%} — the landing experience needs work."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("LANDING_PAGE_FAILURE", label, {"landing_views_24h": landing_views_24h}),
                "human_action": humanize_action("LANDING_PAGE_FAILURE"),
            })

    # Tag all standard signals with high confidence
    for s in signals:
        if "signal_confidence" not in s:
            s["signal_confidence"] = "high"

    return signals


# ---------------------------------------------------------------------------
# classify_opportunity — used by intelligence_worker
# ---------------------------------------------------------------------------

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


def update_product_opportunity(db: Session, product_url: str, shop_domain: str) -> None:
    """
    Aggregate VisitorProductState rows for (shop_domain, product_url) and
    upsert a ProductOpportunity row.  Both arguments are required.
    """
    if not product_url:
        raise ValueError("update_product_opportunity: product_url is required")
    if not shop_domain:
        raise ValueError("update_product_opportunity: shop_domain is required")

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
            func.avg(VisitorProductState.max_scroll_depth).label("avg_scroll"),
        )
        .filter(
            VisitorProductState.shop_domain == shop_domain,
            VisitorProductState.product_url == product_url,
        )
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
        avg_scroll=avg_scroll,
    )

    existing = (
        db.query(ProductOpportunity)
        .filter(
            ProductOpportunity.shop_domain == shop_domain,
            ProductOpportunity.product_url == product_url,
        )
        .first()
    )

    if not existing:
        existing = ProductOpportunity(shop_domain=shop_domain, product_url=product_url)
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
    update_price_intelligence(db, product_url, shop_domain)


# ---------------------------------------------------------------------------
# Raw events path helpers (bootstrap fallback)
# ---------------------------------------------------------------------------

def _conversion_metrics(conn, shop_domain: str, cutoff_ms: int) -> list[dict]:
    """Per product: total_views, unique_visitors, cart_conversions (24 h)."""
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
                COUNT(*)                                              AS total_views,
                COUNT(DISTINCT pv.visitor_id)                         AS unique_visitors,
                COUNT(DISTINCT CASE
                    WHEN cv.visitor_id IS NOT NULL THEN pv.visitor_id
                END)                                                  AS cart_conversions
            FROM product_views pv
            LEFT JOIN cart_visitors cv ON cv.visitor_id = pv.visitor_id
            GROUP BY pv.product_url
            HAVING COUNT(*) >= 5
            ORDER BY COUNT(*) DESC
            LIMIT 100
        """),
        {"shop_domain": shop_domain, "cutoff_ms": cutoff_ms},
    )
    return [dict(r._mapping) for r in result.fetchall()]


def _return_visitor_counts(conn, shop_domain: str, cutoff_7d_ms: int) -> dict[str, int]:
    """product_url → count of visitors who viewed it on 2+ distinct days in 7d."""
    result = conn.execute(
        text("""
            WITH product_daily AS (
                SELECT
                    url AS product_url,
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


def _engagement_metrics(conn, shop_domain: str, cutoff_ms: int) -> dict[str, dict]:
    """
    product_url → {avg_dwell_24h, avg_scroll_24h}

    Reads dwell_time and page_leave events which carry dwell_seconds and
    max_scroll_depth.  Products with no engagement events are absent from
    the returned dict — callers must treat missing keys as None.
    """
    result = conn.execute(
        text("""
            SELECT
                url AS product_url,
                AVG(dwell_seconds)     AS avg_dwell_24h,
                AVG(max_scroll_depth)  AS avg_scroll_24h
            FROM events
            WHERE shop_domain = :shop_domain
              AND timestamp   >= :cutoff_ms
              AND url LIKE '%/products/%'
              AND event_type IN ('dwell_time', 'page_leave')
              AND (dwell_seconds IS NOT NULL OR max_scroll_depth IS NOT NULL)
            GROUP BY url
        """),
        {"shop_domain": shop_domain, "cutoff_ms": cutoff_ms},
    )
    out: dict[str, dict] = {}
    for r in result.fetchall():
        m = r._mapping
        out[m["product_url"]] = {
            "avg_dwell_24h": float(m["avg_dwell_24h"]) if m["avg_dwell_24h"] is not None else None,
            "avg_scroll_24h": float(m["avg_scroll_24h"]) if m["avg_scroll_24h"] is not None else None,
        }
    return out


def _traffic_spikes(conn, shop_domain: str, cutoff_ms: int) -> list[dict]:
    """
    Products where current-hour views > 1.5× average of prior hours.
    Requires at least 2 prior hours of data to avoid false positives.
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
                    AVG(views)                                                     AS avg_hourly_views,
                    MAX(CASE
                        WHEN hour_bucket >= DATE_TRUNC('hour', NOW())
                        THEN views ELSE 0
                    END)                                                           AS current_hour_views,
                    COUNT(DISTINCT hour_bucket)                                    AS hours_with_data
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
            WHERE current_hour_views > 1.5 * avg_hourly_views
              AND avg_hourly_views > 0
              AND hours_with_data >= 2
            ORDER BY spike_ratio DESC
            LIMIT 20
        """),
        {"shop_domain": shop_domain, "cutoff_ms": cutoff_ms},
    )
    return [dict(r._mapping) for r in result.fetchall()]


# ---------------------------------------------------------------------------
# Bootstrap fallback — raw events path
# ---------------------------------------------------------------------------

def detect_opportunities(shop_domain: str) -> list[dict]:
    """
    Run all 8 signal detectors against the raw events table.

    Bootstrap fallback used when product_metrics has no rows for the shop.
    Under normal operation detect_opportunities_from_metrics() is used instead.

    Merges data from four per-product queries into a single unified dict
    per product_url, then delegates to _evaluate_product_signals() — the
    same function used by the metrics path — to guarantee identical behaviour
    between the two paths.
    """
    now = datetime.utcnow()
    cutoff_24h_ms = int((now - timedelta(hours=24)).timestamp() * 1000)
    cutoff_7d_ms = int((now - timedelta(days=7)).timestamp() * 1000)
    detected_at = now.isoformat()

    with _db_engine.begin() as conn:
        conv_rows = _conversion_metrics(conn, shop_domain, cutoff_24h_ms)
        return_map = _return_visitor_counts(conn, shop_domain, cutoff_7d_ms)
        engagement_map = _engagement_metrics(conn, shop_domain, cutoff_24h_ms)
        spikes = _traffic_spikes(conn, shop_domain, cutoff_24h_ms)

    # Build a unified per-product data dict
    product_data: dict[str, dict] = {}

    for row in conv_rows:
        url = row["product_url"]
        product_data[url] = {
            "views_24h": int(row["total_views"]),
            "views_1h": 0,                          # not available from raw path
            "unique_visitors_24h": int(row["unique_visitors"]),
            "cart_conversions_24h": int(row["cart_conversions"]),
            "return_visitor_count_7d": return_map.get(url, 0),
            "avg_dwell_24h": engagement_map.get(url, {}).get("avg_dwell_24h"),
            "avg_scroll_24h": engagement_map.get(url, {}).get("avg_scroll_24h"),
        }

    # Ensure products that appear only in return_map or engagement_map are included
    for url, return_count in return_map.items():
        if url not in product_data:
            eng = engagement_map.get(url, {})
            product_data[url] = {
                "views_24h": 0,
                "views_1h": 0,
                "unique_visitors_24h": 0,
                "cart_conversions_24h": 0,
                "return_visitor_count_7d": return_count,
                "avg_dwell_24h": eng.get("avg_dwell_24h"),
                "avg_scroll_24h": eng.get("avg_scroll_24h"),
            }
        else:
            product_data[url]["return_visitor_count_7d"] = return_count

    # Inject views_1h from spike data for the TRAFFIC_SPIKE detector
    for spike_row in spikes:
        url = spike_row["product_url"]
        if url not in product_data:
            product_data[url] = {
                "views_24h": 0,
                "views_1h": int(spike_row["current_hour_views"]),
                "unique_visitors_24h": 0,
                "cart_conversions_24h": 0,
                "return_visitor_count_7d": return_map.get(url, 0),
                "avg_dwell_24h": engagement_map.get(url, {}).get("avg_dwell_24h"),
                "avg_scroll_24h": engagement_map.get(url, {}).get("avg_scroll_24h"),
            }
        else:
            product_data[url]["views_1h"] = int(spike_row["current_hour_views"])

    # Evaluate signals for every product using the shared evaluator
    all_signals: list[dict] = []
    for url, data in product_data.items():
        product_signals = _evaluate_product_signals(
            product_url=url,
            views_24h=data["views_24h"],
            views_1h=data["views_1h"],
            unique_visitors_24h=data["unique_visitors_24h"],
            cart_conversions_24h=data["cart_conversions_24h"],
            return_visitor_count_7d=data["return_visitor_count_7d"],
            avg_dwell_24h=data["avg_dwell_24h"],
            avg_scroll_24h=data["avg_scroll_24h"],
            detected_at=detected_at,
        )
        all_signals.extend(product_signals)

    all_signals.sort(key=lambda s: s["signal_strength"], reverse=True)
    return all_signals


# ---------------------------------------------------------------------------
# Pre-aggregated detection path — reads product_metrics (normal operation)
# ---------------------------------------------------------------------------

def _has_product_metrics(shop_domain: str) -> bool:
    db = SessionLocal()
    try:
        return (
            db.query(ProductMetrics.id)
            .filter(ProductMetrics.shop_domain == shop_domain)
            .limit(1)
            .scalar()
        ) is not None
    except Exception as exc:
        logger.warning(
            "opportunity_engine._has_product_metrics(%r) failed — "
            "falling back to raw-events detection: %s",
            shop_domain,
            exc,
        )
        return False
    finally:
        db.close()


def detect_opportunities_from_metrics(shop_domain: str) -> list[dict]:
    """
    Run all 8 signal detectors using pre-aggregated product_metrics rows.

    No raw events table scan is performed.  Freshness filter: only rows
    where last_event_at >= (now - 7 days) are evaluated.

    Delegates to _evaluate_product_signals() so detection logic is defined
    exactly once and shared with the raw-events bootstrap path.
    """
    now = datetime.utcnow()
    cutoff_7d_ms = int(
        (now - timedelta(milliseconds=_METRICS_FRESHNESS_MS)).timestamp() * 1000
    )
    detected_at = now.isoformat()
    all_signals: list[dict] = []

    db = SessionLocal()
    try:
        rows = (
            db.query(ProductMetrics)
            .filter(
                ProductMetrics.shop_domain == shop_domain,
                ProductMetrics.last_event_at.isnot(None),
                ProductMetrics.last_event_at >= cutoff_7d_ms,
            )
            .all()
        )
    finally:
        db.close()

    for row in rows:
        product_signals = _evaluate_product_signals(
            product_url=row.product_url,
            views_24h=int(row.views_24h or 0),
            views_1h=int(row.views_1h or 0),
            unique_visitors_24h=int(row.unique_visitors_24h or 0),
            cart_conversions_24h=int(row.cart_conversions_24h or 0),
            return_visitor_count_7d=int(row.return_visitor_count_7d or 0),
            avg_dwell_24h=float(row.avg_dwell_24h) if row.avg_dwell_24h is not None else None,
            avg_scroll_24h=float(row.avg_scroll_24h) if row.avg_scroll_24h is not None else None,
            detected_at=detected_at,
            views_mobile=int(row.views_mobile or 0),
            views_desktop=int(row.views_desktop or 0),
            carts_mobile=int(row.carts_mobile or 0),
            carts_desktop=int(row.carts_desktop or 0),
            cart_conversions_7d=int(row.cart_conversions_7d or 0),
            views_7d=int(row.views_7d or 0),
            views_paid=int(row.views_paid or 0),
            views_organic=int(row.views_organic or 0),
            views_direct=int(row.views_direct or 0),
            carts_paid=int(row.carts_paid or 0),
            carts_organic=int(row.carts_organic or 0),
            carts_direct=int(row.carts_direct or 0),
            purchases_24h=int(row.purchases_24h or 0),
            purchases_mobile=int(row.purchases_mobile or 0),
            purchases_desktop=int(row.purchases_desktop or 0),
            purchases_paid=int(row.purchases_paid or 0),
            purchases_organic=int(row.purchases_organic or 0),
            purchases_direct=int(row.purchases_direct or 0),
            revenue_24h=float(row.revenue_24h or 0),
            peak_hour_views=int(row.peak_hour_views or 0),
            peak_hour_carts=int(row.peak_hour_carts or 0),
            off_peak_hour_views=int(row.off_peak_hour_views or 0),
            off_peak_hour_carts=int(row.off_peak_hour_carts or 0),
            landing_views_24h=int(row.landing_views_24h or 0),
            browsing_views_24h=int(row.browsing_views_24h or 0),
            landing_carts_24h=int(row.landing_carts_24h or 0),
            browsing_carts_24h=int(row.browsing_carts_24h or 0),
        )
        all_signals.extend(product_signals)

        # Early signals for low-traffic products (views < 20)
        early = _evaluate_early_signals(
            product_url=row.product_url,
            views_24h=int(row.views_24h or 0),
            unique_visitors_24h=int(row.unique_visitors_24h or 0),
            cart_conversions_24h=int(row.cart_conversions_24h or 0),
            avg_dwell_24h=float(row.avg_dwell_24h) if row.avg_dwell_24h is not None else None,
            avg_scroll_24h=float(row.avg_scroll_24h) if row.avg_scroll_24h is not None else None,
            detected_at=detected_at,
        )
        all_signals.extend(early)

    # Store-level strategic signals (aggregate across all products)
    store_signals = _evaluate_store_signals(rows, now.isoformat())
    all_signals.extend(store_signals)

    # Single product focus (cross-product, low-traffic only)
    product_views = {row.product_url: int(row.views_24h or 0) for row in rows}
    focus = _evaluate_single_product_focus(all_signals, product_views, detected_at)
    all_signals.extend(focus)

    all_signals.sort(key=lambda s: s["signal_strength"], reverse=True)
    return all_signals


def _evaluate_store_signals(rows: list, detected_at: str) -> list[dict]:
    """
    Store-level signals derived from aggregate product_metrics.
    These fire once per shop (not per product) and have the highest priority.
    """
    signals: list[dict] = []
    if not rows:
        return signals

    # Aggregate store-wide metrics
    total_views = sum(int(r.views_24h or 0) for r in rows)
    total_rev = sum(float(r.revenue_24h or 0) for r in rows)
    total_vm = sum(int(r.views_mobile or 0) for r in rows)
    total_vd = sum(int(r.views_desktop or 0) for r in rows)
    total_pm = sum(int(r.purchases_mobile or 0) for r in rows)
    total_pd = sum(int(r.purchases_desktop or 0) for r in rows)
    total_vp = sum(int(r.views_paid or 0) for r in rows)
    total_vo = sum(int(r.views_organic or 0) for r in rows)
    total_vdi = sum(int(r.views_direct or 0) for r in rows)
    total_pp = sum(int(r.purchases_paid or 0) for r in rows)
    total_po = sum(int(r.purchases_organic or 0) for r in rows)
    total_pdi = sum(int(r.purchases_direct or 0) for r in rows)
    total_purchases = total_pm + total_pd

    # REVENUE_CONCENTRATION — top product > 50% of total revenue
    if total_rev > 0:
        revenues = sorted(
            [(float(r.revenue_24h or 0), r.product_url) for r in rows],
            reverse=True,
        )
        top_rev, top_url = revenues[0]
        top_pct = top_rev / total_rev
        if top_pct > 0.5 and len(revenues) >= 3:
            label = _label_from_url(top_url)
            signals.append({
                "product_url": top_url,
                "signal_type": "REVENUE_CONCENTRATION",
                "signal_strength": round(min(1.0, top_pct), 2),
                "explanation": (
                    f"{label} generates {top_pct:.0%} of your store's revenue. "
                    f"If this product's traffic drops, your entire business is affected."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("REVENUE_CONCENTRATION", label, {}),
                "human_action": humanize_action("REVENUE_CONCENTRATION"),
            })

    # STORE_MOBILE_GAP — mobile > 50% of views but < 30% of purchases
    vt = total_vm + total_vd
    pt = total_pm + total_pd
    if vt >= 20 and pt >= 2:
        m_view_pct = total_vm / vt
        m_purchase_pct = total_pm / pt if pt > 0 else 0
        if m_view_pct > 0.5 and m_purchase_pct < 0.3:
            signals.append({
                "product_url": None,
                "signal_type": "STORE_MOBILE_GAP",
                "signal_strength": round(min(1.0, m_view_pct - m_purchase_pct + 0.3), 2),
                "explanation": (
                    f"Mobile visitors are {m_view_pct:.0%} of your traffic but only "
                    f"{m_purchase_pct:.0%} of purchases. This is a store-wide checkout issue."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("STORE_MOBILE_GAP", "your store", {}),
                "human_action": humanize_action("STORE_MOBILE_GAP"),
            })

    # STORE_PAID_GAP — paid > 40% of views but < 15% of purchases
    vs = total_vp + total_vo + total_vdi
    ps = total_pp + total_po + total_pdi
    if vs >= 20 and ps >= 2:
        paid_view_pct = total_vp / vs
        paid_purchase_pct = total_pp / ps if ps > 0 else 0
        if paid_view_pct > 0.4 and paid_purchase_pct < 0.15:
            signals.append({
                "product_url": None,
                "signal_type": "STORE_PAID_GAP",
                "signal_strength": round(min(1.0, paid_view_pct - paid_purchase_pct + 0.3), 2),
                "explanation": (
                    f"Paid traffic is {paid_view_pct:.0%} of your visits but only "
                    f"{paid_purchase_pct:.0%} of purchases. Your ad spend is not converting to revenue."
                ),
                "detected_at": detected_at,
                "human_label": humanize_signal("STORE_PAID_GAP", "your store", {}),
                "human_action": humanize_action("STORE_PAID_GAP"),
            })

    for s in signals:
        if "signal_confidence" not in s:
            s["signal_confidence"] = "high"

    return signals


# ---------------------------------------------------------------------------
# Persistence layer
# ---------------------------------------------------------------------------

def _persist_signals(signals: list[dict], shop_domain: str) -> None:
    """
    Upsert every signal in the current detection batch.

    Insert: sets expires_at = now + SIGNAL_TTL_HOURS.
    Refresh: extends expires_at by another full SIGNAL_TTL_HOURS from now.
    Cleanup is owned by aggregation_worker._cleanup_expired_signals().
    Errors are swallowed so a DB hiccup never breaks the API response.
    """
    now = datetime.utcnow()
    new_expires_at = now + timedelta(hours=SIGNAL_TTL_HOURS)
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
            confidence = signal.get("signal_confidence", "high")
            if existing is None:
                db.add(
                    OpportunitySignal(
                        shop_domain=shop_domain,
                        product_url=signal["product_url"],
                        signal_type=signal["signal_type"],
                        signal_strength=signal["signal_strength"],
                        signal_confidence=confidence,
                        explanation=signal["explanation"],
                        detected_at=now,
                        refreshed_at=now,
                        expires_at=new_expires_at,
                    )
                )
            else:
                existing.signal_strength = signal["signal_strength"]
                existing.signal_confidence = confidence
                existing.explanation = signal["explanation"]
                existing.detected_at = now
                existing.refreshed_at = now
                existing.expires_at = new_expires_at

        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _read_fresh_signals_from_db(shop_domain: str) -> list[dict]:
    """Read non-expired signals (expires_at >= now) for this shop."""
    now = datetime.utcnow()
    db = SessionLocal()
    try:
        rows = (
            db.query(OpportunitySignal)
            .filter(
                OpportunitySignal.shop_domain == shop_domain,
                OpportunitySignal.expires_at >= now,
            )
            .order_by(OpportunitySignal.signal_strength.desc())
            .all()
        )
        return [
            {
                "product_url": r.product_url,
                "signal_type": r.signal_type,
                "signal_strength": r.signal_strength,
                "signal_confidence": getattr(r, "signal_confidence", None) or "high",
                "explanation": r.explanation,
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                "human_label": humanize_signal(
                    r.signal_type, _label_from_url(r.product_url), None
                ),
                "human_action": humanize_action(r.signal_type),
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
    Serve opportunity signals with a three-level cache.

      Level 1  Redis      — cross-process, TTL_SIGNALS (5 min).
      Level 2  DB read    — non-expired rows (expires_at >= now).
      Level 3  Detection  — metrics path (normal) or raw-events (bootstrap).

    All paths scoped to shop_domain.
    """
    redis_key = KEY_SIGNALS.format(shop=shop_domain)

    cached = cache_get(redis_key)
    if cached is not None:
        return cached

    db_signals = _read_fresh_signals_from_db(shop_domain)
    if db_signals:
        cache_set(redis_key, db_signals, TTL_SIGNALS)
        return db_signals

    if _has_product_metrics(shop_domain):
        signals = detect_opportunities_from_metrics(shop_domain)
    else:
        signals = detect_opportunities(shop_domain)

    _persist_signals(signals, shop_domain)
    cache_set(redis_key, signals, TTL_SIGNALS)
    return signals
