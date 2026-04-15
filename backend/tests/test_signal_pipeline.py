"""Tests for signal detection pipeline (opportunity_engine.py)."""
from app.core.time_utils import utc_now_naive
from app.services.opportunity_engine import (
    _evaluate_product_signals,
    _evaluate_early_signals,
    EARLY_SIGNAL_TYPES,
)


# ---------------------------------------------------------------------------
# Strong signal detection (views >= 20)
# ---------------------------------------------------------------------------

def test_high_traffic_no_cart_fires():
    """25 views, 0 carts → HIGH_TRAFFIC_NO_CART with high confidence."""
    signals = _evaluate_product_signals(
        product_url="/products/widget",
        views_24h=25, views_1h=3, unique_visitors_24h=15,
        cart_conversions_24h=0, return_visitor_count_7d=0,
        avg_dwell_24h=10.0, avg_scroll_24h=40.0,
        detected_at=utc_now_naive().isoformat(),
    )
    types = [s["signal_type"] for s in signals]
    assert "HIGH_TRAFFIC_NO_CART" in types
    for s in signals:
        assert s.get("signal_confidence") == "high"


def test_high_engagement_no_action_fires():
    """High dwell + high scroll + zero carts → engagement signal."""
    signals = _evaluate_product_signals(
        product_url="/products/gadget",
        views_24h=30, views_1h=2, unique_visitors_24h=20,
        cart_conversions_24h=0, return_visitor_count_7d=0,
        avg_dwell_24h=25.0, avg_scroll_24h=75.0,
        detected_at=utc_now_naive().isoformat(),
    )
    types = [s["signal_type"] for s in signals]
    assert "HIGH_ENGAGEMENT_NO_ACTION" in types


def test_no_signals_when_traffic_healthy():
    """Good traffic + good cart rate → no negative signals."""
    signals = _evaluate_product_signals(
        product_url="/products/bestseller",
        views_24h=50, views_1h=5, unique_visitors_24h=30,
        cart_conversions_24h=8, return_visitor_count_7d=3,
        avg_dwell_24h=20.0, avg_scroll_24h=60.0,
        detected_at=utc_now_naive().isoformat(),
    )
    traffic_signals = [s for s in signals if s["signal_type"] in (
        "DEAD_TRAFFIC", "HIGH_TRAFFIC_NO_CART", "LOW_CONVERSION_ATTENTION"
    )]
    assert traffic_signals == []


def test_all_strong_signals_have_high_confidence():
    """Every signal from _evaluate_product_signals must have confidence=high."""
    signals = _evaluate_product_signals(
        product_url="/products/test",
        views_24h=30, views_1h=2, unique_visitors_24h=20,
        cart_conversions_24h=0, return_visitor_count_7d=6,
        avg_dwell_24h=25.0, avg_scroll_24h=80.0,
        detected_at=utc_now_naive().isoformat(),
    )
    assert len(signals) > 0
    for s in signals:
        assert s["signal_confidence"] == "high", f"{s['signal_type']} missing high confidence"


# ---------------------------------------------------------------------------
# Early signal detection (views < 20)
# ---------------------------------------------------------------------------

def test_early_browsing_no_cart():
    """3 views, 0 carts → EARLY_BROWSING_NO_CART with low confidence."""
    signals = _evaluate_early_signals(
        product_url="/products/new-item",
        views_24h=3, unique_visitors_24h=2,
        cart_conversions_24h=0,
        avg_dwell_24h=12.0, avg_scroll_24h=40.0,
        detected_at=utc_now_naive().isoformat(),
    )
    types = [s["signal_type"] for s in signals]
    assert "EARLY_BROWSING_NO_CART" in types
    for s in signals:
        assert s["signal_confidence"] == "low"
        assert s["signal_strength"] <= 0.25


def test_early_drop_off():
    """2 views, very low dwell → EARLY_DROP_OFF."""
    signals = _evaluate_early_signals(
        product_url="/products/bad-page",
        views_24h=2, unique_visitors_24h=2,
        cart_conversions_24h=0,
        avg_dwell_24h=3.0, avg_scroll_24h=10.0,
        detected_at=utc_now_naive().isoformat(),
    )
    types = [s["signal_type"] for s in signals]
    assert "EARLY_DROP_OFF" in types


def test_first_visitor_engagement():
    """1 view, decent dwell → FIRST_VISITOR_ENGAGEMENT."""
    signals = _evaluate_early_signals(
        product_url="/products/promising",
        views_24h=1, unique_visitors_24h=1,
        cart_conversions_24h=0,
        avg_dwell_24h=15.0, avg_scroll_24h=50.0,
        detected_at=utc_now_naive().isoformat(),
    )
    types = [s["signal_type"] for s in signals]
    assert "FIRST_VISITOR_ENGAGEMENT" in types


def test_early_signals_suppressed_above_threshold():
    """views >= 20 → no early signals produced."""
    signals = _evaluate_early_signals(
        product_url="/products/popular",
        views_24h=25, unique_visitors_24h=15,
        cart_conversions_24h=0,
        avg_dwell_24h=20.0, avg_scroll_24h=70.0,
        detected_at=utc_now_naive().isoformat(),
    )
    assert signals == []


def test_early_signal_types_constant():
    """EARLY_SIGNAL_TYPES set contains exactly the expected types."""
    assert "EARLY_BROWSING_NO_CART" in EARLY_SIGNAL_TYPES
    assert "FIRST_VISITOR_ENGAGEMENT" in EARLY_SIGNAL_TYPES
    assert "EARLY_DROP_OFF" in EARLY_SIGNAL_TYPES
    assert "SINGLE_PRODUCT_FOCUS" in EARLY_SIGNAL_TYPES
    # Strong types must NOT be in early set
    assert "HIGH_TRAFFIC_NO_CART" not in EARLY_SIGNAL_TYPES
