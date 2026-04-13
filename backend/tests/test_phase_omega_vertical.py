"""
Phase Ω moat #1 — vertical classifier + vertical-aware benchmarks tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.product import Product
from app.models.shop_order import ShopOrder
from app.services.vertical_classifier import (
    _normalize,
    _score_text,
    classify_shop,
    get_vertical,
    all_verticals,
)
from app.services.vertical_prompt_pack import (
    get_profile,
    baseline_cvr_pct,
    causal_hypotheses_for,
    is_peak_month,
)
from app.services.benchmarks_vertical import (
    get_vertical_benchmark_report,
    get_vertical_pool_stats,
)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_lowercases_and_collapses():
    assert _normalize("Hello   WORLD\n") == "hello world"


def test_normalize_handles_none():
    assert _normalize(None) == ""


def test_score_text_beauty_clear():
    scores = _score_text("rossetto opaco lipstick")
    assert scores.get("beauty", 0) >= 2


def test_score_text_electronics_clear():
    scores = _score_text("wireless headphones charger usb")
    assert scores.get("electronics", 0) >= 3


def test_score_text_empty_returns_empty():
    assert _score_text("") == {}


def test_all_verticals_includes_other():
    assert "other" in all_verticals()
    assert "beauty" in all_verticals()
    assert "electronics" in all_verticals()
    assert len(all_verticals()) == 12  # 11 named + other


# ---------------------------------------------------------------------------
# Prompt pack
# ---------------------------------------------------------------------------


def test_profile_fallback_for_unknown_vertical():
    p = get_profile("nonexistent")
    assert p.vertical == "other"


def test_profile_beauty_baselines_sane():
    assert 1.0 < baseline_cvr_pct("beauty") < 10.0


def test_profile_causal_hypotheses_nonempty():
    assert len(causal_hypotheses_for("beauty")) > 0
    assert len(causal_hypotheses_for("fashion")) > 0


def test_profile_peak_month_beauty_q4():
    assert is_peak_month("beauty", 12) is True
    assert is_peak_month("beauty", 7) is False


# ---------------------------------------------------------------------------
# Classifier — DB integration
# ---------------------------------------------------------------------------


SHOP_BEAUTY = "beauty-test.myshopify.com"
SHOP_ELEC = "elec-test.myshopify.com"
SHOP_EMPTY = "empty-test.myshopify.com"


def _add_products(db, shop: str, titles: list[str]):
    for i, t in enumerate(titles):
        db.add(Product(
            shopify_product_id=f"gid://{shop}/p/{i}",
            title=t,
            price=10.0,
            currency="EUR",
            shop_domain=shop,
        ))
    db.flush()


def test_classify_beauty_shop_force(db):
    _add_products(db, SHOP_BEAUTY, [
        "Mascara Volume Black",
        "Lipstick Rosso Matte",
        "Crema Idratante Viso",
        "Serum Vitamin C",
        "Foundation Light Beige",
    ])
    c = classify_shop(db, SHOP_BEAUTY, force=True)
    assert c.vertical == "beauty"
    assert c.confidence > 0.5
    assert c.sample_size == 5


def test_classify_electronics_shop_force(db):
    _add_products(db, SHOP_ELEC, [
        "Wireless Headphones Pro",
        "USB-C Charger 65W",
        "HDMI Cable 2m",
        "Bluetooth Speaker",
        "4K Monitor 27-inch",
    ])
    c = classify_shop(db, SHOP_ELEC, force=True)
    assert c.vertical == "electronics"
    assert c.confidence > 0.5


def test_classify_empty_shop_returns_other(db):
    c = classify_shop(db, SHOP_EMPTY, force=True)
    assert c.vertical == "other"
    assert c.confidence == 0.0


def test_get_vertical_helper(db):
    _add_products(db, "ven-test.myshopify.com", [
        "Coffee Beans Arabica",
        "Italian Espresso Blend",
        "Wine Bottle Red",
        "Olive Oil Extra Virgin",
    ])
    assert get_vertical(db, "ven-test.myshopify.com") == "food_beverage"


# ---------------------------------------------------------------------------
# Vertical-aware benchmarks
# ---------------------------------------------------------------------------


def _plant_orders(db, shop: str, count: int, price: float):
    for i in range(count):
        db.add(ShopOrder(
            shop_domain=shop,
            shopify_order_id=f"gid://{shop}/o/bv_{i}",
            total_price=price,
            currency="EUR",
            line_items=[],
            created_at=_now() - timedelta(days=i % 25, hours=i),
        ))
    db.flush()


def test_vertical_benchmark_insufficient_peers_falls_back(db):
    """With only one shop in the pool, the vertical-band bucket has 0 peers."""
    _add_products(db, "lone-beauty.myshopify.com", ["Lipstick Red", "Mascara"])
    _plant_orders(db, "lone-beauty.myshopify.com", 10, 35.0)

    report = get_vertical_benchmark_report(db, "lone-beauty.myshopify.com")
    assert report["shop_domain"] == "lone-beauty.myshopify.com"
    # Without enough peers, scope is "insufficient" and fallback baselines surface
    assert report.get("scope") in ("insufficient", "band_only", "vertical_only", "vertical_band")


def test_vertical_pool_stats_shape(db):
    _add_products(db, "pool-a.myshopify.com", ["Lipstick"])
    _plant_orders(db, "pool-a.myshopify.com", 6, 20.0)
    stats = get_vertical_pool_stats(db)
    assert "buckets" in stats
    assert "k_floor" in stats
    assert stats["k_floor"] == 8
    assert isinstance(stats["buckets"], dict)


def test_vertical_benchmark_no_data_for_shop(db):
    """Shop with no orders gets an explicit insufficient_shop_data note."""
    report = get_vertical_benchmark_report(db, "ghost.myshopify.com")
    assert report["note"] == "insufficient_shop_data"
    assert report["metrics"] == {}
