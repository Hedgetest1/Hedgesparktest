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
# humanize_signal
# ---------------------------------------------------------------------------

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

    # ------------------------------------------------------------------ #
    # Traffic signals                                                      #
    # ------------------------------------------------------------------ #

    if signal_type == "DEAD_TRAFFIC":
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
        return (
            f"{label} has traffic today but visitors are bouncing almost immediately."
        )

    if signal_type == "HIGH_TRAFFIC_NO_CART":
        views = _fmt_int(m.get("views_24h"))
        unique = _fmt_int(m.get("unique_visitors_24h"))
        if views != "some" and unique != "some":
            return (
                f"{label} had {views} views from {unique} visitors today "
                "— but no one added it to cart."
            )
        return f"{label} is getting traffic today but no one added it to cart."

    if signal_type == "LOW_CONVERSION_ATTENTION":
        views = _fmt_int(m.get("views_24h"))
        cart = _fmt_int(m.get("cart_conversions_24h"), fallback="very few")
        rate = _fmt_rate(m.get("cart_conversions_24h"), m.get("views_24h"))
        if views != "some":
            return (
                f"{label} had {views} views today but only {cart} moved toward "
                f"checkout — a {rate} conversion rate."
            )
        return f"{label} has steady traffic but a very low conversion rate."

    # ------------------------------------------------------------------ #
    # Engagement signals                                                   #
    # ------------------------------------------------------------------ #

    if signal_type == "HIGH_ENGAGEMENT_NO_ACTION":
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

    if signal_type == "SCROLL_HIGH_NO_CLICK":
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

    # ------------------------------------------------------------------ #
    # Return-visitor signals                                               #
    # ------------------------------------------------------------------ #

    if signal_type == "HIGH_RETURN_LOW_CONVERSION":
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

    if signal_type == "RETURN_VISITOR_INTEREST":
        count = _fmt_int(m.get("return_visitor_count_7d"))
        if count != "some":
            return (
                f"{label} keeps pulling visitors back "
                f"— {count} people returned on multiple days this week."
            )
        return f"{label} is building repeat visitor interest this week."

    # ------------------------------------------------------------------ #
    # Traffic spike (independent)                                          #
    # ------------------------------------------------------------------ #

    if signal_type == "TRAFFIC_SPIKE":
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

    # ------------------------------------------------------------------ #
    # Classify-opportunity signals                                         #
    # ------------------------------------------------------------------ #

    if signal_type == "PRICE_DROP_OR_LOW_STOCK_NUDGE":
        return (
            f"{label} has high-intent visitors showing strong commitment signals "
            "— a price nudge or low-stock badge could convert them."
        )

    if signal_type == "WISHLIST_PROMPT_TEST":
        return (
            f"{label} is attracting high interest but visitors aren't committing "
            "— a more prominent wishlist button could capture intent."
        )

    if signal_type == "FRICTION_OR_PRICE_SENSITIVITY":
        return (
            f"Visitors explore {label} deeply but don't buy "
            "— something in the price, trust, or CTA is creating friction."
        )

    if signal_type == "HIGH_INTEREST_PRODUCT":
        return f"{label} is consistently attracting high-intent visitors this week."

    if signal_type == "NO_ACTION":
        return f"{label} is being tracked — no strong signal detected yet."

    # Unknown signal type: degrade gracefully
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
}

_DEFAULT_ACTION = "Review this product for opportunities to increase conversions."


def humanize_action(signal_type: str) -> str:
    """
    Return a plain-English action sentence for the given signal type.
    Always returns a non-empty string.  Unknown types return the default.
    """
    return _ACTION_MAP.get(signal_type, _DEFAULT_ACTION)
