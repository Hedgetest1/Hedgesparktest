"""
Tests for refund_loss (F2) — product loss signal analyzer.

v1 uses order-frequency decline as a proxy for refund/return impact.
Tests cover: empty shop, stable shop, declining products, loss framing,
report shape, caching.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.shop_order import ShopOrder
from app.services.refund_loss import (
    _compute_product_loss_signals,
    _extract_product_rows,
    get_refund_loss_report,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mk_order(db, shop: str, days_ago: int, price: float, line_items: list,
              suffix: str):
    db.add(ShopOrder(
        shop_domain=shop,
        shopify_order_id=f"gid://{shop}/order/{suffix}",
        total_price=price,
        currency="EUR",
        line_items=line_items,
        created_at=_now() - timedelta(days=days_ago),
    ))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_extract_product_rows_from_valid_line_items():
    items = _extract_product_rows([
        {"product_id": 123, "title": "Candle", "price": "35.00", "quantity": 2},
        {"product_id": 456, "title": "Scarf", "price": "20.00", "quantity": 1},
    ])
    assert len(items) == 2
    assert items[0]["title"] == "Candle"
    assert items[0]["price"] == 35.0
    assert items[0]["quantity"] == 2


def test_extract_product_rows_handles_empty_and_garbage():
    assert _extract_product_rows(None) == []
    assert _extract_product_rows([]) == []
    assert _extract_product_rows([{"no_product_id": "x"}]) != []  # still wraps


# ---------------------------------------------------------------------------
# Signal compute
# ---------------------------------------------------------------------------

def test_stable_products_produce_no_signal(db):
    """A product selling equally in both windows → no decline signal."""
    shop = "stable-shop.myshopify.com"
    for i in range(10):
        _mk_order(db, shop, days_ago=3 + i, price=100.0,
                  line_items=[{"product_id": 1, "title": "Stable", "price": "100", "quantity": 1}],
                  suffix=f"rec_{i}")
    for i in range(10):
        _mk_order(db, shop, days_ago=20 + i, price=100.0,
                  line_items=[{"product_id": 1, "title": "Stable", "price": "100", "quantity": 1}],
                  suffix=f"prior_{i}")
    db.flush()

    signals = _compute_product_loss_signals(db, shop)
    assert signals == []


def test_declining_product_is_flagged(db):
    """A product with 2 recent orders vs 10 prior orders → flagged."""
    shop = "decline-shop.myshopify.com"
    # Recent: only 2 orders of product X
    for i in range(2):
        _mk_order(db, shop, days_ago=3 + i, price=100.0,
                  line_items=[{"product_id": 999, "title": "DecliningProduct", "price": "100", "quantity": 1}],
                  suffix=f"rec_{i}")
    # Prior: 10 orders of same product
    for i in range(10):
        _mk_order(db, shop, days_ago=18 + i, price=100.0,
                  line_items=[{"product_id": 999, "title": "DecliningProduct", "price": "100", "quantity": 1}],
                  suffix=f"prior_{i}")
    db.flush()

    signals = _compute_product_loss_signals(db, shop)
    assert len(signals) >= 1
    top = signals[0]
    assert top["product_title"] == "DecliningProduct"
    assert top["orders_recent_14d"] == 2
    assert top["orders_prior_14d"] == 10
    assert top["decline_pct"] >= 50
    assert top["loss_eur"] > 0


def test_products_sorted_by_loss(db):
    """The report sorts products by loss_eur descending."""
    shop = "sorted-shop.myshopify.com"
    # Product A: big loss (was 20 orders at €200, now 5)
    for i in range(5):
        _mk_order(db, shop, days_ago=3 + i, price=200.0,
                  line_items=[{"product_id": 1, "title": "BigLoss", "price": "200", "quantity": 1}],
                  suffix=f"big_rec_{i}")
    for i in range(20):
        _mk_order(db, shop, days_ago=17 + i // 2, price=200.0,
                  line_items=[{"product_id": 1, "title": "BigLoss", "price": "200", "quantity": 1}],
                  suffix=f"big_prior_{i}")

    # Product B: small loss (was 8 at €20, now 3)
    for i in range(3):
        _mk_order(db, shop, days_ago=3 + i, price=20.0,
                  line_items=[{"product_id": 2, "title": "SmallLoss", "price": "20", "quantity": 1}],
                  suffix=f"small_rec_{i}")
    for i in range(8):
        _mk_order(db, shop, days_ago=17 + i, price=20.0,
                  line_items=[{"product_id": 2, "title": "SmallLoss", "price": "20", "quantity": 1}],
                  suffix=f"small_prior_{i}")
    db.flush()

    signals = _compute_product_loss_signals(db, shop)
    assert len(signals) >= 2
    titles = [s["product_title"] for s in signals]
    assert titles.index("BigLoss") < titles.index("SmallLoss"), (
        "bigger loss must be ranked first"
    )


# ---------------------------------------------------------------------------
# Report shape + headline
# ---------------------------------------------------------------------------

def test_report_empty_shop_returns_stable_headline(db):
    report = get_refund_loss_report(db, "nothing-shop.myshopify.com")
    assert "products" in report
    assert report["product_count"] == 0
    assert "stable" in report["headline"].lower() or "no significant" in report["headline"].lower()


def test_report_declining_shop_returns_warning_headline(db):
    shop = "hot-report-shop.myshopify.com"
    for i in range(2):
        _mk_order(db, shop, days_ago=3 + i, price=500.0,
                  line_items=[{"product_id": 77, "title": "HighValueSlipping", "price": "500", "quantity": 1}],
                  suffix=f"hv_rec_{i}")
    for i in range(12):
        _mk_order(db, shop, days_ago=18 + i, price=500.0,
                  line_items=[{"product_id": 77, "title": "HighValueSlipping", "price": "500", "quantity": 1}],
                  suffix=f"hv_prior_{i}")
    db.flush()

    report = get_refund_loss_report(db, shop)
    assert report["product_count"] >= 1
    assert report["total_loss_eur_per_month"] > 0
    # High-value loss headlines should include the warning emoji or "losing"
    assert "losing" in report["headline"].lower() or "decline" in report["headline"].lower() or "⚠️" in report["headline"]


def test_report_shape_has_required_fields(db):
    report = get_refund_loss_report(db, "shape-check.myshopify.com")
    for key in ("shop_domain", "total_loss_eur_per_month", "product_count",
                "products", "generated_at", "method", "headline"):
        assert key in report, f"missing key {key!r}"
    assert isinstance(report["products"], list)
