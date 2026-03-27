from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class ProductMetricsRow(BaseModel):
    """
    Response schema for a single product row in GET /products/metrics.

    Raw fields are sourced directly from the product_metrics table.
    Computed fields are derived server-side and are always present —
    None only when the inputs required to compute them are zero or NULL
    (e.g. cart_abandonment_rate is None when views_24h == 0 because the
    rate is undefined, not zero).
    """

    # ------------------------------------------------------------------ #
    # Raw fields from product_metrics                                      #
    # ------------------------------------------------------------------ #
    product_url: str

    views_24h: int
    views_7d: int
    unique_visitors_24h: int
    unique_visitors_7d: int
    return_visitor_count_7d: int
    cart_conversions_24h: int
    cart_conversions_7d: int = 0

    # NULL when no dwell/scroll events have been recorded in the 24h window.
    avg_dwell_24h: float | None
    avg_scroll_24h: float | None

    # Device segmentation (24h window)
    views_mobile: int = 0
    views_desktop: int = 0
    carts_mobile: int = 0
    carts_desktop: int = 0

    # Source segmentation (24h window)
    views_paid: int = 0
    views_organic: int = 0
    views_direct: int = 0
    carts_paid: int = 0
    carts_organic: int = 0
    carts_direct: int = 0

    # Purchase attribution
    purchases_24h: int = 0
    purchases_7d: int = 0
    revenue_24h: float = 0
    purchases_mobile: int = 0
    purchases_desktop: int = 0
    purchases_paid: int = 0
    purchases_organic: int = 0
    purchases_direct: int = 0

    # Time-of-day intelligence
    peak_hour_views: int = 0
    peak_hour_carts: int = 0
    off_peak_hour_views: int = 0
    off_peak_hour_carts: int = 0

    # Session context
    landing_views_24h: int = 0
    browsing_views_24h: int = 0
    landing_carts_24h: int = 0
    browsing_carts_24h: int = 0

    # ------------------------------------------------------------------ #
    # Computed fields                                                      #
    # ------------------------------------------------------------------ #

    # (views_24h - cart_conversions_24h) / views_24h
    # None when views_24h == 0 (rate is undefined).
    # Range: 0.0 – 1.0.
    cart_abandonment_rate: float | None

    # return_visitor_count_7d / unique_visitors_7d
    # None when unique_visitors_7d == 0.
    # Range: 0.0 – 1.0.
    return_visitor_rate: float | None

    # (avg_dwell_24h / 60) * 0.5 + (avg_scroll_24h / 100) * 0.5
    # None when both avg_dwell_24h and avg_scroll_24h are NULL.
    # When only one is NULL the missing component is treated as 0.
    # Range: 0.0 – 1.0.
    engagement_score: float | None

    # cart_conversions_24h / views_24h — None when views_24h == 0.
    cart_rate_24h: float | None = None

    # cart_conversions_7d / views_7d — None when views_7d == 0.
    cart_rate_7d: float | None = None

    # Trend: "improving", "declining", or "stable" based on cart_rate_24h vs cart_rate_7d.
    # None when either rate is undefined.
    cart_rate_trend: str | None = None

    # "peak" or "off_peak" — which time block converts better. None if no data.
    peak_conversion_label: str | None = None

    # Landing page cart rate vs browsing cart rate. None if no landing data.
    landing_cart_rate: float | None = None
    browsing_cart_rate: float | None = None

    model_config = {"from_attributes": True}


class ProductMetricsResponse(BaseModel):
    """Top-level response envelope for GET /products/metrics."""

    shop_domain: str
    count: int
    products: list[ProductMetricsRow]
