"""
signal_text.py — Plain-English translation layer for all signal types.

Public interface
----------------
    humanize_signal(signal_type, product_label, metrics=None) -> str
    humanize_action(signal_type) -> str

Signal taxonomy
---------------
Detection-engine signals (opportunity_signals table):

  Traffic signals (mutually exclusive within the group — first match wins):
    DEAD_TRAFFIC               views_24h >= 20, avg_dwell_24h < 5
    HIGH_TRAFFIC_NO_CART       views_24h >= 20, cart_conversions_24h == 0
    LOW_CONVERSION_ATTENTION   views_24h >= 25, 0 < conv_rate < 2 %

  Engagement signals (mutually exclusive within the group):
    HIGH_ENGAGEMENT_NO_ACTION  avg_dwell >= 20, avg_scroll >= 70, cart == 0
    SCROLL_HIGH_NO_CLICK       avg_scroll >= 80, avg_dwell >= 10, cart == 0

  Return-visitor signals (mutually exclusive within the group):
    HIGH_RETURN_LOW_CONVERSION return_visitor_count_7d >= 5, cart_conversions_24h <= 1
    RETURN_VISITOR_INTEREST    return_visitor_count_7d > 3

  Independent:
    TRAFFIC_SPIKE              views_1h > 1.5 × avg_prior_hourly

Classify-opportunity signals (product_opportunities table):
    PRICE_DROP_OR_LOW_STOCK_NUDGE
    WISHLIST_PROMPT_TEST
    FRICTION_OR_PRICE_SENSITIVITY
    HIGH_INTEREST_PRODUCT
    NO_ACTION

Design notes
------------
- No AI, no external calls, no DB access.  Pure translation.
- humanize_signal() degrades safely when metrics are absent or incomplete.
- humanize_action() is always deterministic from signal_type alone.
- Both functions accept unknown signal_type values and fall back gracefully.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_int(value: object, fallback: str = "some") -> str:
    if value is None:
        return fallback
    try:
        n = int(value)
        return str(n) if n > 0 else fallback
    except (TypeError, ValueError):
        return fallback


def _fmt_rate(numerator: object, denominator: object) -> str:
    try:
        n = float(numerator or 0)
        d = float(denominator or 0)
        if d <= 0:
            return "a low rate"
        return f"{n / d:.1%}"
    except (TypeError, ValueError):
        return "a low rate"


def _fmt_float(value: object, decimals: int = 1, fallback: str = "an average") -> str:
    if value is None:
        return fallback
    try:
        f = float(value)
        return f"{f:.{decimals}f}" if f > 0 else fallback
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Signal-text renderers — one pure (label, m) -> str function per signal type
# ---------------------------------------------------------------------------
# Refactor 2026-05-12 (A3 medium close): 309-LOC if-elif chain → registry
# pattern (matches the existing humanize_action / humanize_headline shape
# in this same module). 27 renderers, ~5-15 LOC each, individually testable.

def _text_dead_traffic(label: str, m: dict) -> str:
    views = _fmt_int(m.get("views_24h"))
    dwell = _fmt_float(m.get("avg_dwell_24h"), decimals=1)
    if views != "some" and dwell != "an average":
        return (
            f"{label} received {views} views today but visitors left in "
            f"under {dwell}s on average — the page isn't holding attention."
        )
    if views != "some":
        return (
            f"{label} is getting {views} views today but visitors are "
            "leaving almost immediately."
        )
    return f"{label} has traffic today but visitors are bouncing almost immediately."


def _text_high_traffic_no_cart(label: str, m: dict) -> str:
    views = _fmt_int(m.get("views_24h"))
    unique = _fmt_int(m.get("unique_visitors_24h"))
    if views != "some" and unique != "some":
        return (
            f"{label} had {views} views from {unique} visitors today "
            "— but no one added it to cart."
        )
    return f"{label} is getting traffic today but no one added it to cart."


def _text_low_conversion_attention(label: str, m: dict) -> str:
    views = _fmt_int(m.get("views_24h"))
    cart = _fmt_int(m.get("cart_conversions_24h"), fallback="very few")
    rate = _fmt_rate(m.get("cart_conversions_24h"), m.get("views_24h"))
    if views != "some":
        return (
            f"{label} had {views} views today but only {cart} moved toward "
            f"checkout — a {rate} conversion rate."
        )
    return f"{label} has steady traffic but a very low conversion rate."


def _text_high_engagement_no_action(label: str, m: dict) -> str:
    dwell = _fmt_float(m.get("avg_dwell_24h"), decimals=0)
    scroll = _fmt_float(m.get("avg_scroll_24h"), decimals=0)
    if dwell != "an average" and scroll != "an average":
        return (
            f"Visitors are spending {dwell}s reading {label} and scrolling "
            f"{scroll}% down the page — but none are adding it to cart."
        )
    return (
        f"Visitors are spending significant time on {label} and scrolling "
        "deeply — but none are converting."
    )


def _text_scroll_high_no_click(label: str, m: dict) -> str:
    scroll = _fmt_float(m.get("avg_scroll_24h"), decimals=0)
    if scroll != "an average":
        return (
            f"Visitors scroll {scroll}% through {label} on average "
            "— they're reading, but nothing is prompting them to act."
        )
    return (
        f"Visitors are reading {label} thoroughly but leaving without "
        "taking any action."
    )


def _text_high_return_low_conversion(label: str, m: dict) -> str:
    count = _fmt_int(m.get("return_visitor_count_7d"))
    cart = _fmt_int(m.get("cart_conversions_24h"), fallback="almost no")
    if count != "some":
        return (
            f"{count} visitors came back to {label} multiple times this week "
            f"— but {cart} ended up buying."
        )
    return (
        f"Repeat visitors keep returning to {label} this week "
        "but are not converting."
    )


def _text_return_visitor_interest(label: str, m: dict) -> str:
    count = _fmt_int(m.get("return_visitor_count_7d"))
    if count != "some":
        return (
            f"{label} keeps pulling visitors back "
            f"— {count} people returned on multiple days this week."
        )
    return f"{label} is building repeat visitor interest this week."


def _text_traffic_spike(label: str, m: dict) -> str:
    views_1h = _fmt_int(m.get("views_1h"))
    ratio = _fmt_float(m.get("spike_ratio"), decimals=1, fallback=None)
    if views_1h != "some" and ratio is not None:
        return (
            f"{label} is spiking right now "
            f"— {views_1h} views this hour ({ratio}× above its recent average)."
        )
    if views_1h != "some":
        return f"{label} is spiking right now — {views_1h} views this hour."
    return f"{label} is experiencing a traffic spike right now."


def _text_mobile_conversion_gap(label: str, m: dict) -> str:
    vm = _fmt_int(m.get("views_mobile"))
    vd = _fmt_int(m.get("views_desktop"))
    if vm != "some" and vd != "some":
        return (
            f"{label} converts much worse on one device type "
            f"— {vm} mobile views vs {vd} desktop views with a large cart rate gap."
        )
    return f"{label} has a significant device-based conversion gap."


def _text_cart_rate_declining(label: str, m: dict) -> str:
    return (
        f"{label}'s cart conversion rate is dropping — "
        "today is significantly below its 7-day average."
    )


def _text_paid_traffic_not_converting(label: str, m: dict) -> str:
    vp = _fmt_int(m.get("views_paid"))
    if vp != "some":
        return (
            f"{label} received {vp} paid views today but none moved toward checkout "
            "— the ad spend may be wasted."
        )
    return f"Paid traffic to {label} is not converting."


def _text_device_purchase_gap(label: str, m: dict) -> str:
    return (
        f"{label} has purchases from one device type but zero from the other "
        "despite significant traffic — the checkout experience may be broken on one device."
    )


def _text_source_revenue_gap(label: str, m: dict) -> str:
    return (
        f"Paid traffic to {label} is not generating any purchases, "
        "while organic traffic is converting — ad targeting may be misaligned."
    )


def _text_time_window_misalignment(label: str, m: dict) -> str:
    return (
        f"{label} converts at very different rates depending on time of day "
        "— promotional timing may not match when visitors are ready to buy."
    )


def _text_landing_page_failure(label: str, m: dict) -> str:
    lv = _fmt_int(m.get("landing_views_24h"))
    if lv != "some":
        return (
            f"{lv} visitors landed directly on {label} but very few added to cart. "
            "Visitors who browse to it from other pages convert much better — "
            "the landing experience needs improvement."
        )
    return (
        f"Visitors landing directly on {label} convert much worse than those who browse to it."
    )


def _text_revenue_concentration(label: str, m: dict) -> str:
    return (
        f"Your store's revenue is concentrated in {label}. "
        "If this product's traffic drops, your entire business is affected — "
        "diversify by improving conversion on other products."
    )


def _text_store_mobile_gap(label: str, m: dict) -> str:
    return (
        "Mobile visitors browse your store but don't buy. "
        "This is a store-wide checkout problem — test the full mobile purchase flow."
    )


def _text_store_paid_gap(label: str, m: dict) -> str:
    return (
        "Your paid traffic drives visits but not purchases. "
        "Organic and direct traffic converts better — your ad spend may be misallocated."
    )


def _text_price_drop_or_low_stock_nudge(label: str, m: dict) -> str:
    return (
        f"{label} has high-intent visitors showing strong commitment signals "
        "— a price nudge or low-stock badge could convert them."
    )


def _text_wishlist_prompt_test(label: str, m: dict) -> str:
    return (
        f"{label} is attracting high interest but visitors aren't committing "
        "— a more prominent wishlist button could capture intent."
    )


def _text_friction_or_price_sensitivity(label: str, m: dict) -> str:
    return (
        f"Visitors explore {label} deeply but don't buy "
        "— something in the price, trust, or CTA is creating friction."
    )


def _text_high_interest_product(label: str, m: dict) -> str:
    return f"{label} is consistently attracting high-intent visitors this week."


def _text_no_action(label: str, m: dict) -> str:
    return f"{label} is being tracked — no strong signal detected yet."


def _text_early_browsing_no_cart(label: str, m: dict) -> str:
    views = _fmt_int(m.get("views_24h"))
    if views != "some":
        return (
            f"{label} has had {views} views but no one has added it to cart yet "
            "— still early, worth watching."
        )
    return f"Visitors are browsing {label} but haven't added it to cart yet."


def _text_first_visitor_engagement(label: str, m: dict) -> str:
    dwell = _fmt_float(m.get("avg_dwell_24h"), decimals=0)
    if dwell != "an average":
        return (
            f"A visitor just spent {dwell}s on {label} "
            "— your first real engagement signal."
        )
    return f"Your first visitor engagement on {label} has been detected."


def _text_early_drop_off(label: str, m: dict) -> str:
    dwell = _fmt_float(m.get("avg_dwell_24h"), decimals=0)
    scroll = _fmt_float(m.get("avg_scroll_24h"), decimals=0)
    if dwell != "an average" and scroll != "an average":
        return (
            f"Early visitors to {label} are leaving quickly "
            f"({dwell}s dwell, {scroll}% scroll) "
            "— the above-the-fold content may need attention."
        )
    return f"Early visitors to {label} are leaving before engaging deeply."


def _text_single_product_focus(label: str, m: dict) -> str:
    return (
        f"All recent visitor activity is concentrated on {label} "
        "— this is your most interesting product right now."
    )


# ---------------------------------------------------------------------------
# Registry + dispatcher
# ---------------------------------------------------------------------------

_SIGNAL_TEXT_RENDERERS = {
    # Traffic
    "DEAD_TRAFFIC":               _text_dead_traffic,
    "HIGH_TRAFFIC_NO_CART":       _text_high_traffic_no_cart,
    "LOW_CONVERSION_ATTENTION":   _text_low_conversion_attention,
    # Engagement
    "HIGH_ENGAGEMENT_NO_ACTION":  _text_high_engagement_no_action,
    "SCROLL_HIGH_NO_CLICK":       _text_scroll_high_no_click,
    # Return-visitor
    "HIGH_RETURN_LOW_CONVERSION": _text_high_return_low_conversion,
    "RETURN_VISITOR_INTEREST":    _text_return_visitor_interest,
    # Independent
    "TRAFFIC_SPIKE":              _text_traffic_spike,
    "MOBILE_CONVERSION_GAP":      _text_mobile_conversion_gap,
    "CART_RATE_DECLINING":        _text_cart_rate_declining,
    "PAID_TRAFFIC_NOT_CONVERTING": _text_paid_traffic_not_converting,
    "DEVICE_PURCHASE_GAP":        _text_device_purchase_gap,
    "SOURCE_REVENUE_GAP":         _text_source_revenue_gap,
    "TIME_WINDOW_MISALIGNMENT":   _text_time_window_misalignment,
    "LANDING_PAGE_FAILURE":       _text_landing_page_failure,
    # Store-level strategic
    "REVENUE_CONCENTRATION":      _text_revenue_concentration,
    "STORE_MOBILE_GAP":           _text_store_mobile_gap,
    "STORE_PAID_GAP":             _text_store_paid_gap,
    # Classify-opportunity
    "PRICE_DROP_OR_LOW_STOCK_NUDGE": _text_price_drop_or_low_stock_nudge,
    "WISHLIST_PROMPT_TEST":       _text_wishlist_prompt_test,
    "FRICTION_OR_PRICE_SENSITIVITY": _text_friction_or_price_sensitivity,
    "HIGH_INTEREST_PRODUCT":      _text_high_interest_product,
    "NO_ACTION":                  _text_no_action,
    # Early (low-confidence)
    "EARLY_BROWSING_NO_CART":     _text_early_browsing_no_cart,
    "FIRST_VISITOR_ENGAGEMENT":   _text_first_visitor_engagement,
    "EARLY_DROP_OFF":             _text_early_drop_off,
    "SINGLE_PRODUCT_FOCUS":       _text_single_product_focus,
}


def humanize_signal(
    signal_type: str,
    product_label: str,
    metrics: dict | None = None,
) -> str:
    """
    Return a plain-English sentence describing the signal for the merchant.

    Parameters
    ----------
    signal_type   : one of the signal type strings defined in this module
    product_label : human-readable product name or URL slug
    metrics       : optional dict of product_metrics values
                    (views_1h, views_24h, unique_visitors_24h,
                     cart_conversions_24h, return_visitor_count_7d,
                     avg_dwell_24h, avg_scroll_24h, spike_ratio)
                    Any key may be absent — all templates degrade safely.

    Returns a complete English sentence, never empty.
    """
    m = metrics or {}
    label = product_label or "this product"

    renderer = _SIGNAL_TEXT_RENDERERS.get(signal_type)
    if renderer is not None:
        return renderer(label, m)

    # Unknown signal type: degrade gracefully via title-case fallback.
    readable = signal_type.replace("_", " ").title() if signal_type else "A signal"
    return f"{readable} detected for {label}."


# ---------------------------------------------------------------------------
# humanize_action
# ---------------------------------------------------------------------------

_ACTION_MAP: dict[str, str] = {
    # Traffic signals
    "DEAD_TRAFFIC": (
        "Audit the page load speed, hero image, and above-the-fold content "
        "— visitors are leaving before they see the product."
    ),
    "HIGH_TRAFFIC_NO_CART": (
        "Check that your add-to-cart button is visible and your product "
        "images and page load quickly."
    ),
    "LOW_CONVERSION_ATTENTION": (
        "Review your price, product photos, or description — something is "
        "stopping visitors from buying."
    ),
    # Engagement signals
    "HIGH_ENGAGEMENT_NO_ACTION": (
        "Add a sticky add-to-cart button or a time-sensitive offer — "
        "visitors are reading deeply but need a clearer prompt to act."
    ),
    "SCROLL_HIGH_NO_CLICK": (
        "Place your call-to-action higher on the page or add an inline "
        "buy button where visitors stop scrolling."
    ),
    # Return-visitor signals
    "HIGH_RETURN_LOW_CONVERSION": (
        "Add a returning-visitor discount, low-stock badge, or saved-cart "
        "reminder to convert repeat visitors who are clearly interested."
    ),
    "RETURN_VISITOR_INTEREST": (
        "Add urgency signals such as a low-stock badge or a returning-visitor "
        "discount to convert repeat interest."
    ),
    # Independent
    "TRAFFIC_SPIKE": (
        "Feature this product prominently while traffic is high — consider a "
        "homepage banner or a limited promotion."
    ),
    # Device segmentation
    "MOBILE_CONVERSION_GAP": (
        "Check the product page layout on the underperforming device — "
        "images, CTA visibility, and page speed are the usual culprits."
    ),
    # Temporal trend
    "CART_RATE_DECLINING": (
        "Investigate what changed recently — new competitors, price increase, "
        "page edits, or broken checkout elements could explain the decline."
    ),
    # Source quality
    "PAID_TRAFFIC_NOT_CONVERTING": (
        "Review your ad targeting and landing page alignment — the traffic "
        "you're paying for may not match this product's audience."
    ),
    # Purchase attribution
    "DEVICE_PURCHASE_GAP": (
        "Test the full checkout flow on the underperforming device — "
        "payment methods, form layout, and page speed are the usual culprits."
    ),
    "SOURCE_REVENUE_GAP": (
        "Your paid traffic views but doesn't buy — review ad audience targeting, "
        "landing page alignment, and whether the ad promise matches the product page."
    ),
    # Time-of-day
    "TIME_WINDOW_MISALIGNMENT": (
        "Consider shifting promotional activity (email sends, ad scheduling) "
        "to the time window when visitors are most likely to convert."
    ),
    # Session context
    "LANDING_PAGE_FAILURE": (
        "Improve the above-the-fold experience for direct visitors — "
        "add social proof, clear pricing, and a visible CTA immediately visible on load."
    ),
    # Store-level strategic
    "REVENUE_CONCENTRATION": (
        "Diversify your revenue — improve conversion on second-tier products "
        "and cross-link from your top seller to related items."
    ),
    "STORE_MOBILE_GAP": (
        "Test the full mobile checkout flow — enable Apple Pay / Google Pay, "
        "check page speed, and ensure the cart button is sticky on mobile."
    ),
    "STORE_PAID_GAP": (
        "Audit your ad targeting — shift budget toward products with proven "
        "organic conversion, then use retargeting for visitors who already engaged."
    ),
    # Classify-opportunity signals
    "PRICE_DROP_OR_LOW_STOCK_NUDGE": (
        "Add a price drop or low-stock badge to convert high-intent visitors "
        "before they leave."
    ),
    "WISHLIST_PROMPT_TEST": (
        "Make the wishlist button more prominent on this product page to "
        "capture high-interest visitors who are not yet ready to buy."
    ),
    "FRICTION_OR_PRICE_SENSITIVITY": (
        "Review your pricing, money-back guarantee, or checkout CTA copy to "
        "reduce friction for deeply engaged visitors."
    ),
    "HIGH_INTEREST_PRODUCT": (
        "Promote this product in email or paid ads while visitor interest is high."
    ),
    "NO_ACTION": (
        "Monitor this product — no strong signal yet."
    ),
    # Early signals (low confidence — softer language)
    "EARLY_BROWSING_NO_CART": (
        "Monitor for now — if traffic continues without carts, "
        "check your product images and add-to-cart placement."
    ),
    "FIRST_VISITOR_ENGAGEMENT": (
        "A good start — keep driving traffic and watch whether "
        "engagement translates into add-to-carts."
    ),
    "EARLY_DROP_OFF": (
        "Review your hero image and product title — "
        "first impressions may not be landing."
    ),
    "SINGLE_PRODUCT_FOCUS": (
        "This product is attracting all the attention — consider "
        "featuring it more prominently or testing a small promotion."
    ),
}

_DEFAULT_ACTION = "Review this product for opportunities to increase conversions."


def humanize_action(signal_type: str) -> str:
    """
    Return a plain-English action sentence for the given signal type.
    Always returns a non-empty string.  Unknown types return the default.
    """
    return _ACTION_MAP.get(signal_type, _DEFAULT_ACTION)


# ---------------------------------------------------------------------------
# humanize_headline — merchant-friendly short headline per signal type
# ---------------------------------------------------------------------------

_HEADLINE_MAP: dict[str, str] = {
    "DEAD_TRAFFIC": "Visitors are bouncing",
    "HIGH_TRAFFIC_NO_CART": "Traffic but no add-to-carts",
    "LOW_CONVERSION_ATTENTION": "Low conversion rate",
    "HIGH_ENGAGEMENT_NO_ACTION": "Engaged visitors not buying",
    "SCROLL_HIGH_NO_CLICK": "Deep readers not clicking",
    "HIGH_RETURN_LOW_CONVERSION": "Return visitors not converting",
    "RETURN_VISITOR_INTEREST": "Growing return interest",
    "TRAFFIC_SPIKE": "Traffic spike detected",
    "MOBILE_CONVERSION_GAP": "Device conversion gap",
    "CART_RATE_DECLINING": "Cart rate declining",
    "PAID_TRAFFIC_NOT_CONVERTING": "Paid traffic not converting",
    "DEVICE_PURCHASE_GAP": "Device purchase gap",
    "SOURCE_REVENUE_GAP": "Paid traffic, no revenue",
    "TIME_WINDOW_MISALIGNMENT": "Time-of-day mismatch",
    "LANDING_PAGE_FAILURE": "Landing page underperforming",
    "REVENUE_CONCENTRATION": "Revenue concentrated",
    "STORE_MOBILE_GAP": "Store-wide mobile gap",
    "STORE_PAID_GAP": "Paid traffic not converting",
    "PRICE_DROP_OR_LOW_STOCK_NUDGE": "Price/stock opportunity",
    "WISHLIST_PROMPT_TEST": "Wishlist conversion opportunity",
    "FRICTION_OR_PRICE_SENSITIVITY": "Checkout friction detected",
    "HIGH_INTEREST_PRODUCT": "High-interest product",
    "NO_ACTION": "Monitoring",
    # Early signals
    "EARLY_BROWSING_NO_CART": "Browsing but no carts yet",
    "FIRST_VISITOR_ENGAGEMENT": "First engagement detected",
    "EARLY_DROP_OFF": "Early visitors leaving quickly",
    "SINGLE_PRODUCT_FOCUS": "All eyes on one product",
}

_DEFAULT_HEADLINE = "Opportunity detected"


def humanize_headline(signal_type: str) -> str:
    """
    Return a short merchant-friendly headline for the given signal type.
    Always returns a non-empty string.  Unknown types return a generic headline.
    """
    return _HEADLINE_MAP.get(signal_type, _DEFAULT_HEADLINE)
